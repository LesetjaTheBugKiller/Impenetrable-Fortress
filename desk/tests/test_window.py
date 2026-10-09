import unittest
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from desk.link.beacon import published_at, round_not_before
from desk.shared.window import Window, parse_clock, parse_weekday

BERLIN = ZoneInfo("Europe/Berlin")
UTC = timezone.utc


def weekend() -> Window:
    return Window(BERLIN, open_day=4, open_time=time(22, 0), close_day=6, close_time=time(22, 0))


class WindowTests(unittest.TestCase):
    def test_friday_to_sunday_bounds(self):
        window = weekend()
        self.assertFalse(window.is_open(datetime(2026, 10, 9, 19, 59, tzinfo=UTC)))
        self.assertTrue(window.is_open(datetime(2026, 10, 9, 20, 0, tzinfo=UTC)))
        self.assertTrue(window.is_open(datetime(2026, 10, 10, 12, 0, tzinfo=UTC)))
        self.assertTrue(window.is_open(datetime(2026, 10, 11, 19, 59, tzinfo=UTC)))
        self.assertFalse(window.is_open(datetime(2026, 10, 11, 20, 0, tzinfo=UTC)))
        self.assertFalse(window.is_open(datetime(2026, 10, 12, 10, 0, tzinfo=UTC)))

    def test_daylight_saving_does_not_move_sunday_evening(self):
        window = weekend()
        self.assertTrue(window.is_open(datetime(2026, 10, 25, 20, 30, tzinfo=UTC)))
        self.assertFalse(window.is_open(datetime(2026, 10, 25, 21, 0, tzinfo=UTC)))

    def test_window_can_cross_monday(self):
        window = Window(BERLIN, open_day=5, open_time=time(18, 0), close_day=0, close_time=time(8, 0))
        self.assertFalse(window.is_open(datetime(2026, 10, 10, 15, 59, tzinfo=UTC)))
        self.assertTrue(window.is_open(datetime(2026, 10, 10, 16, 0, tzinfo=UTC)))
        self.assertTrue(window.is_open(datetime(2026, 10, 11, 12, 0, tzinfo=UTC)))
        self.assertTrue(window.is_open(datetime(2026, 10, 12, 5, 59, tzinfo=UTC)))
        self.assertFalse(window.is_open(datetime(2026, 10, 12, 6, 0, tzinfo=UTC)))

    def test_seal_targets_the_next_start_that_has_not_begun(self):
        window = weekend()
        thursday = datetime(2026, 10, 8, 10, 0, tzinfo=UTC)
        saturday = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
        at_open = datetime(2026, 10, 9, 20, 0, tzinfo=UTC)
        self.assertEqual(window.seal_target(thursday), datetime(2026, 10, 9, 22, 0, tzinfo=BERLIN))
        self.assertEqual(window.seal_target(saturday), datetime(2026, 10, 16, 22, 0, tzinfo=BERLIN))
        self.assertEqual(window.seal_target(at_open), datetime(2026, 10, 16, 22, 0, tzinfo=BERLIN))

    def test_round_is_not_published_before_the_window(self):
        start = weekend().seal_target(datetime(2026, 10, 8, 10, 0, tzinfo=UTC))
        unix = int(start.timestamp())
        chosen = round_not_before(unix)
        self.assertGreaterEqual(published_at(chosen), start.astimezone(UTC))
        self.assertLess(published_at(chosen - 1), start.astimezone(UTC))

    def test_names(self):
        self.assertEqual(parse_weekday("Friday"), 4)
        self.assertEqual(parse_clock("22:00"), time(22, 0))
        with self.assertRaises(ValueError):
            parse_clock("22")
