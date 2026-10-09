"""Official drand tle tool. The key for a round does not exist until that round is published."""

from __future__ import annotations

import hashlib
import io
import os
import platform
import subprocess
import tarfile
import urllib.request
from pathlib import Path

from desk.link.beacon import CHAIN_HASH, NETWORK
from desk.shared.errors import DeskError, TooEarly

TLE_VERSION = "1.2.0"
RELEASE = f"https://github.com/drand/tlock/releases/download/v{TLE_VERSION}"
ASSETS = {
    "amd64": (
        f"tlock_{TLE_VERSION}_windows_amd64.tar.gz",
        "c8117179edbd4fcebae56983e385a94b5f17fc90efeddd62b28304f96da91e84",
    ),
    "arm64": (
        f"tlock_{TLE_VERSION}_windows_arm64.tar.gz",
        "15d2d06fa166e3a1d70a4d882b4f91f5f7fdaa67dac697855f1455d71bb2d870",
    ),
}


class Tle:
    def __init__(self, binary: Path, network: str = NETWORK, chain: str = CHAIN_HASH):
        self.binary = binary
        self.network = network
        self.chain = chain

    def encrypt(self, plaintext: bytes, round_number: int, force: bool = False) -> bytes:
        command = [
            str(self.binary),
            "--encrypt",
            "--armor",
            "--round",
            str(round_number),
            "--chain",
            self.chain,
            "--network",
            self.network,
        ]
        if force:
            command.append("--force")
        completed = self._run(command, plaintext)
        if plaintext and plaintext in completed.stdout:
            raise DeskError("The sealed file would have contained the app password.")
        return completed.stdout

    def decrypt(self, armor: bytes) -> bytes:
        command = [
            str(self.binary),
            "--decrypt",
            "--chain",
            self.chain,
            "--network",
            self.network,
        ]
        try:
            completed = self._run(command, armor)
        except DeskError as exc:
            if "too early" in str(exc).lower():
                raise TooEarly("The time-lock key is not published yet.") from exc
            raise DeskError("Could not open the seal.") from exc
        return completed.stdout

    def _run(self, command: list[str], payload: bytes) -> subprocess.CompletedProcess[bytes]:
        if not self.binary.is_file():
            raise DeskError("The time lock program is not installed.")
        try:
            completed = subprocess.run(
                command,
                input=payload,
                capture_output=True,
                timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as exc:
            raise DeskError("The time lock program did not answer.") from exc
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise DeskError(detail or "The time lock program failed.")
        return completed


def default_binary() -> Path:
    return Path(__file__).resolve().parent.parent / "bin" / "tle.exe"


def ensure_binary(binary: Path | None = None) -> Path:
    path = binary or default_binary()
    if path.is_file():
        return path
    if os.name != "nt":
        raise DeskError("The time lock program is provided for Windows.")
    name, digest = _asset()
    request = urllib.request.Request(f"{RELEASE}/{name}", headers={"User-Agent": "weekend-desk"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            archive = response.read()
    except Exception as exc:
        raise DeskError("Could not download the time lock program.") from exc
    actual = hashlib.sha256(archive).hexdigest()
    if actual != digest:
        raise DeskError("The time lock download did not match its published checksum.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
        member = bundle.getmember("tle.exe")
        extracted = bundle.extractfile(member)
        if extracted is None:
            raise DeskError("The time lock download did not contain tle.exe.")
        temporary = path.with_name("tle.exe.part")
        try:
            temporary.write_bytes(extracted.read())
            os.replace(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    return path


def _asset() -> tuple[str, str]:
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64"):
        return ASSETS["amd64"]
    if machine in ("arm64", "aarch64"):
        return ASSETS["arm64"]
    raise DeskError(f"No time lock build is published for {platform.machine()}.")


def wipe(secret: bytearray) -> None:
    for index in range(len(secret)):
        secret[index] = 0
