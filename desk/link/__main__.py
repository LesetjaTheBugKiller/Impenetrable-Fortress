"""Home-PC command. `link` shows the provider reset link only when the desk has refused."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from desk.link.beacon import Beacon
from desk.link.commands import run_link, run_reseal, run_seal
from desk.shared.config import default_config_path, load_config, require_link_settings
from desk.shared.errors import DeskError
from desk.link.mail import newest_links
from desk.link.probe import probe
from desk.shared.schedule import RESEAL_TASK, install_task, is_admin, write_task_script
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
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == "install":
            require_link_settings(config)
            return install(config)
        require_link_settings(config)
        if args.command == "seal":
            return seal(config)
        if args.command == "reseal":
            return reseal(config)
        return link(config)
    except DeskError as exc:
        print(exc, file=sys.stderr)
        return 1


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
    return run_reseal(config, Beacon(), Tle(binary), sys.stdout)


def install(config) -> int:
    if not is_admin():
        raise DeskError("Install the reseal task from an elevated PowerShell.")
    ensure_binary()
    install_task(RESEAL_TASK, write_task_script("reseal-task.cmd", "-m desk.link reseal", config.source))
    print("Reseal installed. At the end of each window it locks the same app password for the next one.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
