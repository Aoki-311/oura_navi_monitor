from __future__ import annotations

import logging
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from google.api_core.exceptions import (
    BadRequest,
    DeadlineExceeded,
    Forbidden,
    GatewayTimeout,
    GoogleAPICallError,
    InternalServerError,
    NotFound,
    ServiceUnavailable,
    TooManyRequests,
)
from google.cloud import bigquery

from app.domain.analysis_scopes import AnalysisScope
from app.repositories.read_cache import PublishedReadCache
from app.settings import Settings
from app.time_window import MetricsTimeWindow


LOGGER = logging.getLogger(__name__)


class NewsUsageRepositoryError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class NewsUsageConfiguration:
    state: Literal["enabled", "disabled", "invalid"]
    source_service: str
    measurement_start_at: datetime | None
    error_code: str = ""


class NewsUsageRepository:
    """Read the independently published News usage snapshot and its bound roster."""

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self._settings = settings
        # News usage may intentionally be disabled. Do not initialize ADC or a
        # BigQuery transport until an enabled branch actually performs a read.
        self._client = client
        self._roster_cache = PublishedReadCache()
        self._dataset = f"{settings.monitor_project_id}.{settings.monitor_bq_dataset}"

    def _view(self, name: str) -> str:
        return f"`{self._dataset}.{name}`"

    def configuration(self) -> NewsUsageConfiguration:
        status = self._settings.news_usage_configuration_status
        source_service = str(
            self._settings.monitor_news_usage_source_service or ""
        ).strip()
        start_text = str(self._settings.monitor_news_usage_start_at or "").strip()
        if status == "disabled":
            return NewsUsageConfiguration(
                state="disabled", source_service="", measurement_start_at=None
            )
        if status != "enabled":
            return NewsUsageConfiguration(
                state="invalid",
                source_service=source_service,
                measurement_start_at=None,
                error_code="invalid_config",
            )
        try:
            parsed = datetime.fromisoformat(start_text.replace("Z", "+00:00"))
        except ValueError:
            return NewsUsageConfiguration(
                state="invalid",
                source_service=source_service,
                measurement_start_at=None,
                error_code="invalid_config",
            )
        if parsed.tzinfo is None:
            return NewsUsageConfiguration(
                state="invalid",
                source_service=source_service,
                measurement_start_at=None,
                error_code="invalid_config",
            )
        return NewsUsageConfiguration(
            state="enabled",
            source_service=source_service,
            measurement_start_at=parsed.astimezone(timezone.utc),
        )

    @staticmethod
    def _provider_error_code(error: GoogleAPICallError) -> str:
        if isinstance(error, NotFound):
            return "schema_unavailable"
        if isinstance(error, Forbidden):
            return "permission_denied"
        if isinstance(
            error,
            (
                DeadlineExceeded,
                GatewayTimeout,
                InternalServerError,
                ServiceUnavailable,
                TooManyRequests,
            ),
        ):
            return "provider_unavailable"
        if isinstance(error, BadRequest):
            messages = [str(error)]
            reasons: list[str] = []
            for detail in getattr(error, "errors", ()) or ():
                if isinstance(detail, dict):
                    messages.append(str(detail.get("message") or ""))
                    reasons.append(str(detail.get("reason") or "").lower())
            normalized = " ".join(messages).lower()
            if any(
                marker in normalized
                for marker in (
                    "unrecognized name",
                    "not found inside",
                    "no such field",
                    "cannot access field",
                )
            ):
                return "schema_unavailable"
            if any(
                marker in normalized
                for marker in (
                    "quota exceeded",
                    "rate limit exceeded",
                    "resources exceeded",
                    "query exceeded limit for bytes billed",
                    "billing tier limit exceeded",
                )
            ) or any(
                reason
                in {
                    "quotaexceeded",
                    "ratelimitexceeded",
                    "resourcesexceeded",
                    "billingtierlimitexceeded",
                }
                for reason in reasons
            ):
                return "provider_unavailable"
            return "query_invalid"
        return "provider_unavailable"

    def _query_config(
        self, parameters: list[bigquery.ScalarQueryParameter] | None = None
    ) -> bigquery.QueryJobConfig:
        return bigquery.QueryJobConfig(
            query_parameters=list(parameters or []),
            maximum_bytes_billed=max(
                1, int(self._settings.monitor_query_maximum_bytes)
            ),
            use_query_cache=True,
        )

    def _run(
        self, sql: str, parameters: list[bigquery.ScalarQueryParameter] | None = None
    ) -> list[dict[str, Any]]:
        values = list(parameters or [])
        if {item.name for item in values} == {"roster_snapshot_run_id"}:
            key = (sql, json.dumps(
                [item.to_api_repr() for item in values], sort_keys=True, default=str,
            ))
            return self._roster_cache.read(key, lambda: self._query(sql, values))
        # The published-events view follows a mutable pointer. In particular,
        # never retain an empty result caused by a concurrent pointer change,
        # or cache the confirmation read that detects that change.
        return self._query(sql, values)

    def _query(
        self, sql: str, parameters: list[bigquery.ScalarQueryParameter],
    ) -> list[dict[str, Any]]:
        if self._client is None:
            self._client = bigquery.Client(project=self._settings.monitor_project_id)
        try:
            rows = self._client.query(
                sql,
                job_config=self._query_config(parameters),
                location=self._settings.monitor_bq_location,
            ).result()
        except GoogleAPICallError as error:
            code = self._provider_error_code(error)
            LOGGER.warning(
                "News usage published source is unavailable",
                extra={
                    "monitor_metadata_component": "news_usage_reader",
                    "monitor_error_code": code,
                    "provider_exception_type": type(error).__name__,
                },
            )
            raise NewsUsageRepositoryError(code) from error
        return [dict(row.items()) if hasattr(row, "items") else dict(row) for row in rows]

    @staticmethod
    def _normalize_datetimes(row: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(row)
        for field in (
            "data_through",
            "measurement_start_at",
            "updated_at",
            "occurred_at",
            "source_ts",
            "materialized_at",
            "snapshot_created_at",
        ):
            value = normalized.get(field)
            if isinstance(value, datetime) and value.tzinfo is None:
                normalized[field] = value.replace(tzinfo=timezone.utc)
        return normalized

    def publication_snapshot(self, *, source_service: str) -> dict[str, Any]:
        rows = self._run(
            f"""
            SELECT
              source, status, data_through, published_run_id,
              roster_snapshot_run_id, measurement_start_at, source_service,
              scope_policy_version, global_roster_fingerprint,
              global_content_fingerprint, user_map_roster_fingerprint,
              user_map_content_fingerprint, updated_at
            FROM {self._view('news_usage_publication_state')}
            WHERE source = 'news_usage'
              AND status = 'succeeded'
              AND source_service = @source_service
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            [
                bigquery.ScalarQueryParameter(
                    "source_service", "STRING", source_service
                )
            ],
        )
        return self._normalize_datetimes(rows[0]) if rows else {}

    @staticmethod
    def _partition_dates(window: MetricsTimeWindow) -> tuple[date, date]:
        from zoneinfo import ZoneInfo

        local = ZoneInfo(window.timezone)
        return (
            window.start_utc.astimezone(local).date(),
            (window.end_utc - timedelta(microseconds=1)).astimezone(local).date(),
        )

    def published_events(
        self,
        *,
        window: MetricsTimeWindow,
        published_run_id: str,
        roster_snapshot_run_id: str,
        publication_data_through: datetime,
        source_service: str,
        scope: AnalysisScope = AnalysisScope.GLOBAL,
        roster_id: str = "",
        area_key: str = "",
    ) -> list[dict[str, Any]]:
        scope = AnalysisScope(scope)
        scope_column = (
            "global_scope_enabled" if scope is AnalysisScope.GLOBAL
            else "user_map_scope_enabled"
        )
        start_date, end_date = self._partition_dates(window)
        rows = self._run(
            f"""
            SELECT
              event.event_id, event.usage_event_id, event.page_view_id,
              event.event_name, event.channel, event.occurred_at,
              event.usage_date_jst, event.user_id, event.roster_id,
              event.roster_snapshot_run_id, event.content_event_id,
              event.content_event_version,
              JSON_VALUE(TO_JSON_STRING(event), '$.content_event_type') AS content_event_type,
              event.content_domain_key,
              event.content_geography_scope, event.content_source_id,
              event.content_category_key, event.source_catalog_version,
              event.filter_snapshot_present, event.filter_domain_keys,
              event.filter_source_ids, event.filter_category_keys,
              event.filter_event_types, event.filter_news_geography_scope,
              event.filter_start_date, event.filter_end_date,
              event.filter_has_query, event.changed_fields, event.surface,
              event.trigger, event.link_kind, event.operation_id, event.result,
              event.error_code, event.summary_date_jst,
              event.producer_revision, event.producer_git_sha,
              event.producer_build_id, event.source_service, event.source_ts,
              event.first_run_id, event.last_run_id, event.materialized_at,
              event.publication_run_id, event.publication_data_through
            FROM {self._view('news_usage_published_events')} AS event
            INNER JOIN {self._view('user_scope')} AS roster
              ON event.roster_snapshot_run_id = roster.snapshot_run_id
             AND event.roster_id = roster.roster_id
             AND roster.{scope_column} = TRUE
             AND roster.is_active = TRUE
            WHERE event.publication_run_id = @published_run_id
              AND event.publication_data_through = @publication_data_through
              AND event.roster_snapshot_run_id = @roster_snapshot_run_id
              AND event.source_service = @source_service
              AND event.usage_date_jst BETWEEN @start_date AND @end_date
              AND event.occurred_at >= @start_ts
              AND event.occurred_at < @end_ts
              AND (@roster_id = '' OR event.roster_id = @roster_id)
              AND (@area_key = '' OR roster.area_key = @area_key)
            ORDER BY event.occurred_at, event.event_id
            """,
            [
                bigquery.ScalarQueryParameter(
                    "published_run_id", "STRING", published_run_id
                ),
                bigquery.ScalarQueryParameter(
                    "roster_snapshot_run_id", "STRING", roster_snapshot_run_id
                ),
                bigquery.ScalarQueryParameter(
                    "publication_data_through",
                    "TIMESTAMP",
                    publication_data_through,
                ),
                bigquery.ScalarQueryParameter(
                    "source_service", "STRING", source_service
                ),
                bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
                bigquery.ScalarQueryParameter("end_date", "DATE", end_date),
                bigquery.ScalarQueryParameter(
                    "start_ts", "TIMESTAMP", window.start_utc
                ),
                bigquery.ScalarQueryParameter("end_ts", "TIMESTAMP", window.end_utc),
                bigquery.ScalarQueryParameter("roster_id", "STRING", roster_id),
                bigquery.ScalarQueryParameter("area_key", "STRING", area_key),
            ],
        )
        return [self._normalize_datetimes(row) for row in rows]

    def published_roster_snapshot(
        self, *, roster_snapshot_run_id: str
    ) -> list[dict[str, Any]]:
        rows = self._run(
            f"""
            SELECT
              snapshot_run_id, snapshot_created_at, roster_id, user_id,
              name, email, area, area_key, workplace, role, department,
              mr_experience, label_ids_json, is_active, global_scope_enabled,
              user_map_scope_enabled, is_admin, updated_at,
              roster_isolated_count, roster_issue_counts_json,
              roster_diagnostic_fingerprint
            FROM {self._view('user_scope')}
            WHERE snapshot_run_id = @roster_snapshot_run_id
            ORDER BY roster_id
            """,
            [
                bigquery.ScalarQueryParameter(
                    "roster_snapshot_run_id", "STRING", roster_snapshot_run_id
                )
            ],
        )
        return [self._normalize_datetimes(row) for row in rows]

    def unmatched_event_diagnostics(
        self,
        *,
        window: MetricsTimeWindow,
        publication_data_through: datetime,
    ) -> dict[str, Any]:
        """Read only a count; source identifiers and payloads never leave BigQuery."""

        try:
            rows = self._run(
                f"""
                SELECT COUNT(DISTINCT source_event_hash) AS unmatched_event_count
                FROM {self._view('news_usage_event_issues')}
                WHERE issue_code = 'source_event_without_roster'
                  AND resolution_status = 'open'
                  AND source_ts < @publication_data_through
                  AND event_ts >= @start_ts
                  AND event_ts < @end_ts
                """,
                [
                    bigquery.ScalarQueryParameter(
                        "publication_data_through",
                        "TIMESTAMP",
                        publication_data_through,
                    ),
                    bigquery.ScalarQueryParameter(
                        "start_ts", "TIMESTAMP", window.start_utc
                    ),
                    bigquery.ScalarQueryParameter(
                        "end_ts", "TIMESTAMP", window.end_utc
                    ),
                ],
            )
        except NewsUsageRepositoryError as error:
            return {
                "state": "unavailable",
                "unmatched_event_count": 0,
                "error_code": error.code,
            }
        return {
            "state": "available",
            "unmatched_event_count": int(
                (rows[0] if rows else {}).get("unmatched_event_count") or 0
            ),
            "error_code": "",
        }
