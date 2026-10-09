"""Window keeper. A scheduled task runs this every minute as SYSTEM."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from desk.shared.config import default_config_path, load_config
from desk.shared.errors import DeskError
from desk.keeper.firewall import apply_firewall, enable_remote_desktop_service
from desk.shared.schedule import KEEPER_TASK, install_task, is_admin, write_task_script


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="keeper",
        description="Open inbound Remote Desktop during the weekend window and block it the rest of the week.",
    )
    parser.add_argument("command", nargs="?", choices=("run", "install"), default="run")
    parser.add_argument("--config", type=Path, default=default_config_path())
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == "install":
            return install(config)
        return run(config)
    except DeskError as exc:
        print(exc, file=sys.stderr)
        return 1


def run(config) -> int:
    now = datetime.now(config.window.zone)
    open_now = config.window.is_open(now)
    apply_firewall(open_now)
    print("Remote Desktop allowed." if open_now else "Remote Desktop blocked.")
    return 0


def install(config) -> int:
    if not is_admin():
        raise DeskError("Install the keeper from an elevated PowerShell so it can own the firewall and the scheduled task.")
    enable_remote_desktop_service()
    apply_firewall(config.window.is_open(datetime.now(config.window.zone)))
    install_task(KEEPER_TASK, write_task_script("keeper-task.cmd", "-m desk.keeper run", config.source))
    print("Keeper installed. It runs every minute as SYSTEM.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
