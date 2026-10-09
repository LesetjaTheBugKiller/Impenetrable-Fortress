"""League of Entropy quicknet beacon.

The link app asks this beacon whether the weekend window is open. The beacon is
free, it has no account, and a round's signature does not exist until that
round is published.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

from desk.shared.errors import BeaconError

CHAIN_HASH = "52db9ba70e0cc0f6eaf7803dd07447a1f5477735fd3f661792ba94600c84e971"
PUBLIC_KEY = (
    "83cf0f2896adee7eb8b5f01fcad3912212c437e0073e911fb90022d3e760183c"
    "8c4b450b6a0a6c3ac6a5776a2d1064510d1fec758c921cc22b0e17e63aaf4bcb5"
    "ed66304de9cf809bd274ca73bab4af5a6e9c76a4bc09e76eae8991ef5ece45a"
)
GENESIS = 1692803367
PERIOD = 3
SCHEME = "bls-unchained-g1-rfc9380"
NETWORK = "https://api.drand.sh"


@dataclass(frozen=True)
class BeaconTime:
    round: int
    at: datetime


def round_not_before(unix_time: int, genesis: int = GENESIS, period: int = PERIOD) -> int:
    """First quicknet round whose published time is at or after unix_time."""
    if unix_time <= genesis:
        return 1
    delta = unix_time - genesis
    return (delta + period - 1) // period + 1


def published_at(round_number: int, genesis: int = GENESIS, period: int = PERIOD) -> datetime:
    return datetime.fromtimestamp(genesis + (round_number - 1) * period, timezone.utc)


class Beacon:
    def __init__(self, network: str = NETWORK, timeout: float = 5.0, opener=None):
        self.network = network.rstrip("/")
        self.timeout = timeout
        self._open = opener or urllib.request.urlopen

    def now(self) -> BeaconTime:
        info = self._get_json(f"/{CHAIN_HASH}/info")
        self._check_info(info)
        latest = self._get_json(f"/{CHAIN_HASH}/public/latest")
        try:
            round_number = int(latest["round"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BeaconError("The time beacon returned no round.") from exc
        return BeaconTime(round=round_number, at=published_at(round_number))

    def _check_info(self, info: dict) -> None:
        if (
            info.get("hash") != CHAIN_HASH
            or info.get("public_key") != PUBLIC_KEY
            or info.get("period") != PERIOD
            or info.get("genesis_time") != GENESIS
            or info.get("schemeID") != SCHEME
        ):
            raise BeaconError("The time beacon did not match the quicknet network.")

    def _get_json(self, path: str) -> dict:
        request = urllib.request.Request(
            self.network + path,
            headers={"User-Agent": "weekend-desk"},
        )
        try:
            with self._open(request, timeout=self.timeout) as response:
                payload = response.read()
        except Exception as exc:
            raise BeaconError("The time beacon did not answer.") from exc
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise BeaconError("The time beacon returned an unreadable answer.") from exc
