from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import unittest
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from app.time_window import resolve_time_window


@dataclass
class _FakeSettings:
    monitor_timezone: str = "Asia/Tokyo"
    monitor_retention_days: int = 180
    monitor_default_days: int = 7


class TimeWindowResolveTest(unittest.TestCase):
    def test_preset_today_resolves(self) -> None:
        settings = _FakeSettings()
        window = resolve_time_window(
            settings=settings,
            days=7,
            preset="today",
            start="",
            end="",
        )
        self.assertEqual(window.source, "preset")
        self.assertEqual(window.preset, "today")
        self.assertGreater(window.end_utc, window.start_utc)

    def test_preset_yesterday_resolves_one_day(self) -> None:
        settings = _FakeSettings()
        window = resolve_time_window(
            settings=settings,
            days=7,
            preset="yesterday",
            start="",
            end="",
        )
        self.assertEqual(window.source, "preset")
        self.assertEqual(window.preset, "yesterday")
        duration_hours = (window.end_utc - window.start_utc).total_seconds() / 3600
        self.assertGreaterEqual(duration_hours, 23.0)
        self.assertLessEqual(duration_hours, 25.0)

    def test_custom_datetime_resolves(self) -> None:
        settings = _FakeSettings()
        now_local = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Tokyo"))
        start_local = (now_local - timedelta(hours=3)).replace(second=0, microsecond=0)
        end_local = (now_local - timedelta(hours=1)).replace(second=0, microsecond=0)
        window = resolve_time_window(
            settings=settings,
            days=7,
            preset="",
            start=start_local.strftime("%Y-%m-%dT%H:%M"),
            end=end_local.strftime("%Y-%m-%dT%H:%M"),
        )
        self.assertEqual(window.source, "custom")
        self.assertEqual(window.preset, "")
        duration_hours = (window.end_utc - window.start_utc).total_seconds() / 3600
        self.assertGreaterEqual(duration_hours, 1.9)
        self.assertLessEqual(duration_hours, 2.1)

    def test_invalid_preset_raises(self) -> None:
        settings = _FakeSettings()
        with self.assertRaises(HTTPException):
            resolve_time_window(
                settings=settings,
                days=7,
                preset="invalid",
                start="",
                end="",
            )

    def test_end_earlier_than_start_raises(self) -> None:
        settings = _FakeSettings()
        with self.assertRaises(HTTPException):
            resolve_time_window(
                settings=settings,
                days=7,
                preset="",
                start="2026-03-16T11:00",
                end="2026-03-16T09:00",
            )

    def test_retention_clamps_old_custom_start(self) -> None:
        settings = _FakeSettings(monitor_retention_days=1)
        window = resolve_time_window(
            settings=settings,
            days=7,
            preset="",
            start="2000-01-01T00:00",
            end="",
        )
        floor = datetime.now(timezone.utc) - timedelta(days=1, minutes=2)
        self.assertGreaterEqual(window.start_utc, floor)


if __name__ == "__main__":
    unittest.main()
