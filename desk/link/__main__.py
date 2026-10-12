"""Home-PC command. `link` shows the provider reset link only when the desk has refused."""

from __future__ import annotations

import argparse
import dataclasses
import getpass
import sys
from pathlib import Path

from desk.link.beacon import Beacon
from desk.link.commands import run_link, run_reseal, run_seal
from desk.shared.config import default_config_path, load_config, require_link_settings
from desk.shared.errors import DeskError
from desk.link.mail import newest_links
from desk.link.probe import probe
from desk.shared.schedule import RESEAL_TASK, install_task, is_admin, refuse_unsafe_for_cmd, write_task_script
from desk.link.tle import Tle, default_binary, ensure_binary, wipe


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="link",
        description=(
            "Read the provider reset link during the weekend window, "
            "and only after Remote Desktop actively refuses."
        ),
    )
    parser.add_argument("command", nargs="?", choices=("link", "seal", "reseal", "install"), default="link")
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument(
        "--seal-path",
        type=Path,
        default=None,
        help="Absolute seal file path. Overrides SEAL_PATH. The reseal task pins this at install.",
    )
    parser.add_argument("--force", action="store_true", help="install: allow an elevated PowerShell.")
    args = parser.parse_args(argv)
    try:
        config = with_seal_path(load_config(args.config), args.seal_path)
        if args.command == "install":
            require_link_settings(config)
            return install(config, force=args.force)
        require_link_settings(config)
        if args.command == "seal":
            return seal(config)
        if args.command == "reseal":
            return reseal(config)
        return link(config)
    except DeskError as exc:
        print(exc, file=sys.stderr)
        return 1


def with_seal_path(config, seal_path: Path | None):
    if seal_path is None:
        return config
    if not seal_path.is_absolute():
        raise DeskError("--seal-path must be an absolute path.")
    return dataclasses.replace(config, seal_path=seal_path)


def link(config) -> int:
    binary = default_binary()
    if not binary.is_file():
        raise DeskError("The time lock program is not installed. Run: py -3 -m desk.link install")
    return run_link(config, Beacon(), probe, Tle(binary), newest_links, sys.stdout)


def seal(config) -> int:
    if not sys.stdin.isatty():
        raise DeskError("Type the app password at the prompt. It is not read from a pipe.")
    password = bytearray(getpass.getpass("Dedicated Gmail app password: ").replace(" ", ""), "utf-8")
    try:
        if not password:
            raise DeskError("The app password was empty.")
        return run_seal(config, Beacon(), Tle(ensure_binary()), password, sys.stdout)
    finally:
        wipe(password)


def reseal(config) -> int:
    binary = default_binary()
    if not binary.is_file():
        if sys.stdout.isatty():
            raise DeskError("The time lock program is not installed. Run: py -3 -m desk.link install")
        return 0
    return run_reseal(config, Beacon(), Tle(binary), sys.stdout, sys.stderr)


def reseal_task_args(seal_path: Path) -> str:
    if '"' in str(seal_path):
        raise DeskError(f"A quote in {seal_path} cannot be used in the scheduled task.")
    refuse_unsafe_for_cmd(str(seal_path))
    return f'-m desk.link reseal --seal-path "{seal_path}"'


def install(config, force: bool = False) -> int:
    # Run from a normal (not elevated) PowerShell, signed in as the account that seals.
    # The task runs as that account, so it reads the same seal file as `seal` and `link`.
    if is_admin():
        if not force:
            raise DeskError(
                "Run install from a normal PowerShell, not an elevated one. An elevated install can register "
                "the reseal task for the wrong account or token. Add --force only if you are sure."
            )
        print(
            "WARNING: installing from an elevated PowerShell because of --force. "
            "Check that 'Run As User' in Task Scheduler is the account that seals.",
            file=sys.stderr,
        )
    ensure_binary()
    script = write_task_script("reseal-task.cmd", reseal_task_args(config.seal_path), config.source)
    install_task(RESEAL_TASK, script, as_system=False)
    print(f"Reseal installed for this account. Seal file: {config.seal_path}")
    if not config.seal_path.is_file():
        print("No seal yet. Run: py -3 -m desk.link seal")
    print("At the end of each window it locks the same app password for the next one.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
