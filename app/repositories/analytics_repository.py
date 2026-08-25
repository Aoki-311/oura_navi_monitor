from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from google.cloud import bigquery

from app.settings import Settings
from app.time_window import MetricsTimeWindow


class AnalyticsRepository:
    """Read only the partition-bounded canonical semantic functions."""

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client or bigquery.Client(project=settings.monitor_project_id)
        self._dataset = f"{settings.monitor_project_id}.{settings.monitor_bq_dataset}"

    def _view(self, name: str) -> str:
        return f"`{self._dataset}.{name}`"

    def _history_start_date(self):
        text = str(self._settings.monitor_analytics_start_at or "").strip()
        if not text:
            raise ValueError("MONITOR_ANALYTICS_START_AT is required for historical user metrics")
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        resolved = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        return resolved.astimezone(ZoneInfo(self._settings.monitor_timezone)).date()

    def _run(self, sql: str, parameters: list[Any]) -> list[dict[str, Any]]:
        config = bigquery.QueryJobConfig(
            query_parameters=parameters,
            maximum_bytes_billed=max(1, int(self._settings.monitor_query_maximum_bytes)),
            use_query_cache=True,
        )
        return [dict(row.items()) for row in self._client.query(
            sql,
            job_config=config,
            location=self._settings.monitor_bq_location,
        ).result()]

    @staticmethod
    def _partition_parameters(window: MetricsTimeWindow) -> list[Any]:
        local = ZoneInfo(window.timezone)
        return [
            bigquery.ScalarQueryParameter(
                "start_date", "DATE", window.start_utc.astimezone(local).date()
            ),
            bigquery.ScalarQueryParameter(
                "end_date",
                "DATE",
                (window.end_utc - timedelta(microseconds=1)).astimezone(local).date(),
            ),
        ]

    def overview_events(self, *, window: MetricsTimeWindow, area_key: str = "") -> list[dict[str, Any]]:
        sql = f"""
        SELECT *
        FROM {self._view('dashboard_events')}(@start_date, @end_date)
        WHERE question_date BETWEEN @start_date AND @end_date
          AND question_ts >= @start_ts
          AND question_ts < @end_ts
          AND (@area_key = '' OR area_key = @area_key)
        ORDER BY question_ts
        """
        return self._run(
            sql,
            [
                bigquery.ScalarQueryParameter("start_ts", "TIMESTAMP", window.start_utc),
                bigquery.ScalarQueryParameter("end_ts", "TIMESTAMP", window.end_utc),
                bigquery.ScalarQueryParameter("area_key", "STRING", area_key),
                *self._partition_parameters(window),
            ],
        )

    def activity_events(self, *, end: datetime, area_key: str = "") -> list[dict[str, Any]]:
        sql = f"""
        SELECT roster_id, question_ts, question_date, area_key, area, role
        FROM {self._view('dashboard_events')}(@start_date, @end_date)
        WHERE question_date BETWEEN @start_date AND @end_date
          AND question_ts >= @start_ts
          AND question_ts < @end_ts
          AND (@area_key = '' OR area_key = @area_key)
        """
        window = MetricsTimeWindow(
            start_utc=end - timedelta(days=14),
            end_utc=end,
            timezone=self._settings.monitor_timezone,
            source="days",
            preset="",
            requested_days=14,
            bucket_minutes=1440,
        )
        return self._run(
            sql,
            [
                bigquery.ScalarQueryParameter("start_ts", "TIMESTAMP", window.start_utc),
                bigquery.ScalarQueryParameter("end_ts", "TIMESTAMP", end),
                bigquery.ScalarQueryParameter("area_key", "STRING", area_key),
                *self._partition_parameters(window),
            ],
        )

    def user_metrics(self) -> list[dict[str, Any]]:
        today = datetime.now(timezone.utc).astimezone(
            ZoneInfo(self._settings.monitor_timezone)
        ).date()
        return self._run(
            f"SELECT * FROM {self._view('dashboard_user_list')}(@history_start_date, @today) ORDER BY last_active_at DESC",
            [
                bigquery.ScalarQueryParameter(
                    "history_start_date", "DATE", self._history_start_date()
                ),
                bigquery.ScalarQueryParameter("today", "DATE", today),
            ],
        )

    def user_detail_events(self, *, roster_id: str, window: MetricsTimeWindow) -> list[dict[str, Any]]:
        sql = f"""
        SELECT *
        FROM {self._view('dashboard_events')}(@start_date, @end_date)
        WHERE roster_id = @roster_id
          AND question_date BETWEEN @start_date AND @end_date
          AND question_ts >= @start_ts
          AND question_ts < @end_ts
        ORDER BY question_ts
        """
        return self._run(
            sql,
            [
                bigquery.ScalarQueryParameter("roster_id", "STRING", roster_id),
                bigquery.ScalarQueryParameter("start_ts", "TIMESTAMP", window.start_utc),
                bigquery.ScalarQueryParameter("end_ts", "TIMESTAMP", window.end_utc),
                *self._partition_parameters(window),
            ],
        )
