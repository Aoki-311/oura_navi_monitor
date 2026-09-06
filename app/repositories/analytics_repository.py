from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from google.cloud import bigquery

from app.settings import Settings
from app.time_window import MetricsTimeWindow
from app.repositories.read_cache import PublishedReadCache


class AnalyticsRepository:
    """Read only the partition-bounded canonical semantic functions."""

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client or bigquery.Client(project=settings.monitor_project_id)
        self._dataset = f"{settings.monitor_project_id}.{settings.monitor_bq_dataset}"
        self._reads = PublishedReadCache()

    def _view(self, name: str) -> str:
        return f"`{self._dataset}.{name}`"

    def _events_source(
        self,
        published_run_id: str | None,
    ) -> tuple[str, list[Any]]:
        run_id = self._required_published_run_id(published_run_id)
        return (
            f"{self._view('dashboard_events_v2')}(@start_date, @end_date, @published_run_id)",
            [
                bigquery.ScalarQueryParameter(
                    "published_run_id", "STRING", run_id
                )
            ],
        )

    @staticmethod
    def _required_published_run_id(published_run_id: str | None) -> str:
        run_id = str(published_run_id or "").strip()
        if not run_id:
            raise ValueError("published_run_id is required")
        return run_id

    def _history_start_date(self):
        text = str(self._settings.monitor_analytics_start_at or "").strip()
        if not text:
            raise ValueError("MONITOR_ANALYTICS_START_AT is required for historical user metrics")
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        resolved = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        return resolved.astimezone(ZoneInfo(self._settings.monitor_timezone)).date()

    def _run(self, sql: str, parameters: list[Any], *, immutable: bool = False) -> list[dict[str, Any]]:
        # Only roster snapshots are immutable. Mutable facts share in-flight
        # work, then are re-read; a pointer change cannot poison retained facts.
        run_id = next((item.value for item in parameters if item.name == "published_run_id"), None)
        key = (sql, json.dumps([item.to_api_repr() for item in parameters], sort_keys=True))
        return self._reads.read(key, lambda: self._query(sql, parameters), retain=immutable and bool(run_id))

    def _query(self, sql: str, parameters: list[Any]) -> list[dict[str, Any]]:
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

    def published_roster_snapshot(self, *, published_run_id: str) -> list[dict[str, Any]]:
        run_id = str(published_run_id or "").strip()
        if not run_id:
            raise ValueError("published_run_id is required")
        return self._run(
            f"""
            SELECT
              snapshot_run_id, snapshot_created_at,
              roster_id, user_id, name, email, area, area_key, workplace,
              role, department, mr_experience, label_ids_json, labels_json,
              is_active, global_scope_enabled, user_map_scope_enabled,
              is_admin, updated_at, roster_isolated_count,
              roster_issue_counts_json, roster_diagnostic_fingerprint,
              global_label_catalog_status, global_label_catalog_issues_json,
              user_map_label_catalog_status,
              user_map_label_catalog_issues_json
            FROM {self._view('user_scope')}
            WHERE snapshot_run_id = @published_run_id
            ORDER BY roster_id
            """,
            [
                bigquery.ScalarQueryParameter(
                    "published_run_id", "STRING", run_id
                )
            ],
            immutable=True,
        )

    def overview_events(
        self,
        *,
        window: MetricsTimeWindow,
        published_run_id: str | None,
        area_key: str = "",
    ) -> list[dict[str, Any]]:
        events_source, contract_parameters = self._events_source(
            published_run_id
        )
        sql = f"""
        SELECT *
        FROM {events_source}
        WHERE question_date BETWEEN @start_date AND @end_date
          AND question_ts >= @start_ts
          AND question_ts < @end_ts
          AND valid_question = TRUE
          AND (@area_key = '' OR area_key = @area_key)
        ORDER BY question_ts
        """
        return self._run(
            sql,
            [
                bigquery.ScalarQueryParameter("start_ts", "TIMESTAMP", window.start_utc),
                bigquery.ScalarQueryParameter("end_ts", "TIMESTAMP", window.end_utc),
                bigquery.ScalarQueryParameter("area_key", "STRING", area_key),
                *contract_parameters,
                *self._partition_parameters(window),
            ],
        )

    def activity_events(
        self,
        *,
        end: datetime,
        published_run_id: str | None,
        area_key: str = "",
    ) -> list[dict[str, Any]]:
        events_source, contract_parameters = self._events_source(
            published_run_id
        )
        sql = f"""
        SELECT roster_id, question_ts, question_date, area_key, area, role
        FROM {events_source}
        WHERE question_date BETWEEN @start_date AND @end_date
          AND question_ts >= @start_ts
          AND question_ts < @end_ts
          AND valid_question = TRUE
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
                *contract_parameters,
                *self._partition_parameters(window),
            ],
        )

    def user_metrics(
        self,
        *,
        window: MetricsTimeWindow,
        published_run_id: str | None,
    ) -> list[dict[str, Any]]:
        run_id = self._required_published_run_id(published_run_id)
        as_of = window.end_utc.astimezone(timezone.utc)
        parameters = [
            bigquery.ScalarQueryParameter(
                "history_start_date", "DATE", self._history_start_date()
            ),
            bigquery.ScalarQueryParameter("as_of", "TIMESTAMP", as_of),
            bigquery.ScalarQueryParameter(
                "published_run_id", "STRING", run_id
            ),
        ]
        source = (
            f"{self._view('dashboard_user_list_v2')}"
            "(@history_start_date, @as_of, @published_run_id)"
        )
        return self._run(
            f"SELECT * FROM {source} ORDER BY last_active_at DESC",
            parameters,
        )

    def user_detail_events(
        self,
        *,
        roster_id: str,
        window: MetricsTimeWindow,
        published_run_id: str | None,
    ) -> list[dict[str, Any]]:
        events_source, contract_parameters = self._events_source(
            published_run_id
        )
        sql = f"""
        SELECT *
        FROM {events_source}
        WHERE roster_id = @roster_id
          AND question_date BETWEEN @start_date AND @end_date
          AND question_ts >= @start_ts
          AND question_ts < @end_ts
          AND valid_question = TRUE
        ORDER BY question_ts
        """
        return self._run(
            sql,
            [
                bigquery.ScalarQueryParameter("roster_id", "STRING", roster_id),
                bigquery.ScalarQueryParameter("start_ts", "TIMESTAMP", window.start_utc),
                bigquery.ScalarQueryParameter("end_ts", "TIMESTAMP", window.end_utc),
                *contract_parameters,
                *self._partition_parameters(window),
            ],
        )
