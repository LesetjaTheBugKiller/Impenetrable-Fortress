"""Tamper-evident log of Remote Desktop logons on the VPS.

Each line of the log is one JSON entry. Every entry carries an HMAC-SHA256 made with a
machine-only key, and the MAC of the entry before it ("prev"). Editing, deleting, or
reordering a line breaks a MAC or a link, and `verify` names the line.

Limits: anyone who can read the key (SYSTEM or an administrator on the VPS) can rewrite the
whole chain with fresh MACs. Copy the chain head off the machine (`rdp-log head`) and pass it
back to `verify --anchor` to catch that. Cutting entries off the end is only caught by an anchor.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from desk.shared.errors import DeskError

GENESIS = "0" * 64
SECURITY = "Security"
REMOTE_CONNECTION = "Microsoft-Windows-TerminalServices-RemoteConnectionManager/Operational"
LOCAL_SESSION = "Microsoft-Windows-TerminalServices-LocalSessionManager/Operational"
# Channel -> {event id: kind}
SOURCES: dict[str, dict[int, str]] = {
    SECURITY: {4624: "logon", 4625: "logon_failed"},
    REMOTE_CONNECTION: {1149: "rdp_authenticated"},
    LOCAL_SESSION: {21: "session_logon", 24: "session_disconnected", 25: "session_reconnected"},
}
# 4624 counts only for Remote Desktop (type 10). Failed RDP logons arrive as type 3 with NLA, or 10 without.
LOGON_TYPES = {4624: {"10"}, 4625: {"3", "10"}}
FIRST_RUN_LOOKBACK_MS = 7 * 24 * 3600 * 1000
EVENT_NS = "{http://schemas.microsoft.com/win/2004/08/events/event}"
FIELDS = {
    "user": ("TargetUserName", "Param1", "User"),
    "domain": ("TargetDomainName", "Param2"),
    "ip": ("IpAddress", "Param3", "Address"),
    "logon_type": ("LogonType",),
    "session": ("SessionID",),
    "status": ("Status", "SubStatus"),
}

Query = Callable[[str, str], str]


@dataclass(frozen=True)
class Settings:
    enabled: bool
    log_path: Path
    key_path: Path


def _program_data() -> Path:
    return Path(os.environ.get("ProgramData") or os.environ.get("PROGRAMDATA") or r"C:\ProgramData") / "WeekendDesk"


def load_settings(config_source: Path) -> Settings:
    from desk.shared.config import _parse

    values = _parse(config_source) if config_source.is_file() else {}

    def path_of(key: str, default: Path) -> Path:
        text = values.get(key, "").strip()
        if not text:
            return default
        path = Path(os.path.expandvars(text))
        return path if path.is_absolute() else (config_source.parent / path).resolve()

    return Settings(
        enabled=values.get("RDPLOG_ENABLED", "").strip().lower() in ("1", "yes", "true", "on"),
        log_path=path_of("RDPLOG_PATH", _program_data() / "rdp-log.jsonl"),
        key_path=path_of("RDPLOG_KEY_PATH", _program_data() / "rdp-log.key"),
    )


# ---------- MACs and the chain ----------


def key_id(key: bytes) -> str:
    return hashlib.sha256(b"weekend-desk rdp-log key id\0" + key).hexdigest()[:16]


def canonical(entry: dict) -> bytes:
    body = {name: value for name, value in entry.items() if name != "mac"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def mac_for(key: bytes, entry: dict) -> str:
    return hmac.new(key, canonical(entry), hashlib.sha256).hexdigest()


def seal_entry(key: bytes, fields: dict, seq: int, prev: str) -> dict:
    entry = dict(fields)
    entry.update({"v": 1, "seq": seq, "prev": prev, "kid": key_id(key)})
    entry["mac"] = mac_for(key, entry)
    return entry


def read_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def tail(path: Path) -> tuple[int, str]:
    """(last seq, last mac) of the file as written, valid or not. (0, GENESIS) when empty."""
    for line in reversed(read_lines(path)):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            return int(entry["seq"]), str(entry["mac"])
        except (ValueError, KeyError, TypeError):
            raise DeskError(f"The last line of {path} is unreadable. Run: rdp-log verify")
    return 0, GENESIS


def append(path: Path, key: bytes, events: list[dict]) -> int:
    if not events:
        return 0
    seq, prev = tail(path)
    lines = []
    for fields in events:
        seq += 1
        entry = seal_entry(key, fields, seq, prev)
        prev = entry["mac"]
        lines.append(json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return len(lines)


@dataclass(frozen=True)
class Problem:
    line: int
    seq: object
    kind: str
    detail: str

    def render(self) -> str:
        return f"  line {self.line:<5} seq {str(self.seq):<6} {self.kind:<12} {self.detail}"


def _describe(entry: dict) -> str:
    who = entry.get("user") or "?"
    if entry.get("domain"):
        who = f"{entry['domain']}\\{who}"
    return f"{entry.get('kind', '?')} {who} from {entry.get('ip') or '?'} at {entry.get('time', '?')}"


def verify(lines: list[str], key: bytes, anchors: list[tuple[int, str]] = ()) -> tuple[int, list[Problem]]:
    """Return (entries checked, problems). No problems means the chain is intact."""
    problems: list[Problem] = []
    expected_kid = key_id(key)
    prev_mac = GENESIS
    prev_seq = 0
    seen: dict[int, str] = {}
    checked = 0
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        checked += 1
        try:
            entry = json.loads(line)
            seq = int(entry["seq"])
            stored = str(entry["mac"])
            link = str(entry["prev"])
        except (ValueError, KeyError, TypeError):
            problems.append(Problem(number, "?", "UNREADABLE", "not a log entry (edited or injected)"))
            continue
        if entry.get("kid") != expected_kid:
            problems.append(Problem(number, seq, "WRONG KEY", f"made with key {entry.get('kid')}, this machine's key is {expected_kid}"))
        elif not hmac.compare_digest(stored, mac_for(key, entry)):
            problems.append(Problem(number, seq, "EDITED", f"MAC does not match the contents: {_describe(entry)}"))
        if seq <= prev_seq:
            problems.append(Problem(number, seq, "OUT OF ORDER", f"comes after seq {prev_seq}"))
        elif seq > prev_seq + 1:
            missing = f"{prev_seq + 1}" if seq == prev_seq + 2 else f"{prev_seq + 1}-{seq - 1}"
            problems.append(Problem(number, seq, "MISSING", f"seq {missing} deleted before this line"))
        if not hmac.compare_digest(link, prev_mac):
            problems.append(Problem(number, seq, "BROKEN LINK", "does not follow the line before it (deleted, inserted, or moved)"))
        seen[seq] = stored
        prev_mac = stored
        prev_seq = max(prev_seq, seq)
    for anchor_seq, anchor_mac in anchors:
        if anchor_seq not in seen:
            problems.append(Problem(0, anchor_seq, "ANCHOR LOST", "anchored entry is gone (log cut short or replaced)"))
        elif not hmac.compare_digest(seen[anchor_seq], anchor_mac):
            problems.append(Problem(0, anchor_seq, "ANCHOR DIFF", "entry differs from the copy kept off the machine (chain rewritten)"))
    return checked, problems


def parse_anchor(text: str) -> tuple[int, str]:
    try:
        seq, mac = text.strip().split(":", 1)
        value = (int(seq), mac.strip().lower())
    except ValueError as exc:
        raise DeskError(f"An anchor looks like 42:3f9a..., not {text!r}.") from exc
    if len(value[1]) != 64 or any(c not in "0123456789abcdef" for c in value[1]):
        raise DeskError(f"An anchor looks like 42:3f9a..., not {text!r}.")
    return value


# ---------- Windows events ----------


def wevtutil_query(channel: str, xpath: str) -> str:
    result = subprocess.run(
        ["wevtutil", "qe", channel, f"/q:{xpath}", "/f:xml"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise DeskError(f"Could not read the {channel} log: {(result.stderr or result.stdout).strip()}")
    return result.stdout


def xpath_for(channel: str, after_record: int) -> str:
    ids = " or ".join(f"EventID={event_id}" for event_id in sorted(SOURCES[channel]))
    if after_record:
        where = f"EventRecordID>{after_record}"
    else:
        where = f"TimeCreated[timediff(@SystemTime) <= {FIRST_RUN_LOOKBACK_MS}]"
    return f"*[System[({ids}) and {where}]]"


def parse_events(channel: str, xml_text: str) -> list[dict]:
    text = xml_text.strip()
    if not text:
        return []
    try:
        root = ElementTree.fromstring(f"<Events>{text}</Events>")
    except ElementTree.ParseError as exc:
        raise DeskError(f"The {channel} log returned unreadable XML.") from exc
    found = []
    for event in root.iter(f"{EVENT_NS}Event"):
        system = event.find(f"{EVENT_NS}System")
        if system is None:
            continue
        try:
            event_id = int(system.findtext(f"{EVENT_NS}EventID", "0"))
            record = int(system.findtext(f"{EVENT_NS}EventRecordID", "0"))
        except ValueError:
            continue
        kind = SOURCES.get(channel, {}).get(event_id)
        if kind is None:
            continue
        created = system.find(f"{EVENT_NS}TimeCreated")
        data = _data(event)
        if event_id in LOGON_TYPES and data.get("LogonType") not in LOGON_TYPES[event_id]:
            continue
        fields = {
            "channel": channel,
            "record": record,
            "event_id": event_id,
            "kind": kind,
            "time": created.get("SystemTime", "") if created is not None else "",
        }
        for name, sources in FIELDS.items():
            value = next((data[source] for source in sources if data.get(source) not in (None, "", "-")), "")
            if value:
                fields[name] = value
        found.append(fields)
    found.sort(key=lambda item: item["record"])
    return found


def _data(event) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in event.iter(f"{EVENT_NS}Data"):
        name = item.get("Name")
        if name:
            values[name] = (item.text or "").strip()
    user_data = event.find(f"{EVENT_NS}UserData")
    if user_data is not None:
        for element in user_data.iter():
            if len(element) == 0 and element.text and element.text.strip():
                values[element.tag.split("}")[-1]] = element.text.strip()
    return values


def cursors(lines: list[str]) -> dict[str, int]:
    """Highest event record already logged, per channel."""
    found: dict[str, int] = {}
    for line in lines:
        try:
            entry = json.loads(line)
            channel, record = str(entry["channel"]), int(entry["record"])
        except (ValueError, KeyError, TypeError):
            continue
        found[channel] = max(found.get(channel, 0), record)
    return found


def collect(path: Path, key: bytes, query: Query = wevtutil_query) -> int:
    """Append every new RDP event. Returns how many entries were written."""
    known = cursors(read_lines(path))
    events: list[dict] = []
    for channel in SOURCES:
        events.extend(parse_events(channel, query(channel, xpath_for(channel, known.get(channel, 0)))))
    events.sort(key=lambda item: (item["time"], item["channel"], item["record"]))
    return append(path, key, events)


# ---------- Key file ----------

CRYPTPROTECT_UI_FORBIDDEN = 0x1
CRYPTPROTECT_LOCAL_MACHINE = 0x4


def dpapi_protect(data: bytes) -> bytes:
    return _dpapi("CryptProtectData", data, CRYPTPROTECT_LOCAL_MACHINE | CRYPTPROTECT_UI_FORBIDDEN)


def dpapi_unprotect(data: bytes) -> bytes:
    return _dpapi("CryptUnprotectData", data, CRYPTPROTECT_UI_FORBIDDEN)


def _dpapi(function: str, data: bytes, flags: int) -> bytes:
    if os.name != "nt":
        raise DeskError("The RDP log key uses Windows DPAPI.")
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(data, len(data))
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    target = Blob()
    call = getattr(ctypes.windll.crypt32, function)
    if not call(ctypes.byref(source), None, None, None, None, flags, ctypes.byref(target)):
        raise DeskError("Windows could not open the RDP log key.")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


class KeyFile:
    def __init__(self, path: Path, protect=dpapi_protect, unprotect=dpapi_unprotect, restrict=None):
        self.path = path
        self.protect = protect
        self.unprotect = unprotect
        self.restrict = restrict or restrict_to_system_and_admins

    def load(self) -> bytes:
        if not self.path.is_file():
            raise DeskError(f"No RDP log key at {self.path}. Run once on the VPS, elevated: py -3 -m desk.keeper rdp-log init")
        try:
            payload = json.loads(self.path.read_text(encoding="ascii"))
            key = self.unprotect(base64.b64decode(payload["blob"]))
        except DeskError:
            raise
        except Exception as exc:
            raise DeskError(f"The RDP log key at {self.path} cannot be read.") from exc
        if len(key) != 32:
            raise DeskError(f"The RDP log key at {self.path} is damaged.")
        return key

    def create(self) -> bytes:
        if self.path.exists():
            raise DeskError(f"An RDP log key already exists at {self.path}. It is never replaced, or the old log stops verifying.")
        key = secrets.token_bytes(32)
        body = json.dumps({"v": 1, "protect": "dpapi-localmachine", "blob": base64.b64encode(self.protect(key)).decode("ascii")})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("x", encoding="ascii") as handle:
            handle.write(body + "\n")
        self.restrict(self.path)
        return key


def restrict_to_system_and_admins(path: Path) -> None:
    if os.name != "nt":
        return
    result = subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", "*S-1-5-18:(F)", "/grant:r", "*S-1-5-32-544:(F)"],
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise DeskError(f"Could not lock down {path}: {(result.stderr or result.stdout).strip()}")


# ---------- Commands ----------


def run_action(action: str, settings: Settings, anchors: list[str], stdout, keys: KeyFile | None = None, query: Query = wevtutil_query) -> int:
    keys = keys or KeyFile(settings.key_path)
    if action == "init":
        keys.create()
        stdout.write(f"RDP log key created at {settings.key_path}. Log: {settings.log_path}\n")
        stdout.write("Set RDPLOG_ENABLED=yes in desk.env so the keeper collects every minute.\n")
        return 0
    key = keys.load()
    if action == "collect":
        written = collect(settings.log_path, key, query)
        stdout.write(f"RDP log: {written} new entr{'y' if written == 1 else 'ies'}.\n")
        return 0
    if action == "head":
        seq, mac = tail(settings.log_path)
        if seq == 0:
            stdout.write("RDP log is empty. Nothing to anchor yet.\n")
            return 0
        stdout.write(f"{seq}:{mac}\n")
        stdout.write("Keep this line off the VPS. Later: rdp-log verify --anchor " + f"{seq}:{mac}\n")
        return 0
    if action == "verify":
        parsed = [parse_anchor(text) for text in anchors]
        checked, problems = verify(read_lines(settings.log_path), key, parsed)
        stdout.write(f"RDP log: {settings.log_path}\n")
        if checked == 0 and not problems:
            stdout.write("Empty log. Nothing to verify.\n")
            return 0
        if not problems:
            anchored = f", {len(parsed)} anchor(s) match" if parsed else ""
            stdout.write(f"OK: {checked} entries, chain intact{anchored}.\n")
            return 0
        for problem in problems:
            stdout.write(problem.render() + "\n")
        if checked and sum(1 for p in problems if p.kind in ("EDITED", "WRONG KEY")) == checked:
            stdout.write("Every entry fails its MAC: wrong key, or the whole file was replaced.\n")
        stdout.write(f"TAMPERED: {len(problems)} problem(s) in {checked} entries.\n")
        return 2
    raise DeskError(f"Unknown rdp-log action {action!r}.")


def collect_if_enabled(config_source: Path, stderr=None) -> None:
    """Called from the keeper's every-minute run. It never stops the firewall work."""
    stderr = stderr or sys.stderr
    try:
        settings = load_settings(config_source)
        if not settings.enabled:
            return
        collect(settings.log_path, KeyFile(settings.key_path).load())
    except Exception as exc:  # the lock matters more than the log
        stderr.write(f"RDP log not collected: {exc}\n")
