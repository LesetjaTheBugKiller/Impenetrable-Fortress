"""Sealed file. The app password is only inside the time-lock ciphertext."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from desk.shared.errors import DeskError

SYNC_MARKERS = ("onedrive", "dropbox", "google drive", "googledrive", "iclouddrive", "icloud")


@dataclass(frozen=True)
class Seal:
    round: int
    window_start: datetime
    armor: bytes
    owner: str


def refuse_synced_folder(path: Path) -> None:
    probe = path if path.exists() else path.parent
    try:
        text = str(probe.resolve()).lower()
    except OSError:
        text = str(path).lower()
    for marker in SYNC_MARKERS:
        if marker in text:
            raise DeskError(
                "The seal has to live in a folder that does not sync. "
                "OneDrive, Dropbox, Google Drive, and iCloud keep old copies, "
                "and an old copy opens once the window starts."
            )


def read_seal(path: Path) -> Seal | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        window_start = datetime.strptime(payload["window_start"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        armor = payload["armor"].encode("ascii")
        return Seal(
            round=int(payload["round"]),
            window_start=window_start,
            armor=armor,
            owner=str(payload.get("owner") or ""),
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise DeskError("Seal file cannot be read.") from exc


def write_seal(path: Path, seal: Seal) -> None:
    refuse_synced_folder(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(
        {
            "v": 1,
            "round": seal.round,
            "window_start": seal.window_start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "owner": seal.owner,
            "armor": seal.armor.decode("ascii"),
        },
        indent=2,
    ).encode("ascii") + b"\n"
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    restrict_to_user_and_system(path, seal.owner)


def restrict_to_user_and_system(path: Path, owner: str) -> None:
    if os.name != "nt":
        return
    grants = ["*S-1-5-18:(M)"]
    if owner:
        grants.append(f"{owner}:(M)")
    else:
        grants.append("*S-1-5-32-544:(M)")
    command = ["icacls", str(path), "/inheritance:r"]
    for grant in grants:
        command.extend(["/grant:r", grant])
    subprocess.run(
        command,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
