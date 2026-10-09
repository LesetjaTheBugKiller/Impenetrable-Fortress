import os
import tempfile
import unittest
from datetime import datetime, time, timezone
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

from desk.link.beacon import BeaconTime, round_not_before
from desk.link.commands import link_action, run_link, run_reseal, run_seal
from desk.shared.config import DeskConfig, load_config
from desk.shared.errors import DeskError, TooEarly
from desk.link.mail import extract_links
from desk.link.probe import OPEN, REFUSED, SILENT
from desk.link.sealfile import Seal, read_seal, refuse_synced_folder, write_seal
from desk.shared.window import Window

BERLIN = ZoneInfo("Europe/Berlin")
UTC = timezone.utc
SECRET = b"app-password-value"


class FakeBeacon:
    def __init__(self, at, round_number, error=None):
        self._at = at
        self._round = round_number
        self._error = error

    def now(self):
        if self._error:
            raise self._error
        return BeaconTime(self._round, self._at)


class FakeLock:
    def __init__(self, published):
        self.published = published
        self.boxes = {}
        self.decrypts = 0

    def encrypt(self, plaintext, round_number, force=False):
        token = os.urandom(8).hex().encode("ascii")
        blob = b"FAKE" + token
        self.boxes[blob] = (round_number, bytes(plaintext))
        return blob

    def decrypt(self, armor):
        self.decrypts += 1
        round_number, plaintext = self.boxes[armor]
        if round_number > self.published:
            raise TooEarly("early")
        return plaintext


def config_for(path: Path) -> DeskConfig:
    return DeskConfig(
        window=Window(BERLIN, 4, time(22, 0), 6, time(22, 0)),
        vps_host="203.0.113.10",
        rdp_port=3389,
        rdp_timeout=5,
        gmail_address="desk@gmail.com",
        provider_from="reset@provider.example",
        provider_link_host="console.provider.example",
        seal_path=path,
        source=path,
    )


def this_window_seal(folder: Path, lock: FakeLock) -> Path:
    start = datetime(2026, 10, 9, 20, 0, tzinfo=UTC)
    chosen = round_not_before(int(start.timestamp()))
    path = folder / "seal.json"
    write_seal(path, Seal(chosen, start, lock.encrypt(SECRET, chosen), "micca"))
    return path


class LinkDecisionTests(unittest.TestCase):
    def test_only_an_open_window_and_a_refusal_shows_the_link(self):
        self.assertEqual(link_action(False, True, REFUSED), SILENT)
        self.assertEqual(link_action(True, False, REFUSED), "closed")
        self.assertEqual(link_action(True, True, SILENT), SILENT)
        self.assertEqual(link_action(True, True, OPEN), "desk")
        self.assertEqual(link_action(True, True, REFUSED), "show")

    def test_network_failures_do_not_open_the_seal(self):
        with tempfile.TemporaryDirectory() as folder:
            lock = FakeLock(published=10**12)
            path = this_window_seal(Path(folder), lock)
            out = StringIO()
            calls = []

            def boom(*_args):
                raise OSError("down")

            code = run_link(config_for(path), FakeBeacon(None, 0, error=DeskError("down")), boom, lock, calls.append, out)
            self.assertEqual(code, 0)
            self.assertEqual(out.getvalue(), "")
            self.assertEqual(lock.decrypts, 0)
            self.assertEqual(calls, [])

            out = StringIO()
            saturday = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
            code = run_link(
                config_for(path),
                FakeBeacon(saturday, 10**12),
                lambda *_args: SILENT,
                lock,
                calls.append,
                out,
            )
            self.assertEqual(code, 0)
            self.assertEqual(out.getvalue(), "")
            self.assertEqual(lock.decrypts, 0)

    def test_refusal_during_the_window_shows_the_link_and_not_the_password(self):
        with tempfile.TemporaryDirectory() as folder:
            lock = FakeLock(published=10**12)
            path = this_window_seal(Path(folder), lock)
            seen = {}

            def mail(address, password, sender, host):
                seen["password"] = bytes(password)
                seen["host"] = host
                return ["https://console.provider.example/reset/abc"]

            out = StringIO()
            code = run_link(
                config_for(path),
                FakeBeacon(datetime(2026, 10, 10, 12, 0, tzinfo=UTC), 10**12),
                lambda *_args: REFUSED,
                lock,
                mail,
                out,
            )
            self.assertEqual(code, 0)
            self.assertEqual(out.getvalue(), "https://console.provider.example/reset/abc\n")
            self.assertNotIn(SECRET.decode(), out.getvalue())
            self.assertEqual(seen["password"], SECRET)
            self.assertEqual(lock.decrypts, 1)

    def test_open_desk_does_not_open_the_seal(self):
        with tempfile.TemporaryDirectory() as folder:
            lock = FakeLock(published=10**12)
            path = this_window_seal(Path(folder), lock)
            out = StringIO()
            code = run_link(
                config_for(path),
                FakeBeacon(datetime(2026, 10, 10, 12, 0, tzinfo=UTC), 10**12),
                lambda *_args: OPEN,
                lock,
                lambda *_args: ["https://example.test"],
                out,
            )
            self.assertEqual(out.getvalue(), "Desk is open.\n")
            self.assertEqual(code, 0)
            self.assertEqual(lock.decrypts, 0)

    def test_closed_window_does_not_probe(self):
        with tempfile.TemporaryDirectory() as folder:
            lock = FakeLock(published=10**12)
            path = this_window_seal(Path(folder), lock)
            out = StringIO()

            def probe(*_args):
                raise AssertionError("probed")

            code = run_link(
                config_for(path),
                FakeBeacon(datetime(2026, 10, 9, 19, 0, tzinfo=UTC), 10**12),
                probe,
                lock,
                lambda *_args: [],
                out,
            )
            self.assertEqual(code, 0)
            self.assertEqual(out.getvalue(), "Window is closed.\n")
            self.assertEqual(lock.decrypts, 0)


class ResealTests(unittest.TestCase):
    def test_reseal_replaces_the_file_only_after_the_window(self):
        with tempfile.TemporaryDirectory() as folder:
            start = datetime(2026, 10, 9, 20, 0, tzinfo=UTC)
            chosen = round_not_before(int(start.timestamp()))
            lock = FakeLock(published=chosen)
            path = Path(folder) / "seal.json"
            write_seal(path, Seal(chosen, start, lock.encrypt(SECRET, chosen), "micca"))
            original = path.read_bytes()
            out = StringIO()
            code = run_reseal(
                config_for(path),
                FakeBeacon(datetime(2026, 10, 10, 12, 0, tzinfo=UTC), chosen),
                lock,
                out,
            )
            self.assertEqual(code, 0)
            self.assertEqual(out.getvalue(), "")
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(lock.decrypts, 0)

            out = StringIO()
            code = run_reseal(
                config_for(path),
                FakeBeacon(datetime(2026, 10, 11, 20, 0, tzinfo=UTC), chosen),
                lock,
                out,
            )
            self.assertEqual(code, 0)
            self.assertIn("Resealed until Friday 2026-10-16 22:00", out.getvalue())
            self.assertNotIn(SECRET.decode(), out.getvalue())
            rewritten = path.read_bytes()
            self.assertNotEqual(rewritten, original)
            self.assertNotIn(original, rewritten)
            self.assertNotIn(SECRET, rewritten)
            sealed = read_seal(path)
            self.assertGreater(sealed.round, chosen)
            with self.assertRaises(TooEarly):
                lock.decrypt(sealed.armor)

    def test_synced_folder_is_refused(self):
        with self.assertRaises(DeskError):
            refuse_synced_folder(Path(r"C:\Users\me\OneDrive\seal.json"))

    def test_seal_before_the_window_does_not_store_the_password(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "seal.json"
            target_round = round_not_before(int(datetime(2026, 10, 9, 20, 0, tzinfo=UTC).timestamp()))
            lock = FakeLock(published=0)
            out = StringIO()
            code = run_seal(
                config_for(path),
                FakeBeacon(datetime(2026, 10, 8, 10, 0, tzinfo=UTC), 1),
                lock,
                bytearray(SECRET),
                out,
            )
            self.assertEqual(code, 0)
            self.assertIn("Sealed until Friday 2026-10-09 22:00", out.getvalue())
            body = path.read_bytes()
            self.assertNotIn(SECRET, body)
            self.assertEqual(read_seal(path).round, target_round)


class MailTests(unittest.TestCase):
    def test_extracts_provider_links(self):
        raw = (
            b"From: reset@provider.example\r\n"
            b"Content-Type: text/html; charset=utf-8\r\n"
            b"\r\n"
            b'<a href="https://console.provider.example/reset/abc.">reset</a>'
            b'<a href="https://other.example/nope">no</a>'
        )
        self.assertEqual(
            extract_links(raw, "console.provider.example"),
            ["https://console.provider.example/reset/abc"],
        )


class ConfigTests(unittest.TestCase):
    def test_loads_the_shared_window(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "desk.env"
            path.write_text(
                "TIMEZONE=Europe/Berlin\nOPEN_DAY=Friday\nOPEN_TIME=22:00\n"
                "CLOSE_DAY=Sunday\nCLOSE_TIME=22:00\nVPS_HOST=203.0.113.10\n",
                encoding="utf-8",
            )
            loaded = load_config(path)
            self.assertEqual(loaded.window.open_day, 4)
            self.assertTrue(loaded.seal_path.is_absolute())
            self.assertEqual(loaded.vps_host, "203.0.113.10")

    def test_zero_length_window_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "desk.env"
            path.write_text(
                "TIMEZONE=Europe/Berlin\nOPEN_DAY=Friday\nOPEN_TIME=22:00\n"
                "CLOSE_DAY=Friday\nCLOSE_TIME=22:00\n",
                encoding="utf-8",
            )
            with self.assertRaises(DeskError):
                load_config(path)
