"""Weekend window in one timezone.

The window is half-open: it includes the opening minute and excludes the closing
minute. A week is measured in local dates, so a daylight-saving change does not
move Friday 22:00 or Sunday 22:00.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


@dataclass(frozen=True)
class Window:
    zone: ZoneInfo
    open_day: int
    open_time: time
    close_day: int
    close_time: time

    def is_open(self, instant: datetime) -> bool:
        instant = _aware(instant)
        upcoming = self.next_start_at_or_after(instant)
        if _same(instant, upcoming):
            return True
        previous = self.add_weeks(upcoming, -1)
        return previous <= instant < self.close_for(previous)

    def next_start_at_or_after(self, instant: datetime) -> datetime:
        local = _aware(instant).astimezone(self.zone)
        monday = local.date() - timedelta(days=local.weekday())
        start = self.at(monday + timedelta(days=self.open_day), self.open_time)
        if start < local:
            start = self.add_weeks(start, 1)
        return start

    def seal_target(self, instant: datetime) -> datetime:
        """The next window start whose key is still in the future."""
        upcoming = self.next_start_at_or_after(instant)
        if _same(instant, upcoming) or upcoming <= _aware(instant):
            return self.add_weeks(upcoming, 1)
        return upcoming

    def close_for(self, start: datetime) -> datetime:
        local = _aware(start).astimezone(self.zone)
        delta_days = (self.close_day - self.open_day) % 7
        if delta_days == 0 and self.close_time <= self.open_time:
            delta_days = 7
        return self.at(local.date() + timedelta(days=delta_days), self.close_time)

    def add_weeks(self, start: datetime, weeks: int) -> datetime:
        local = _aware(start).astimezone(self.zone)
        return self.at(local.date() + timedelta(days=7 * weeks), self.open_time)

    def at(self, day: date, clock: time) -> datetime:
        return datetime(day.year, day.month, day.day, clock.hour, clock.minute, tzinfo=self.zone)


def parse_weekday(value: str) -> int:
    try:
        return WEEKDAYS[value.strip().lower()]
    except KeyError as exc:
        raise ValueError(f"Unknown weekday {value!r}.") from exc


def parse_clock(value: str) -> time:
    parts = value.strip().split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ValueError(f"Clock time must look like 22:00, not {value!r}.")
    hour, minute = int(parts[0]), int(parts[1])
    if hour > 23 or minute > 59:
        raise ValueError(f"Clock time must look like 22:00, not {value!r}.")
    return time(hour, minute)


def load_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name.strip())
    except Exception as exc:
        raise ValueError(f"Unknown timezone {name!r}.") from exc


def _aware(instant: datetime) -> datetime:
    if instant.tzinfo is None:
        raise ValueError("A window instant needs a timezone.")
    return instant.astimezone(timezone.utc)


def _same(left: datetime, right: datetime) -> bool:
    return _aware(left) == _aware(right)
