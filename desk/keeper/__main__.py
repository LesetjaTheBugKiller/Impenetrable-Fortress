"""Window keeper. A scheduled task runs this every minute as SYSTEM, with --scheduled."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from desk.shared.config import default_config_path, load_config
from desk.shared.errors import DeskError
from desk.keeper import firewall, rdplog
from desk.keeper.firewall import (
    PortPlanError,
    apply_firewall,
    create_block_rules,
    current_rule_ports,
    enable_remote_desktop_service,
    fail_closed,
    fresh_block_ports,
    plan_ports,
)
from desk.shared.schedule import KEEPER_TASK, install_task, is_admin, write_task_script

EVENT_ID = "900"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="keeper",
        description="Open inbound Remote Desktop during the weekend window and block it the rest of the week.",
    )
    parser.add_argument(
        "command", nargs="?", choices=("run", "install", "rdp-log", "reset-ports"), default="run"
    )
    parser.add_argument("action", nargs="?", choices=("verify", "collect", "head", "init"), default=None)
    parser.add_argument("--anchor", action="append", default=[], help="seq:mac from an earlier rdp-log head")
    parser.add_argument("--force", action="store_true", help="install, reset-ports: also outside the open window.")
    parser.add_argument("--scheduled", action="store_true", help="run: set by the scheduled task.")
    parser.add_argument("--config", type=Path, default=default_config_path())
    args = parser.parse_args(argv)
    if args.command == "run":
        return run_safely(args.config, scheduled=args.scheduled)
    try:
        config = load_config(args.config)
        if args.command == "install":
            return install(config, force=args.force)
        if args.command == "rdp-log":
            settings = rdplog.load_settings(config.source)
            return rdplog.run_action(args.action or "verify", settings, args.anchor, sys.stdout)
        return reset_ports(config, force=args.force)
    except DeskError as exc:
        print(exc, file=sys.stderr)
        return 1


def _warn(message: str) -> None:
    print(message, file=sys.stderr)


def _netsh():
    """The netsh runner, looked up at call time (tests replace firewall.run_command)."""
    return firewall.run_command


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- WeekendDesk folder, status file, Event Log ----------

SYSTEM_SID = "S-1-5-18"
ADMINS_SID = "S-1-5-32-544"
# Users, Authenticated Users, Everyone, CREATOR OWNER: explicit grants removed from the folder.
OTHER_SIDS = ("S-1-5-32-545", "S-1-5-11", "S-1-1-0", "S-1-3-0")


def run_tool(command: list[str]) -> subprocess.CompletedProcess[str]:
    """icacls / powershell. Tests replace this."""
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def desk_folder() -> Path:
    return Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "WeekendDesk"


def status_path() -> Path:
    """Diagnostics only. Never read for a decision."""
    return desk_folder() / "keeper-status.json"


def secure_folder() -> None:
    """WeekendDesk folder: SYSTEM and Administrators full control only, inheritance removed. Raises DeskError."""
    folder = desk_folder()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        command = ["icacls", str(folder), "/inheritance:r", "/grant:r",
                   f"*{SYSTEM_SID}:(OI)(CI)F", f"*{ADMINS_SID}:(OI)(CI)F"]
        command += ["/remove:g"] + [f"*{sid}" for sid in OTHER_SIDS]
        result = run_tool(command)
    except Exception as exc:
        raise DeskError(f"Could not lock down {folder}: {exc}") from exc
    if result.returncode != 0:
        detail = " ".join(f"{result.stdout} {result.stderr}".split())[:200]
        raise DeskError(f"Could not lock down {folder}: icacls exit {result.returncode} {detail}")


def write_status(data: dict) -> None:
    """Best effort, diagnostics only. A failure is reported on stderr and otherwise ignored."""
    path = status_path()
    try:
        secure_folder()
    except DeskError as exc:
        _warn(f"WARNING: {exc}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".part")
        temporary.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)
    except Exception as exc:
        _warn(f"WARNING: could not write {path}: {exc}")


def report_event(message: str) -> None:
    """Best effort: an ERROR in the Windows Application log, source WeekendDesk, ID 900."""
    text = " ".join(message.replace('"', "'").split())[:900]
    try:
        subprocess.run(
            ["eventcreate", "/L", "APPLICATION", "/T", "ERROR", "/SO", "WeekendDesk", "/ID", EVENT_ID, "/D", text],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass


# ---------- run ----------


def port_plan(config, sticky: bool = True):
    """Block and allow ports for this run; see desk/keeper/firewall.py. sticky=False forgets earlier ports."""
    return plan_ports(
        config.rdp_port if config.rdp_port_set else None,
        force=config.rdp_port_force,
        current=current_rule_ports(_netsh(), warn=_warn) if sticky else (),
        warn=_warn,
    )


def window_state(config) -> bool | None:
    """True inside the window, False outside, None when it cannot be worked out."""
    try:
        return bool(config.window.is_open(datetime.now(config.window.zone)))
    except Exception:
        return None


def run_safely(config_path: Path, scheduled: bool = False) -> int:
    """`keeper run`. Any error: exit 1 with an ALERT. Outside the window or unknown: fail closed."""
    try:
        config = load_config(config_path)
    except Exception as exc:
        return failure(f"could not load {config_path}: {exc}", None, scheduled)
    try:
        state = window_state(config)
    except Exception as exc:
        return failure(f"unexpected error before the window check: {exc}", None, scheduled)
    if state is None:
        return failure("could not work out whether the window is open", None, scheduled, config)
    try:
        plan = port_plan(config)
        apply_firewall(state, _netsh(), ports=plan.block, allow_ports=plan.allow)
    except Exception as exc:
        kind = "Remote Desktop ports not updated" if isinstance(exc, PortPlanError) else "firewall update failed"
        return failure(f"{kind}: {exc}", state, scheduled, config)
    print("Remote Desktop allowed." if state else "Remote Desktop blocked.")
    status = {
        "time": _now().isoformat(),
        "ok": True,
        "window": "open" if state else "closed",
        "block_ports": list(plan.block),
        "allow_ports": list(plan.allow),
    }
    write_status(status)
    rdplog.collect_if_enabled(config.source)
    return 0


def failure(error: str, open_now: bool | None, scheduled: bool, config=None) -> int:
    """Exit 1. Inside the window change nothing. Outside or unknown: fail closed, and on a fresh VPS
    (no WeekendDeskLock-TCP/-UDP) create them, but only in the scheduled run."""
    window = "open" if open_now else ("unknown" if open_now is None else "closed")
    _warn(f"ALERT: {error}")
    status = {"time": _now().isoformat(), "ok": False, "error": error, "window": window}
    if open_now:
        action = "Inside the window: allow and block rules left exactly as they are. The next run retries."
        status["action"] = "none (inside the window)"
    else:
        if open_now is None:
            _warn("ALERT: window state unknown. Treating it as closed.")
        report = fail_closed(_netsh())
        status["rules"] = dict(report.rules)
        action = "Outside the window: block rules switched on and allow rules off, ports unchanged. " + report.summary()
        status["action"] = "fail closed"
        if report.new_block_missing:
            if scheduled:
                created = create_block_rules(fresh_block_ports(), _netsh())
                status["created"] = created
                action += " No WeekendDeskLock-TCP/-UDP rules existed; created: " + "; ".join(
                    f"{name}: {state}" for name, state in created.items()
                )
            else:
                action += (
                    " ALERT: no WeekendDeskLock-TCP/-UDP rules exist, so nothing blocks Remote Desktop. "
                    "This interactive run creates none; the scheduled run (--scheduled) will."
                )
    _warn(action)
    status["message"] = action
    write_status(status)
    try:
        report_event(f"WeekendDesk keeper: {error}. {action}")
    except Exception:
        pass
    return 1


def install(config, force: bool = False) -> int:
    if not is_admin():
        raise DeskError("Install the keeper from an elevated PowerShell so it can own the firewall and the scheduled task.")
    open_now = config.window.is_open(datetime.now(config.window.zone))
    if not open_now and not force:
        raise DeskError(
            "The window is closed. Installing now switches the Remote Desktop block on at once, and it is not "
            "verified that an open Remote Desktop session survives that. Install inside the window, or from the "
            "provider's web console, or add --force if you are sure."
        )
    secure_folder()
    enable_remote_desktop_service(_netsh())
    plan = port_plan(config)
    apply_firewall(open_now, _netsh(), ports=plan.block, allow_ports=plan.allow)
    print(f"Remote Desktop ports blocked outside the window: {', '.join(str(port) for port in plan.block)}")
    print(f"Remote Desktop ports allowed inside the window: {', '.join(str(port) for port in plan.allow)}")
    install_task(KEEPER_TASK, write_task_script("keeper-task.cmd", "-m desk.keeper run --scheduled", config.source))
    print("Keeper installed. It runs every minute as SYSTEM.")
    return 0


def reset_ports(config, force: bool = False) -> int:
    """Shrink block and allow rules to this run's ports plus 3389. Inside the open window only, unless --force."""
    if not is_admin():
        raise DeskError("Run reset-ports from an elevated PowerShell so it can change the firewall.")
    open_now = config.window.is_open(datetime.now(config.window.zone))
    if not open_now and not force:
        raise DeskError(
            "reset-ports runs only inside the open window, so a port that is still in use is never unblocked "
            "during the week. Add --force only if you are sure."
        )
    plan = port_plan(config, sticky=False)
    apply_firewall(open_now, _netsh(), ports=plan.block, allow_ports=plan.allow)
    print(f"Ports reset. Blocked outside the window: {', '.join(str(port) for port in plan.block)}")
    print(f"Allowed inside the window: {', '.join(str(port) for port in plan.allow)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
