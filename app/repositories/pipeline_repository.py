from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

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

from app.settings import Settings


logger = logging.getLogger(__name__)


class PipelineRepository:
    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client or bigquery.Client(project=settings.monitor_project_id)
        dataset = f"{settings.monitor_project_id}.{settings.monitor_bq_dataset}"
        self._table = f"`{dataset}.pipeline_state`"
        self._runs_table = f"`{dataset}.pipeline_runs`"
        self._quality_table = f"`{dataset}.pipeline_quality_events`"
        self._issue_table = f"`{dataset}.pipeline_event_issues`"

    def _query_config(
        self,
        *,
        parameters: list[bigquery.ScalarQueryParameter] | None = None,
    ) -> bigquery.QueryJobConfig:
        return bigquery.QueryJobConfig(
            maximum_bytes_billed=max(
                1,
                int(self._settings.monitor_query_maximum_bytes),
            ),
            use_query_cache=True,
            query_parameters=list(parameters or []),
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
            # A rolling additive schema can make a newly referenced column or
            # field temporarily unavailable. Only that known provider shape is
            # recoverable; syntax and other invalid-query defects must surface.
            messages = [str(error)]
            reasons: list[str] = []
            for detail in getattr(error, "errors", ()) or ():
                if isinstance(detail, dict):
                    messages.append(str(detail.get("message") or ""))
                    reasons.append(str(detail.get("reason") or "").lower())
            normalized = " ".join(messages).lower()
            schema_markers = (
                "unrecognized name",
                "not found inside",
                "no such field",
                "cannot access field",
            )
            if any(marker in normalized for marker in schema_markers):
                return "schema_unavailable"
            provider_markers = (
                "quota exceeded",
                "rate limit exceeded",
                "resources exceeded",
                "query exceeded limit for bytes billed",
                "billing tier limit exceeded",
            )
            provider_reasons = {
                "quotaexceeded",
                "ratelimitexceeded",
                "resourcesexceeded",
                "billingtierlimitexceeded",
            }
            if any(marker in normalized for marker in provider_markers) or any(
                reason in provider_reasons for reason in reasons
            ):
                return "provider_unavailable"
            return "query_invalid"
        return "provider_unavailable"

    @staticmethod
    def _values(row: Any) -> dict[str, Any]:
        values = dict(row.items()) if hasattr(row, "items") else dict(row)
        for field in (
            "data_through",
            "updated_at",
            "latest_run_finished_at",
        ):
            value = values.get(field)
            if isinstance(value, datetime) and value.tzinfo is None:
                values[field] = value.replace(tzinfo=timezone.utc)
        return values

    def publication_snapshot(self) -> dict[str, Any]:
        """Return stable publication state plus independently available diagnostics.

        The published watermark is the authority that bounds visible facts. Optional
        quality tables must never be able to make that stable state unreadable during
        an additive schema rollout or a temporary diagnostics-provider failure.
        """

        snapshot: dict[str, Any] = {
            "publication_state_available": True,
            "publication_state_error_code": "",
        }
        try:
            publication_rows = list(
                self._client.query(
                    f"""
                    SELECT data_through, published_run_id, updated_at
                    FROM {self._table}
                    WHERE source = 'published' AND status = 'succeeded'
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    job_config=self._query_config(),
                    location=self._settings.monitor_bq_location,
                ).result()
            )
        except GoogleAPICallError as error:
            error_code = self._provider_error_code(error)
            logger.warning(
                "Monitor publication state is unavailable",
                extra={
                    "monitor_metadata_component": "publication_state",
                    "monitor_error_code": error_code,
                    "provider_exception_type": type(error).__name__,
                },
            )
            return {
                "publication_state_available": False,
                "publication_state_error_code": error_code,
                "quality_diagnostics_available": False,
                "quality_diagnostics_error_code": "publication_state_unavailable",
            }

        if publication_rows:
            snapshot.update(self._values(publication_rows[0]))

        published_run_id = str(snapshot.get("published_run_id") or "")
        published_updated_at = snapshot.get("updated_at")
        try:
            diagnostic_rows = list(
                self._client.query(
                    f"""
                    WITH latest_run AS (
                  SELECT run_id, status, error_code, started_at, finished_at
                  FROM {self._runs_table}
                  WHERE DATE(started_at) BETWEEN
                    DATE_SUB(CURRENT_DATE(), INTERVAL 2 DAY) AND CURRENT_DATE()
                    AND source = 'published'
                  ORDER BY started_at DESC
                  LIMIT 1
                )
                SELECT
                  latest_run.run_id AS latest_run_id,
                  latest_run.status AS latest_run_status,
                  latest_run.error_code AS latest_run_error_code,
                  latest_run.finished_at AS latest_run_finished_at,
                  COALESCE((
                    SELECT COUNT(DISTINCT issue.source_event_hash)
                    FROM {self._issue_table} issue
                    WHERE issue.last_run_id = @published_run_id
                      AND issue.disposition = 'row_quarantined'
                      AND issue.resolution_status = 'open'
                  ), 0) AS quarantined_event_count,
                  COALESCE((
                    SELECT COUNT(DISTINCT issue.source_event_hash)
                    FROM {self._issue_table} issue
                    WHERE issue.last_run_id = @published_run_id
                      AND issue.issue_code = 'duplicate_delivery_deduplicated'
                  ), 0) AS deduplicated_delivery_count,
                  COALESCE((
                    SELECT SUM(quality.failure_count)
                    FROM {self._quality_table} quality
                    WHERE quality.run_id = @published_run_id
                      AND quality.disposition = 'repaired'
                      AND @published_updated_at IS NOT NULL
                      AND DATE(quality.observed_at) BETWEEN
                        DATE_SUB(DATE(@published_updated_at), INTERVAL 1 DAY)
                        AND DATE_ADD(DATE(@published_updated_at), INTERVAL 1 DAY)
                  ), 0) AS repaired_duplicate_fact_count,
                  COALESCE((
                    SELECT SUM(quality.failure_count)
                    FROM {self._quality_table} quality
                    WHERE quality.run_id = @published_run_id
                      AND quality.disposition = 'axis_unmeasured'
                      AND @published_updated_at IS NOT NULL
                      AND DATE(quality.observed_at) BETWEEN
                        DATE_SUB(DATE(@published_updated_at), INTERVAL 1 DAY)
                        AND DATE_ADD(DATE(@published_updated_at), INTERVAL 1 DAY)
                  ), 0) AS axis_unmeasured_finding_count,
                  COALESCE((
                    SELECT SUM(quality.failure_count)
                    FROM {self._quality_table} quality
                    WHERE quality.run_id = latest_run.run_id
                      AND quality.disposition = 'batch_blocking'
                      AND quality.passed IS NOT TRUE
                      AND DATE(quality.observed_at) BETWEEN
                        DATE_SUB(DATE(latest_run.started_at), INTERVAL 1 DAY)
                        AND DATE_ADD(DATE(latest_run.started_at), INTERVAL 1 DAY)
                  ), 0) AS batch_blocking_failure_count
                FROM (SELECT 1) anchor
                LEFT JOIN latest_run ON TRUE
                """,
                    job_config=self._query_config(
                        parameters=[
                            bigquery.ScalarQueryParameter(
                                "published_run_id",
                                "STRING",
                                published_run_id,
                            ),
                            bigquery.ScalarQueryParameter(
                                "published_updated_at",
                                "TIMESTAMP",
                                published_updated_at,
                            ),
                        ]
                    ),
                    location=self._settings.monitor_bq_location,
                ).result()
            )
        except GoogleAPICallError as error:
            error_code = self._provider_error_code(error)
            logger.warning(
                "Monitor pipeline diagnostics are unavailable; preserving published facts",
                extra={
                    "monitor_metadata_component": "pipeline_diagnostics",
                    "monitor_error_code": error_code,
                    "provider_exception_type": type(error).__name__,
                },
            )
            snapshot.update(
                {
                    "quality_diagnostics_available": False,
                    "quality_diagnostics_error_code": error_code,
                }
            )
            return snapshot

        snapshot.update(
            {
                "quality_diagnostics_available": True,
                "quality_diagnostics_error_code": "",
            }
        )
        if diagnostic_rows:
            snapshot.update(self._values(diagnostic_rows[0]))
        return snapshot

    def data_through(self) -> datetime | None:
        value = self.publication_snapshot().get("data_through")
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return None
