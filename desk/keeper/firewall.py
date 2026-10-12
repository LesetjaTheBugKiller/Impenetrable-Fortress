"""Windows Firewall rules for inbound Remote Desktop.

A block rule outranks an allow rule. Outside the window the block rules are on.
Inside the window they are off, and the allow rules are what open the port.
There is one allow and one block rule per protocol (TCP and UDP).

Allow rules cover only this run's ports: the registry listener port (when it can be
read and passes the port check) and RDP_PORT (when set). If the listener port cannot
be read, 3389 is allowed instead.

Block rules never shrink on their own: 3389, every port the WeekendDesk rules already
cover, the listener port and RDP_PORT. A changed PortNumber only takes effect after
Remote Desktop restarts, so the old port stays blocked too. The block list is capped
at MAX_BLOCK_PORTS; past that the keeper stops without changing any rule.
`py -3 -m desk.keeper reset-ports` (inside the open window) shrinks both lists back
to this run's ports plus 3389.

On a cap or port-check error the keeper exits 1. Outside the window (or when the
window state cannot be worked out) it fails closed: fail_closed() switches the
existing block rules on and the allow rules off, ports unchanged. Inside the window
it leaves every rule as it is and the next run retries.

The first version's TCP 3389 rules (WeekendDeskAllow, WeekendDeskLock) are not
deleted here. While WeekendDeskLock exists, its enable= follows the block state.
The Remote Desktop service is started at install and is not restarted later,
so a service that has stopped can still refuse the port.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from desk.shared.errors import DeskError

Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]

ALLOW = "WeekendDeskAllow"
BLOCK = "WeekendDeskLock"
# Single-protocol TCP 3389 rules written by the first version. Delete them by hand once the new rules work.
LEGACY_RULES = (ALLOW, BLOCK)
PROTOCOLS = ("TCP", "UDP")
DEFAULT_PORT = 3389
RDP_KEY = r"HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server"
RDP_LISTENER_KEY = r"SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp"
MAX_BLOCK_PORTS = 4
# Ports RDP_PORT and the listener port may not be, unless RDP_PORT_FORCE=1.
# 80 and 8080 stand in for the server port, which has no key in desk.env.
RESERVED_PORTS = {
    80: "HTTP / server",
    135: "RPC endpoint mapper",
    139: "NetBIOS session",
    445: "SMB file sharing",
    5985: "WinRM HTTP",
    5986: "WinRM HTTPS",
    8080: "server",
}
# A port line in `netsh advfirewall firewall show rule` in any language: a key with
# "port" in it (LocalPort, Lokaler Port, Port local, or Russian "port"), then a list of numbers.
_PORT_LINE = re.compile(r"^[^:\n]*(?:port|\u043f\u043e\u0440\u0442)[^:\n]*:[ \t]*([0-9][0-9, \t-]*?)[ \t]*$", re.MULTILINE | re.IGNORECASE)


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def rule_name(base: str, protocol: str) -> str:
    return f"{base}-{protocol}"


def all_rule_names() -> tuple[str, ...]:
    return tuple(rule_name(base, protocol) for base in (BLOCK, ALLOW) for protocol in PROTOCOLS) + LEGACY_RULES


def read_listener_port() -> int:
    import winreg

    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, RDP_LISTENER_KEY) as key:
        value, _kind = winreg.QueryValueEx(key, "PortNumber")
    return int(value)


def listener_port(read: Callable[[], int] = read_listener_port) -> int | None:
    """The port the Remote Desktop listener is set to, or None when it cannot be read."""
    try:
        port = int(read())
    except Exception:
        return None
    return port if _valid(port) else None


def parse_local_ports(text: str) -> set[int]:
    """Port numbers from `netsh advfirewall firewall show rule` output, in any display language.

    Only lines whose key mentions a port and whose value is a list of numbers count; ranges are skipped.
    RemotePort is Any on WeekendDesk rules, so it never adds a number.
    """
    ports: set[int] = set()
    for match in _PORT_LINE.finditer(text or ""):
        for part in match.group(1).split(","):
            part = part.strip()
            if part.isdigit() and _valid(int(part)):
                ports.add(int(part))
    return ports


def current_rule_ports(run: Runner = run_command, warn: Callable[[str], None] | None = None) -> set[int]:
    """Ports the existing WeekendDesk rules cover now, new and legacy.

    When a rule exists but no port can be read from it, warn: the block list then keeps
    only 3389 and this run's ports.
    """
    ports: set[int] = set()
    unreadable: list[str] = []
    for name in all_rule_names():
        shown = run(show_rule(name))
        if rule_exists(shown):
            found = parse_local_ports(shown.stdout)
            if not found:
                unreadable.append(name)
            ports |= found
    if unreadable and warn is not None:
        warn(
            "WARNING: could not read the ports of firewall rule(s) "
            + ", ".join(unreadable)
            + ". Keeping 3389 and the ports from this run only."
        )
    return ports


def port_refusal(port: int) -> str | None:
    """Why a port cannot be the Remote Desktop port, or None when it can."""
    if not _valid(port):
        return "not a port number (1-65535)"
    if port in RESERVED_PORTS:
        return f"port {port} is used by {RESERVED_PORTS[port]}"
    if port < 1024 and port != DEFAULT_PORT:
        return f"port {port} is below 1024"
    return None


class PortPlanError(DeskError):
    """RDP_PORT refused or block list over the cap. Raised before any rule is touched."""


@dataclass(frozen=True)
class PortPlan:
    block: tuple[int, ...]
    allow: tuple[int, ...]


def plan_ports(
    configured: int | None,
    force: bool = False,
    read: Callable[[], int] | None = None,
    current: Iterable[int] = (),
    warn: Callable[[str], None] | None = None,
    cap: int = MAX_BLOCK_PORTS,
) -> PortPlan:
    """Ports for this run. Raises PortPlanError, before any rule is touched, when RDP_PORT is refused or the cap is hit."""
    say = warn or (lambda _message: None)
    if configured is not None and not force:
        reason = port_refusal(configured)
        if reason:
            raise PortPlanError(
                f"RDP_PORT={configured} refused: {reason}. Fix RDP_PORT in desk.env, or set RDP_PORT_FORCE=1 "
                "if this really is the Remote Desktop port."
            )
    allow: set[int] = set()
    found = listener_port(read or read_listener_port)
    if found is None:
        say(
            "WARNING: could not read the Remote Desktop listener port (PortNumber under "
            f"HKLM\\{RDP_LISTENER_KEY}). Allowing 3389."
        )
        allow.add(DEFAULT_PORT)
    elif not force and port_refusal(found):
        say(f"WARNING: listener port {found} is not opened: {port_refusal(found)}. It stays blocked. "
            "Set RDP_PORT_FORCE=1 if this really is the Remote Desktop port.")
    else:
        allow.add(found)
    if configured is not None and _valid(configured):
        allow.add(configured)
    if not allow:
        say("WARNING: no usable Remote Desktop port this run. Allowing 3389.")
        allow.add(DEFAULT_PORT)
    block = {DEFAULT_PORT} | allow | {port for port in current if _valid(port)}
    if found is not None:
        block.add(found)
    if len(block) > cap:
        raise PortPlanError(
            f"The Remote Desktop block list would cover {len(block)} ports ({port_list(sorted(block))}); "
            f"the limit is {cap}. During the open window run: py -3 -m desk.keeper reset-ports"
        )
    return PortPlan(block=tuple(sorted(block)), allow=tuple(sorted(allow)))


def port_list(ports: Iterable[int]) -> str:
    return ",".join(str(port) for port in ports)


def allow_rule(enabled: str = "yes", protocol: str = "TCP", ports: Iterable[int] = (DEFAULT_PORT,)) -> list[str]:
    return _add_rule(rule_name(ALLOW, protocol), "allow", enabled, protocol, ports)


def block_rule(enabled: str, protocol: str = "TCP", ports: Iterable[int] = (DEFAULT_PORT,)) -> list[str]:
    return _add_rule(rule_name(BLOCK, protocol), "block", enabled, protocol, ports)


def _add_rule(name: str, action: str, enabled: str, protocol: str, ports: Iterable[int]) -> list[str]:
    return [
        "netsh",
        "advfirewall",
        "firewall",
        "add",
        "rule",
        f"name={name}",
        "dir=in",
        f"action={action}",
        f"protocol={protocol}",
        f"localport={port_list(ports)}",
        f"enable={enabled}",
        "profile=any",
    ]


def set_enabled(name: str, enabled: str) -> list[str]:
    return ["netsh", "advfirewall", "firewall", "set", "rule", f"name={name}", "new", f"enable={enabled}"]


def set_rule(name: str, protocol: str, ports: Iterable[int], enabled: str) -> list[str]:
    """Bring an existing rule to the current port list and state in one call."""
    return [
        "netsh",
        "advfirewall",
        "firewall",
        "set",
        "rule",
        f"name={name}",
        "new",
        f"protocol={protocol}",
        f"localport={port_list(ports)}",
        f"enable={enabled}",
    ]


def show_rule(name: str) -> list[str]:
    return ["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"]


PRESENT = "present"
MISSING = "missing"
UNKNOWN = "unknown"


def rule_state(result: subprocess.CompletedProcess[str]) -> str:
    """present / missing / unknown from a `show rule` result.

    The exit code decides: 0 = present. A non-zero exit is "missing" only when netsh also says
    "No rules match" (English); otherwise it is "unknown" (another language, or netsh failed).
    """
    text = f"{result.stdout}\n{result.stderr}"
    hint = "No rules match" in text
    if result.returncode == 0:
        return MISSING if hint else PRESENT
    return MISSING if hint else UNKNOWN


def rule_exists(result: subprocess.CompletedProcess[str]) -> bool:
    return rule_state(result) == PRESENT


def _detail(result: subprocess.CompletedProcess[str]) -> str:
    text = " ".join(f"{result.stderr or ''} {result.stdout or ''}".split())
    return f"exit {result.returncode}" + (f": {text[:200]}" if text else "")


def _command_text(command: list[str]) -> str:
    return " ".join(command)


def _set_or_raise(name: str, command: list[str], run: Runner) -> None:
    result = run(command)
    if result.returncode != 0:
        raise DeskError(f"Could not set firewall rule {name}: `{_command_text(command)}` failed ({_detail(result)}).")


def apply_firewall(
    open_now: bool,
    run: Runner = run_command,
    ports: Iterable[int] = (DEFAULT_PORT,),
    allow_ports: Iterable[int] | None = None,
) -> None:
    """Block rules on `ports` (always with 3389), allow rules on `allow_ports` (default: the same ports).

    Every error names the rule and the netsh command.
    """
    ports = tuple(sorted(set(ports) | {DEFAULT_PORT}))
    allowed = ports if allow_ports is None else tuple(sorted(set(allow_ports)))
    if not allowed:
        raise DeskError("No Remote Desktop port to allow. No allow rule was changed.")
    blocked = "no" if open_now else "yes"
    # Block rules first, so a closing window is never left with only the allow rules updated.
    for protocol in PROTOCOLS:
        name = rule_name(BLOCK, protocol)
        _ensure(name, block_rule(blocked, protocol, ports), run)
        _set_or_raise(name, set_rule(name, protocol, ports, blocked), run)
    for protocol in PROTOCOLS:
        name = rule_name(ALLOW, protocol)
        _ensure(name, allow_rule("yes", protocol, allowed), run)
        _set_or_raise(name, set_rule(name, protocol, allowed, "yes"), run)
    sync_legacy_block(blocked, run)


def sync_legacy_block(blocked: str, run: Runner = run_command) -> None:
    """While the first version's WeekendDeskLock exists, keep it in the same state as the new block rules."""
    if rule_exists(run(show_rule(BLOCK))):
        _set_or_raise(BLOCK, set_enabled(BLOCK, blocked), run)


@dataclass
class FailClosedReport:
    """What fail_closed() did, rule by rule."""

    rules: dict[str, str]  # name -> "enabled" / "disabled" / "missing" / "show failed: ..." / "set failed: ..."

    @property
    def block_confirmed(self) -> list[str]:
        return [name for name, state in self.rules.items() if state == "enabled"]

    @property
    def failed(self) -> list[str]:
        return [name for name, state in self.rules.items() if state.startswith(("show failed", "set failed"))]

    @property
    def new_block_missing(self) -> bool:
        """Both WeekendDeskLock-TCP and -UDP are clearly missing (a fresh VPS)."""
        return all(self.rules.get(rule_name(BLOCK, protocol)) == "missing" for protocol in PROTOCOLS)

    def summary(self) -> str:
        parts = [f"{name}: {state}" for name, state in self.rules.items()]
        text = "; ".join(parts)
        if not self.block_confirmed:
            text = "NO block rule was confirmed enabled - Remote Desktop may be OPEN. " + text
        return text


BLOCK_NAMES = (rule_name(BLOCK, "TCP"), rule_name(BLOCK, "UDP"), BLOCK)
ALLOW_NAMES = (rule_name(ALLOW, "TCP"), rule_name(ALLOW, "UDP"), ALLOW)


def fail_closed(run: Runner = run_command) -> FailClosedReport:
    """Block rules on, allow rules off, ports unchanged. Uses only `set rule ... new enable=`; creates nothing.

    Every rule is tried even when one fails. A rule whose `show` failed (not clearly missing) is still
    set, because a set on a missing rule only fails.
    """
    rules: dict[str, str] = {}
    targets = [(name, "yes") for name in BLOCK_NAMES] + [(name, "no") for name in ALLOW_NAMES]
    for name, enabled in targets:
        try:
            shown = run(show_rule(name))
            state = rule_state(shown)
        except Exception as exc:
            state, shown = UNKNOWN, None
            show_error = f"show failed: {exc}"
        else:
            show_error = f"show failed: {_detail(shown)}"
        if state == MISSING:
            rules[name] = "missing"
            continue
        try:
            result = run(set_enabled(name, enabled))
            ok = result.returncode == 0
            set_error = f"set failed: {_detail(result)}"
        except Exception as exc:
            ok, set_error = False, f"set failed: {exc}"
        if ok:
            rules[name] = "enabled" if enabled == "yes" else "disabled"
        else:
            rules[name] = show_error if state == UNKNOWN else set_error
    return FailClosedReport(rules)


def fresh_block_ports(read: Callable[[], int] | None = None) -> tuple[int, ...]:
    """3389, plus the listener port when it can be read and passes the port check."""
    ports = {DEFAULT_PORT}
    found = listener_port(read or read_listener_port)
    if found is not None and port_refusal(found) is None:
        ports.add(found)
    return tuple(sorted(ports))


def create_block_rules(ports: Iterable[int], run: Runner = run_command) -> dict[str, str]:
    """Fresh VPS: add WeekendDeskLock-TCP/-UDP, enabled. Returns name -> "created" / "add failed: ...". Never raises."""
    ports = tuple(sorted(set(ports) | {DEFAULT_PORT}))
    done: dict[str, str] = {}
    for protocol in PROTOCOLS:
        name = rule_name(BLOCK, protocol)
        try:
            result = run(block_rule("yes", protocol, ports))
            done[name] = "created" if result.returncode == 0 else f"add failed: {_detail(result)}"
        except Exception as exc:
            done[name] = f"add failed: {exc}"
    return done


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


def _valid(port: int) -> bool:
    return 1 <= port <= 65535


def _ensure(name: str, command: list[str], run: Runner) -> None:
    """Add the rule only when netsh clearly says it is missing.

    If `show rule` fails in any other way the rule may exist, and adding would create a second rule of
    the same name, so this is an error instead (outside the window the keeper then fails closed).
    """
    shown = run(show_rule(name))
    state = rule_state(shown)
    if state == PRESENT:
        return
    if state == UNKNOWN:
        raise DeskError(
            f"ALERT: could not check firewall rule {name}: `{_command_text(show_rule(name))}` failed "
            f"({_detail(shown)}). Not adding it, so no duplicate rule is created."
        )
    added = run(command)
    if added.returncode != 0 and not rule_exists(run(show_rule(name))):
        raise DeskError(f"Could not create firewall rule {name}: `{_command_text(command)}` failed ({_detail(added)}).")
