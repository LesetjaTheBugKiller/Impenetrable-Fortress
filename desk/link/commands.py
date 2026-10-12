"""What `link` is allowed to do, and when the seal is rewritten."""

from __future__ import annotations

import os
import sys

from desk.link.beacon import round_not_before
from desk.shared.config import DeskConfig
from desk.shared.errors import DeskError, TooEarly
from desk.link.probe import OPEN, REFUSED, SILENT
from desk.link.sealfile import Seal, read_seal, write_seal
from desk.link.tle import wipe


def link_action(beacon_ok: bool, window_open: bool, probe_result: str) -> str:
    if not beacon_ok:
        return SILENT
    if not window_open:
        return "closed"
    if probe_result == REFUSED:
        return "show"
    if probe_result == OPEN:
        return "desk"
    return SILENT


def run_link(config: DeskConfig, beacon, probe, timelock, read_mail, stdout) -> int:
    try:
        current = beacon.now()
    except Exception:
        return 0
    open_now = config.window.is_open(current.at)
    action = link_action(True, open_now, SILENT)
    if action == "closed":
        stdout.write("Window is closed.\n")
        return 0
    try:
        probed = probe(config.vps_host, config.rdp_port, config.rdp_timeout)
    except Exception:
        return 0
    action = link_action(True, True, probed)
    if action == "desk":
        stdout.write("Desk is open.\n")
        return 0
    if action != "show":
        return 0
    seal = _readable_seal(config.seal_path, stdout)
    if seal is None:
        return 1
    try:
        secret = bytearray(timelock.decrypt(seal.armor))
    except TooEarly:
        stdout.write("Seal is not open yet.\n")
        return 1
    except DeskError:
        stdout.write("Could not open the seal.\n")
        return 1
    try:
        links = read_mail(config.gmail_address, bytes(secret), config.provider_from, config.provider_link_host)
    except DeskError:
        stdout.write("Could not read mail.\n")
        return 1
    finally:
        wipe(secret)
    if not links:
        stdout.write("No link in the newest provider mail.\n")
        return 1
    for url in links:
        stdout.write(url + "\n")
    return 0


def run_seal(config: DeskConfig, beacon, timelock, password: bytearray, stdout) -> int:
    try:
        current = beacon.now()
        target = config.window.seal_target(current.at)
        round_number = round_not_before(int(target.timestamp()))
        if current.round >= round_number:
            raise DeskError("That window has already started.")
        armor = timelock.encrypt(bytes(password), round_number)
        if bytes(password) in armor:
            raise DeskError("The sealed file would have contained the app password.")
        write_seal(
            config.seal_path,
            Seal(round=round_number, window_start=target, armor=armor, owner=os.environ.get("USERNAME", "")),
        )
    finally:
        wipe(password)
    local = format_when(target, config.window.zone)
    stdout.write(f"Sealed until {local}.\n")
    return 0


def run_reseal(config: DeskConfig, beacon, timelock, stdout, stderr=None) -> int:
    try:
        current = beacon.now()
    except Exception:
        return 0
    try:
        seal = read_seal(config.seal_path)
    except DeskError:
        stdout.write("Seal file cannot be read.\n")
        return 1
    if seal is None:
        (stderr or sys.stderr).write(f"Seal file is missing: {config.seal_path}. Run: py -3 -m desk.link seal\n")
        return 1
    if current.at < config.window.close_for(seal.window_start):
        return 0
    try:
        secret = bytearray(timelock.decrypt(seal.armor))
    except TooEarly:
        return 0
    except DeskError:
        stdout.write("Could not open the seal.\n")
        return 1
    try:
        target = config.window.seal_target(current.at)
        round_number = round_not_before(int(target.timestamp()))
        if current.round >= round_number:
            raise DeskError("The next window has already started.")
        armor = timelock.encrypt(bytes(secret), round_number)
        if bytes(secret) in armor:
            raise DeskError("The sealed file would have contained the app password.")
        write_seal(
            config.seal_path,
            Seal(round=round_number, window_start=target, armor=armor, owner=seal.owner),
        )
    finally:
        wipe(secret)
    local = format_when(target, config.window.zone)
    stdout.write(f"Resealed until {local}.\n")
    return 0


def format_when(instant, zone) -> str:
    local = instant.astimezone(zone)
    names = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
    return f"{names[local.weekday()]} {local.strftime('%Y-%m-%d %H:%M')}"


def _readable_seal(path, stdout) -> Seal | None:
    try:
        seal = read_seal(path)
    except DeskError:
        stdout.write("Seal file cannot be read.\n")
        return None
    if seal is None:
        stdout.write("Seal file is missing.\n")
        return None
    return seal
