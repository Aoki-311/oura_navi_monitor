from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class RefreshPolicy:
    """Single owner for the Monitor refresh, freshness, lease and alert timings."""

    job_name: str = "oura-navi-monitor-refresh"
    scheduler_name: str = "oura-navi-monitor-refresh-three-hour"
    legacy_scheduler_name: str = "oura-navi-monitor-refresh-quarter-hour"
    scheduler_cron: str = "5 */3 * * *"
    legacy_scheduler_cron: str = "*/15 * * * *"
    scheduler_bootstrap_lead_days: int = 2
    scheduler_attempt_deadline_seconds: int = 60
    legacy_scheduler_attempt_deadline_seconds: int = 30
    scheduler_max_retry_attempts: int = 0
    timezone: str = "Asia/Tokyo"
    cadence_minutes: int = 180
    expected_delay_minutes: int = 5
    event_future_tolerance_minutes: int = 10
    overlap_minutes: int = 240
    max_window_hours: int = 24
    freshness_stale_after_minutes: int = 240
    no_success_warning_minutes: int = 240
    no_success_critical_minutes: int = 420
    lease_ttl_minutes: int = 45
    job_timeout_minutes: int = 30

    def __post_init__(self) -> None:
        if self.cadence_minutes <= 0 or 1440 % self.cadence_minutes != 0:
            raise ValueError("refresh cadence must divide one day")
        if self.expected_delay_minutes < 0:
            raise ValueError("refresh delay cannot be negative")
        if self.event_future_tolerance_minutes < self.expected_delay_minutes:
            raise ValueError("event future tolerance must cover the expected log delay")
        if self.scheduler_bootstrap_lead_days < 2:
            raise ValueError("scheduler bootstrap lead must leave at least one full day")
        if not 15 <= self.scheduler_attempt_deadline_seconds <= 1800:
            raise ValueError("scheduler attempt deadline must be between 15s and 30m")
        if not 15 <= self.legacy_scheduler_attempt_deadline_seconds <= 1800:
            raise ValueError("legacy scheduler attempt deadline must be between 15s and 30m")
        if not 0 <= self.scheduler_max_retry_attempts <= 5:
            raise ValueError("scheduler retry count must be between zero and five")
        if self.overlap_minutes < self.cadence_minutes + self.expected_delay_minutes:
            raise ValueError("refresh overlap must cover one cadence plus expected delay")
        if self.freshness_stale_after_minutes < self.cadence_minutes:
            raise ValueError("freshness threshold must cover one refresh cadence")
        if self.no_success_warning_minutes < self.freshness_stale_after_minutes:
            raise ValueError("warning threshold cannot precede the freshness threshold")
        if self.no_success_critical_minutes <= self.no_success_warning_minutes:
            raise ValueError("critical threshold must follow the warning threshold")
        if self.lease_ttl_minutes <= self.job_timeout_minutes:
            raise ValueError("lease TTL must exceed the Cloud Run Job timeout")


REFRESH_POLICY = RefreshPolicy()


def safe_scheduler_bootstrap_cron(
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> str:
    """Return a valid calendar schedule whose next match is over 24 hours away."""

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    local_zone = ZoneInfo(timezone_name or REFRESH_POLICY.timezone)
    safe_date = current.astimezone(local_zone) + timedelta(
        days=REFRESH_POLICY.scheduler_bootstrap_lead_days
    )
    return f"0 0 {safe_date.day} {safe_date.month} *"


def next_scheduled_refresh(
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> datetime:
    """Return the next three-hour scheduler boundary as an aware UTC datetime."""

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    local_zone = ZoneInfo(timezone_name or REFRESH_POLICY.timezone)
    local_now = current.astimezone(local_zone)
    cadence_hours = REFRESH_POLICY.cadence_minutes // 60
    candidate = local_now.replace(
        hour=(local_now.hour // cadence_hours) * cadence_hours,
        minute=REFRESH_POLICY.expected_delay_minutes,
        second=0,
        microsecond=0,
    )
    if candidate <= local_now:
        candidate += timedelta(hours=cadence_hours)
    return candidate.astimezone(timezone.utc)


__all__ = [
    "REFRESH_POLICY",
    "RefreshPolicy",
    "next_scheduled_refresh",
    "safe_scheduler_bootstrap_cron",
]
