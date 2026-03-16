from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

PresetType = Literal["today", "yesterday", "last_30m", "last_1h", "last_6h", "last_12h"]
SourceType = Literal["days", "preset", "custom"]


class TimeWindowSettings(Protocol):
    monitor_timezone: str
    monitor_retention_days: int
    monitor_default_days: int


class TimeWindowValidationError(ValueError):
    pass

_PRESET_SET = {
    "today",
    "yesterday",
    "last_30m",
    "last_1h",
    "last_6h",
    "last_12h",
}


@dataclass(frozen=True)
class MetricsTimeWindow:
    start_utc: datetime
    end_utc: datetime
    timezone: str
    source: SourceType
    preset: str
    requested_days: int
    bucket_minutes: int

    @property
    def duration_seconds(self) -> int:
        return max(1, int((self.end_utc - self.start_utc).total_seconds()))

    @property
    def is_day_bucket(self) -> bool:
        return self.bucket_minutes >= 1440


def _parse_iso_datetime(raw: str, *, tz: ZoneInfo) -> datetime:
    value = str(raw or "").strip()
    if not value:
        raise ValueError("empty datetime")

    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"

    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc)


def _bucket_minutes_for_window(start_utc: datetime, end_utc: datetime) -> int:
    seconds = max(1, int((end_utc - start_utc).total_seconds()))
    if seconds <= 60 * 60:
        return 5
    if seconds <= 6 * 60 * 60:
        return 15
    if seconds <= 24 * 60 * 60:
        return 30
    if seconds <= 72 * 60 * 60:
        return 60
    return 1440


def resolve_time_window(
    *,
    settings: TimeWindowSettings,
    days: int,
    preset: str | None,
    start: str | None,
    end: str | None,
) -> MetricsTimeWindow:
    tz_name = str(settings.monitor_timezone or "Asia/Tokyo")
    tz = ZoneInfo(tz_name)
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(tz)

    cleaned_preset = str(preset or "").strip().lower()
    raw_start = str(start or "").strip()
    raw_end = str(end or "").strip()

    if cleaned_preset and cleaned_preset not in _PRESET_SET:
        raise TimeWindowValidationError(f"unsupported preset: {cleaned_preset}")

    default_days = max(1, min(int(days or settings.monitor_default_days), settings.monitor_retention_days))

    source: SourceType
    selected_preset = ""

    if raw_start or raw_end:
        source = "custom"
        try:
            start_utc = _parse_iso_datetime(raw_start, tz=tz) if raw_start else (now_utc - timedelta(days=default_days))
            end_utc = _parse_iso_datetime(raw_end, tz=tz) if raw_end else now_utc
        except Exception as exc:  # pragma: no cover - error path tested via caller
            raise TimeWindowValidationError(f"invalid custom datetime: {exc}") from exc
    elif cleaned_preset:
        source = "preset"
        selected_preset = cleaned_preset
        if cleaned_preset == "today":
            local_start = datetime(
                year=now_local.year,
                month=now_local.month,
                day=now_local.day,
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
                tzinfo=tz,
            )
            local_end = now_local
        elif cleaned_preset == "yesterday":
            today_start = datetime(
                year=now_local.year,
                month=now_local.month,
                day=now_local.day,
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
                tzinfo=tz,
            )
            local_start = today_start - timedelta(days=1)
            local_end = today_start
        elif cleaned_preset == "last_30m":
            local_start = now_local - timedelta(minutes=30)
            local_end = now_local
        elif cleaned_preset == "last_1h":
            local_start = now_local - timedelta(hours=1)
            local_end = now_local
        elif cleaned_preset == "last_6h":
            local_start = now_local - timedelta(hours=6)
            local_end = now_local
        else:  # last_12h
            local_start = now_local - timedelta(hours=12)
            local_end = now_local
        start_utc = local_start.astimezone(timezone.utc)
        end_utc = local_end.astimezone(timezone.utc)
    else:
        source = "days"
        start_utc = now_utc - timedelta(days=default_days)
        end_utc = now_utc

    if end_utc <= start_utc:
        raise TimeWindowValidationError("end must be later than start")

    retention_floor = now_utc - timedelta(days=settings.monitor_retention_days)
    if start_utc < retention_floor:
        start_utc = retention_floor

    if end_utc > now_utc:
        end_utc = now_utc

    if end_utc <= start_utc:
        end_utc = start_utc + timedelta(minutes=1)

    requested_days = max(1, int((end_utc - start_utc).total_seconds() // 86400) + 1)

    return MetricsTimeWindow(
        start_utc=start_utc,
        end_utc=end_utc,
        timezone=tz_name,
        source=source,
        preset=selected_preset,
        requested_days=requested_days,
        bucket_minutes=_bucket_minutes_for_window(start_utc, end_utc),
    )
