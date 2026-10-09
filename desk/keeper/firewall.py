"""Windows Firewall pair for inbound Remote Desktop.

A block rule outranks an allow rule. Outside the window the block rule is on.
Inside the window it is off, and the allow rule is what opens the port.
The Remote Desktop service is started at install and is not restarted later,
so a service that has stopped can still refuse the port.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]

ALLOW = "WeekendDeskAllow"
BLOCK = "WeekendDeskLock"
RDP_KEY = r"HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server"


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def allow_rule(enabled: str = "yes") -> list[str]:
    return [
        "netsh",
        "advfirewall",
        "firewall",
        "add",
        "rule",
        f"name={ALLOW}",
        "dir=in",
        "action=allow",
        "protocol=TCP",
        "localport=3389",
        f"enable={enabled}",
        "profile=any",
    ]


def block_rule(enabled: str) -> list[str]:
    return [
        "netsh",
        "advfirewall",
        "firewall",
        "add",
        "rule",
        f"name={BLOCK}",
        "dir=in",
        "action=block",
        "protocol=TCP",
        "localport=3389",
        f"enable={enabled}",
        "profile=any",
    ]


def set_enabled(name: str, enabled: str) -> list[str]:
    return ["netsh", "advfirewall", "firewall", "set", "rule", f"name={name}", "new", f"enable={enabled}"]


def show_rule(name: str) -> list[str]:
    return ["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"]


def rule_exists(result: subprocess.CompletedProcess[str]) -> bool:
    text = f"{result.stdout}\n{result.stderr}"
    return result.returncode == 0 and "No rules match" not in text


def apply_firewall(open_now: bool, run: Runner = run_command) -> None:
    from desk.shared.errors import DeskError

    _ensure(ALLOW, "yes", run)
    _ensure(BLOCK, "no" if open_now else "yes", run)
    enabled = "no" if open_now else "yes"
    result = run(set_enabled(BLOCK, enabled))
    if result.returncode != 0:
        raise DeskError("Could not set the Remote Desktop firewall rule.")


def enable_remote_desktop_service(run: Runner = run_command) -> None:
    from desk.shared.errors import DeskError

    registry = run(
        [
            "reg",
            "add",
            RDP_KEY,
            "/v",
            "fDenyTSConnections",
            "/t",
            "REG_DWORD",
            "/d",
            "0",
            "/f",
        ]
    )
    if registry.returncode != 0:
        raise DeskError("Could not enable Remote Desktop.")
    run(["sc.exe", "config", "TermService", "start=", "auto"])
    run(["sc.exe", "start", "TermService"])


def _ensure(name: str, enabled: str, run: Runner) -> None:
    from desk.shared.errors import DeskError

    shown = run(show_rule(name))
    if rule_exists(shown):
        return
    command = allow_rule(enabled) if name == ALLOW else block_rule(enabled)
    added = run(command)
    if added.returncode != 0 and not rule_exists(run(show_rule(name))):
        raise DeskError("Could not create the Remote Desktop firewall rule.")
