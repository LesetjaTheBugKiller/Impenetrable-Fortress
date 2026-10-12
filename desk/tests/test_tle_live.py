import os
import unittest

from desk.link.beacon import Beacon
from desk.shared.errors import BeaconError, TooEarly
from desk.link.tle import Tle, default_binary


@unittest.skipUnless(
    os.environ.get("RUN_LIVE_TESTS") == "1",
    "live test: contacts the drand beacon and runs tle.exe. Set RUN_LIVE_TESTS=1 to run it.",
)
class TleLiveTests(unittest.TestCase):
    def test_a_future_round_stays_sealed_and_a_past_round_opens(self):
        binary = default_binary()
        if not binary.is_file():
            self.skipTest("tle is not installed")
        try:
            current = Beacon().now()
        except BeaconError as exc:
            self.skipTest(str(exc))
        secret = b"test-secret-value-not-real"
        tool = Tle(binary)
        future = tool.encrypt(secret, current.round + 5000)
        self.assertNotIn(secret, future)
        with self.assertRaises(TooEarly):
            tool.decrypt(future)
        opened = tool.decrypt(tool.encrypt(secret, max(1, current.round - 2), force=True))
        self.assertEqual(opened, secret)
