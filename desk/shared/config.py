from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from desk.shared.errors import DeskError
from desk.shared.window import Window, load_zone, parse_clock, parse_weekday


@dataclass(frozen=True)
class DeskConfig:
    window: Window
    vps_host: str
    rdp_port: int
    rdp_timeout: float
    gmail_address: str
    provider_from: str
    provider_link_host: str
    seal_path: Path
    source: Path
    rdp_port_set: bool = False
    rdp_port_force: bool = False


def default_config_path() -> Path:
    return Path(__file__).resolve().parent.parent / "desk.env"


def _default_seal_path() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    root = Path(local) if local else Path.home()
    return root / "WeekendDesk" / "seal.json"


def load_config(path: Path) -> DeskConfig:
    if not path.is_file():
        raise DeskError(f"Missing {path}. Copy desk/desk.env.example to desk/desk.env and set it once.")
    values = _parse(path)
    try:
        window = Window(
            zone=load_zone(_required(values, "TIMEZONE")),
            open_day=parse_weekday(_required(values, "OPEN_DAY")),
            open_time=parse_clock(_required(values, "OPEN_TIME")),
            close_day=parse_weekday(_required(values, "CLOSE_DAY")),
            close_time=parse_clock(_required(values, "CLOSE_TIME")),
        )
    except ValueError as exc:
        raise DeskError(str(exc)) from exc
    if window.open_day == window.close_day and window.open_time == window.close_time:
        raise DeskError("The weekend window has no length.")
    seal_path = resolve_seal_path(values.get("SEAL_PATH", ""), path.parent)
    return DeskConfig(
        window=window,
        vps_host=values.get("VPS_HOST", "").strip(),
        rdp_port=_port(values.get("RDP_PORT", "").strip() or "3389"),
        rdp_timeout=_timeout(values.get("RDP_TIMEOUT_SECONDS", "5")),
        gmail_address=values.get("GMAIL_ADDRESS", "").strip(),
        provider_from=values.get("PROVIDER_FROM", "").strip(),
        provider_link_host=values.get("PROVIDER_LINK_HOST", "").strip(),
        seal_path=seal_path,
        source=path,
        rdp_port_set=bool(values.get("RDP_PORT", "").strip()),
        rdp_port_force=values.get("RDP_PORT_FORCE", "").strip() == "1",
    )


def resolve_seal_path(value: str, base: Path) -> Path:
    """SEAL_PATH as an absolute path.

    Empty means %LOCALAPPDATA%\\WeekendDesk\\seal.json of the account running the command.
    %VARIABLES% and ~ are expanded. A relative path is taken from the desk.env folder.
    """
    text = value.strip()
    if not text:
        return _default_seal_path().resolve()
    seal_path = Path(os.path.expanduser(os.path.expandvars(text)))
    if not seal_path.is_absolute():
        seal_path = base / seal_path
    return seal_path.resolve()


def require_link_settings(config: DeskConfig) -> None:
    if not config.vps_host or any(character.isspace() for character in config.vps_host):
        raise DeskError("Set VPS_HOST in desk.env.")
    if "@" not in config.gmail_address or any(character.isspace() for character in config.gmail_address):
        raise DeskError("Set GMAIL_ADDRESS in desk.env.")
    if "@" not in config.provider_from or any(character.isspace() for character in config.provider_from):
        raise DeskError("Set PROVIDER_FROM in desk.env to the address the reset mail comes from.")


def _parse(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise DeskError(f"{path}:{lineno}: expected KEY=VALUE.")
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _required(values: dict[str, str], key: str) -> str:
    value = values.get(key, "").strip()
    if not value:
        raise DeskError(f"Set {key} in desk.env.")
    return value


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise DeskError("RDP_PORT must be a number.") from exc
    if not 1 <= port <= 65535:
        raise DeskError("RDP_PORT must be from 1 to 65535.")
    return port


def _timeout(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError as exc:
        raise DeskError("RDP_TIMEOUT_SECONDS must be a number.") from exc
    if timeout <= 0:
        raise DeskError("RDP_TIMEOUT_SECONDS must be greater than zero.")
    return timeout
