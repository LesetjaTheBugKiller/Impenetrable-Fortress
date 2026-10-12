import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, time, timezone
from io import StringIO
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from desk.link import __main__ as link_main
from desk.link.beacon import BeaconTime
from desk.link.commands import run_reseal
from desk.shared.config import DeskConfig
from desk.shared.window import Window

BERLIN = ZoneInfo("Europe/Berlin")
UTC = timezone.utc
AFTER_THE_WINDOW = datetime(2026, 10, 11, 20, 0, tzinfo=UTC)


class FakeBeacon:
    def __init__(self, at, round_number):
        self._at = at
        self._round = round_number

    def now(self):
        return BeaconTime(self._round, self._at)


class UnusedLock:
    def encrypt(self, *_args, **_kwargs):
        raise AssertionError("encrypted without a seal")

    def decrypt(self, *_args):
        raise AssertionError("decrypted without a seal")


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


class MissingSealTests(unittest.TestCase):
    def test_a_missing_seal_is_a_failure_reported_on_stderr(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "seal.json"
            out = StringIO()
            err = StringIO()
            code = run_reseal(config_for(path), FakeBeacon(AFTER_THE_WINDOW, 10**12), UnusedLock(), out, err)
            self.assertEqual(code, 1)
            self.assertEqual(out.getvalue(), "")
            self.assertEqual(err.getvalue(), f"Seal file is missing: {path}. Run: py -3 -m desk.link seal\n")
            self.assertFalse(path.exists())

    def test_the_reseal_command_exits_with_that_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            missing = folder / "seal.json"
            binary = folder / "tle.exe"
            binary.write_bytes(b"")
            config = folder / "desk.env"
            config.write_text(
                "TIMEZONE=Europe/Berlin\nOPEN_DAY=Friday\nOPEN_TIME=22:00\n"
                "CLOSE_DAY=Sunday\nCLOSE_TIME=22:00\nVPS_HOST=203.0.113.10\n"
                "GMAIL_ADDRESS=desk@gmail.com\nPROVIDER_FROM=reset@provider.example\n"
                f"SEAL_PATH={missing}\n",
                encoding="utf-8",
            )
            out = StringIO()
            err = StringIO()
            with mock.patch.object(link_main, "default_binary", return_value=binary), mock.patch.object(
                link_main, "Beacon", return_value=FakeBeacon(AFTER_THE_WINDOW, 10**12)
            ), mock.patch.object(link_main, "Tle", return_value=UnusedLock()), redirect_stdout(out), redirect_stderr(err):
                code = link_main.main(["reseal", "--config", str(config)])
            self.assertEqual(code, 1)
            self.assertIn("Seal file is missing:", err.getvalue())
            self.assertIn(str(missing), err.getvalue())
            self.assertEqual(out.getvalue(), "")
