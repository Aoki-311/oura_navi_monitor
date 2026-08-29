from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from google.cloud import bigquery

from app.jobs.project_firestore import (
    CITATION_SCHEMA,
    CONVERSATION_SCHEMA,
    USER_SCOPE_SCHEMA,
    FirestoreProjector,
    struct_array_parameter,
)
from app.settings import Settings, get_settings

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
_UNSET = object()
_TRIGGER_SOURCES = {"manual", "manual_backfill", "scheduler_three_hour"}


class LeaseUnavailableError(RuntimeError):
    """Another execution owns the only canonical publisher lease."""


class PublicationOutcomeUnknownError(RuntimeError):
    """The publish transport failed and its atomic commit cannot be read back."""


class DataQualityGateError(RuntimeError):
    """Canonical facts were rolled back while quality diagnostics were retained."""

    def __init__(
        self,
        *,
        run_id: str,
        checks: list[dict[str, Any]],
    ) -> None:
        super().__init__("canonical monitor batch integrity gate failed")
        self.run_id = run_id
        self.checks = checks


def render_sql(name: str, settings: Settings) -> str:
    text = (SQL_DIR / name).read_text(encoding="utf-8")
    values = {
        "PROJECT_ID": settings.monitor_project_id,
        "DATASET_ID": settings.monitor_bq_dataset,
        "BQ_LOCATION": settings.monitor_bq_location,
        "MONITOR_TIMEZONE": settings.monitor_timezone,
        "SOURCE_SERVICE": settings.monitor_source_service,
    }
    for key, value in values.items():
        text = text.replace("${" + key + "}", str(value))
    if "${" in text:
        raise ValueError(f"unresolved SQL placeholder in {name}")
    return text


def render_publish_sql(settings: Settings) -> str:
    """Atomic fact publication, quality ledger, lease CAS and watermark advance."""

    projection_sql = render_sql("merge_firestore_projection.sql", settings)
    fact_sql = render_sql("merge_incremental.sql", settings)
    quality_sql = render_sql("check_data_quality.sql", settings).rstrip().removesuffix(";")
    state = f"`{settings.monitor_project_id}.{settings.monitor_bq_dataset}.pipeline_state`"
    runs = f"`{settings.monitor_project_id}.{settings.monitor_bq_dataset}.pipeline_runs`"
    ledger = f"`{settings.monitor_project_id}.{settings.monitor_bq_dataset}.pipeline_quality_events`"
    return f"""
DECLARE event_partition_start DATE DEFAULT DATE(@window_start, '{settings.monitor_timezone}');
DECLARE event_partition_end DATE DEFAULT DATE(@window_end, '{settings.monitor_timezone}');
DECLARE persistence_partition_start DATE DEFAULT event_partition_start;
DECLARE persistence_partition_end DATE DEFAULT event_partition_end;
DECLARE affected_answer_partition_start DATE DEFAULT event_partition_start;
DECLARE affected_answer_partition_end DATE DEFAULT event_partition_end;
DECLARE run_quality_results ARRAY<STRUCT<
  check_name STRING,
  disposition STRING,
  severity STRING,
  failure_count INT64,
  passed BOOL
>> DEFAULT [];
BEGIN TRANSACTION;
ASSERT EXISTS (
  SELECT 1 FROM {state}
  WHERE source = 'published'
    AND lease_run_id = @lease_id
    AND lease_expires_at > CURRENT_TIMESTAMP()
    AND (
      (@expected_watermark IS NULL AND data_through IS NULL)
      OR data_through = @expected_watermark
    )
) AS 'publisher lease or expected watermark changed';
ASSERT (@expected_watermark IS NULL OR @window_end > @expected_watermark)
  AS 'published watermark must advance strictly';
{projection_sql}
{fact_sql}
SET run_quality_results = ARRAY(
  SELECT AS STRUCT
    CAST(check_name AS STRING) AS check_name,
    CAST(disposition AS STRING) AS disposition,
    CAST(severity AS STRING) AS severity,
    CAST(failure_count AS INT64) AS failure_count,
    CAST(passed AS BOOL) AS passed
  FROM ({quality_sql})
);
IF EXISTS (
  SELECT 1
  FROM UNNEST(run_quality_results)
  WHERE disposition = 'batch_blocking' AND passed IS NOT TRUE
) THEN
  ROLLBACK TRANSACTION;
  DELETE FROM {ledger} WHERE run_id = @run_id;
  INSERT INTO {ledger} (
    run_id, window_start, window_end, check_name, disposition, severity,
    failure_count, passed, observed_at
  )
  SELECT
    @run_id, @window_start, @window_end, check_name, disposition, severity,
    failure_count, passed, CURRENT_TIMESTAMP()
  FROM UNNEST(run_quality_results);
  UPDATE {runs}
  SET status = 'failed',
      finished_at = CURRENT_TIMESTAMP(),
      error_code = 'DataQualityGateError'
  WHERE run_id = @run_id
    AND status = 'running'
    AND DATE(started_at) BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL 2 DAY)
      AND CURRENT_DATE();
ELSE
  DELETE FROM {ledger} WHERE run_id = @run_id;
  INSERT INTO {ledger} (
    run_id, window_start, window_end, check_name, disposition, severity,
    failure_count, passed, observed_at
  )
  SELECT
    @run_id, @window_start, @window_end, check_name, disposition, severity,
    failure_count, passed, CURRENT_TIMESTAMP()
  FROM UNNEST(run_quality_results);
  UPDATE {state}
  SET data_through = @window_end,
      published_run_id = @run_id,
      status = 'succeeded',
      updated_at = CURRENT_TIMESTAMP(),
      lease_run_id = NULL,
      lease_acquired_at = NULL,
      lease_expires_at = NULL
  WHERE source = 'published'
    AND lease_run_id = @lease_id
    AND (
      (@expected_watermark IS NULL AND data_through IS NULL)
      OR data_through = @expected_watermark
    );
  ASSERT @@row_count = 1 AS 'published watermark compare-and-set failed';
  UPDATE {runs}
  SET status = 'succeeded',
      finished_at = CURRENT_TIMESTAMP(),
      input_rows = (SELECT COUNT(*) FROM _run_monitor_source),
      merged_rows = (SELECT COUNT(*) FROM _run_admissible_monitor_events),
      duplicate_rows = (
        SELECT COUNT(*)
        FROM _run_event_issues
        WHERE issue_code IN (
          'duplicate_delivery_deduplicated',
          'conflicting_duplicate_event_id'
        )
      )
  WHERE run_id = @run_id
    AND DATE(started_at) BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL 2 DAY)
      AND CURRENT_DATE();
  ASSERT @@row_count = 1 AS 'pipeline run completion row is missing';
  COMMIT TRANSACTION;
END IF;
SELECT check_name, failure_count, disposition, severity, passed
FROM UNNEST(run_quality_results)
ORDER BY check_name;
""".strip()


def _parse_start(value: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("MONITOR_ANALYTICS_START_AT is required before the first refresh")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _as_utc_datetime(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    resolved = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return resolved.astimezone(timezone.utc)


def _row_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    items = getattr(row, "items", None)
    return dict(items()) if callable(items) else {}


def _failed_quality_checks(result: dict[str, Any]) -> list[dict[str, Any]]:
    run = result.get("lastRun") if isinstance(result.get("lastRun"), dict) else result
    checks = run.get("dataQualityChecks") if isinstance(run, dict) else []
    if not isinstance(checks, list):
        return []
    failures: list[dict[str, Any]] = []
    for item in checks:
        if not isinstance(item, dict):
            continue
        try:
            failure_count = int(item.get("failure_count") or 0)
        except (TypeError, ValueError):
            continue
        if failure_count <= 0:
            continue
        failures.append(
            {
                "monitor_pipeline_quality_event": True,
                "check_name": str(item.get("check_name") or "")[:100],
                "disposition": str(item.get("disposition") or "")[:40],
                "severity": str(item.get("severity") or "")[:40],
                "failure_count": failure_count,
                "run_id": str(run.get("runId") or "")[:100],
            }
        )
    return failures


def _has_blocking_quality_failure(checks: list[dict[str, Any]]) -> bool:
    return any(
        str(item.get("disposition") or "") == "batch_blocking"
        and item.get("passed") is not True
        for item in checks
        if isinstance(item, dict)
    )


def _render_begin_run_sql(dataset: str) -> str:
    """Create or reset one recent run without an unbounded MERGE target scan."""

    return f"""
DECLARE matched_run_count INT64 DEFAULT 0;
BEGIN TRANSACTION;
UPDATE `{dataset}.pipeline_runs`
SET finished_at = NULL,
    execution_id = @execution_id,
    trigger_source = @trigger_source,
    window_start = @window_start,
    window_end = @window_end,
    source = 'published',
    status = 'running',
    error_code = NULL
WHERE run_id = @run_id
  AND started_at >= @run_partition_start
  AND started_at < @run_partition_end;
SET matched_run_count = @@row_count;
ASSERT matched_run_count <= 1 AS 'duplicate pipeline run identity';
IF matched_run_count = 0 THEN
  INSERT INTO `{dataset}.pipeline_runs` (
    run_id, execution_id, trigger_source, started_at, window_start, window_end,
    source, status
  ) VALUES (
    @run_id, @execution_id, @trigger_source, @run_started_at, @window_start,
    @window_end, 'published', 'running'
  );
END IF;
COMMIT TRANSACTION;
""".strip()


class AnalyticsRefreshJob:
    def __init__(
        self,
        settings: Settings,
        *,
        client: Any | None = None,
        projector: FirestoreProjector | None = None,
        execution_id: str = "",
        trigger_source: str = "manual",
    ) -> None:
        self._settings = settings
        self._client = client or bigquery.Client(project=settings.monitor_project_id)
        self._projector = projector or FirestoreProjector(settings)
        self._dataset = f"{settings.monitor_project_id}.{settings.monitor_bq_dataset}"
        runtime_execution = str(
            execution_id
            or os.getenv("CLOUD_RUN_EXECUTION", "")
            or os.getenv("MONITOR_REFRESH_EXECUTION_ID", "")
        ).strip()
        self._execution_id = runtime_execution or f"local-{uuid4().hex}"
        normalized_trigger_source = str(trigger_source or "manual").strip().lower()
        if normalized_trigger_source not in _TRIGGER_SOURCES:
            raise ValueError("unsupported refresh trigger source")
        self._trigger_source = normalized_trigger_source

    def _lease_id(self) -> str:
        digest = hashlib.sha256(
            f"{self._execution_id}:publisher-lease".encode("utf-8")
        ).hexdigest()[:32]
        return f"lease_{digest}"

    def _run_id(
        self,
        *,
        sequence: int,
        expected_watermark: datetime | None,
        window_end: datetime,
    ) -> str:
        watermark_token = expected_watermark.isoformat() if expected_watermark else "initial"
        digest = hashlib.sha256(
            (
                f"{self._execution_id}:{max(0, int(sequence))}:"
                f"{watermark_token}:{window_end.isoformat()}"
            ).encode("utf-8")
        ).hexdigest()[:32]
        return f"refresh_{digest}"

    def _query(self, sql: str, parameters: list[Any]) -> Any:
        config = bigquery.QueryJobConfig(
            query_parameters=parameters,
            maximum_bytes_billed=self._settings.monitor_query_maximum_bytes,
            use_query_cache=False,
        )
        return self._client.query(
            sql,
            job_config=config,
            location=self._settings.monitor_bq_location,
        ).result()

    def _read_watermark(self) -> datetime | None:
        rows = list(
            self._query(
                f"SELECT MAX(data_through) AS data_through FROM `{self._dataset}.pipeline_state` "
                "WHERE source = 'published' AND status = 'succeeded'",
                [],
            )
        )
        return _as_utc_datetime(_row_dict(rows[0]).get("data_through")) if rows else None

    def window(
        self,
        *,
        now: datetime | None = None,
        watermark: datetime | None | object = _UNSET,
    ) -> tuple[datetime, datetime]:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        target_end = current - timedelta(minutes=self._settings.monitor_refresh_delay_minutes)
        resolved_watermark = self._read_watermark() if watermark is _UNSET else _as_utc_datetime(watermark)
        analytics_start = _parse_start(self._settings.monitor_analytics_start_at)
        base = resolved_watermark or analytics_start
        window_start = max(
            analytics_start,
            base - timedelta(minutes=self._settings.monitor_refresh_overlap_minutes),
        )
        window_end = min(
            target_end,
            window_start
            + timedelta(hours=max(1, int(self._settings.monitor_refresh_max_window_hours))),
        )
        if window_end <= window_start:
            raise ValueError("refresh window is empty")
        return window_start, window_end

    def _acquire_lease(self, lease_id: str) -> dict[str, Any]:
        rows = list(
            self._query(
                f"""
MERGE `{self._dataset}.pipeline_state` target
USING (SELECT 'published' AS source) incoming
ON target.source = incoming.source
WHEN MATCHED AND (
  target.lease_run_id IS NULL
  OR target.lease_expires_at <= CURRENT_TIMESTAMP()
  OR target.lease_run_id = @lease_id
) THEN UPDATE SET
  lease_run_id = @lease_id,
  lease_acquired_at = IF(target.lease_run_id = @lease_id, target.lease_acquired_at, CURRENT_TIMESTAMP()),
  lease_expires_at = TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL @lease_ttl_minutes MINUTE)
WHEN NOT MATCHED THEN INSERT (
  source, data_through, published_run_id, status, updated_at,
  lease_run_id, lease_acquired_at, lease_expires_at
) VALUES (
  'published', NULL, NULL, 'never_published', CURRENT_TIMESTAMP(),
  @lease_id, CURRENT_TIMESTAMP(),
  TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL @lease_ttl_minutes MINUTE)
);
SELECT
  lease_run_id = @lease_id AS acquired,
  data_through,
  lease_run_id,
  lease_expires_at
FROM `{self._dataset}.pipeline_state`
WHERE source = 'published';
""".strip(),
                [
                    bigquery.ScalarQueryParameter("lease_id", "STRING", lease_id),
                    bigquery.ScalarQueryParameter(
                        "lease_ttl_minutes",
                        "INT64",
                        max(1, int(self._settings.monitor_refresh_lease_ttl_minutes)),
                    ),
                ],
            )
        )
        row = _row_dict(rows[0]) if rows else {}
        if row.get("acquired") is not True:
            raise LeaseUnavailableError("canonical publisher lease is already owned")
        return row

    def _renew_lease(self, lease_id: str) -> None:
        rows = list(
            self._query(
                f"""
UPDATE `{self._dataset}.pipeline_state`
SET lease_expires_at = TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL @lease_ttl_minutes MINUTE)
WHERE source = 'published' AND lease_run_id = @lease_id;
SELECT COUNTIF(lease_run_id = @lease_id AND lease_expires_at > CURRENT_TIMESTAMP()) = 1 AS renewed
FROM `{self._dataset}.pipeline_state`
WHERE source = 'published';
""".strip(),
                [
                    bigquery.ScalarQueryParameter("lease_id", "STRING", lease_id),
                    bigquery.ScalarQueryParameter(
                        "lease_ttl_minutes",
                        "INT64",
                        max(1, int(self._settings.monitor_refresh_lease_ttl_minutes)),
                    ),
                ],
            )
        )
        if not rows or _row_dict(rows[0]).get("renewed") is not True:
            raise RuntimeError("canonical publisher lease was lost")

    def _release_lease(self, lease_id: str) -> None:
        self._query(
            f"""UPDATE `{self._dataset}.pipeline_state`
            SET lease_run_id = NULL, lease_acquired_at = NULL, lease_expires_at = NULL
            WHERE source = 'published' AND lease_run_id = @lease_id""",
            [bigquery.ScalarQueryParameter("lease_id", "STRING", lease_id)],
        )

    def _read_publication_receipt(
        self,
        *,
        run_id: str,
        expected_window_end: datetime,
    ) -> dict[str, Any]:
        rows = list(
            self._query(
                f"""
SELECT
  state.data_through,
  state.published_run_id,
  state.status AS state_status,
  @expected_window_end AS expected_window_end,
  (
    SELECT run.status
    FROM `{self._dataset}.pipeline_runs` run
    WHERE run.run_id = @run_id
      AND DATE(run.started_at) BETWEEN
        DATE_SUB(CURRENT_DATE(), INTERVAL 2 DAY) AND CURRENT_DATE()
    ORDER BY run.started_at DESC
    LIMIT 1
  ) AS run_status
FROM `{self._dataset}.pipeline_state` state
WHERE state.source = 'published'
LIMIT 1;
""".strip(),
                [
                    bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
                    bigquery.ScalarQueryParameter(
                        "expected_window_end", "TIMESTAMP", expected_window_end
                    ),
                ],
            )
        )
        return _row_dict(rows[0]) if rows else {}

    def _read_quality_checks(self, run_id: str) -> list[dict[str, Any]]:
        return [
            _row_dict(row)
            for row in self._query(
                f"""
SELECT check_name, failure_count, disposition, severity, passed
FROM `{self._dataset}.pipeline_quality_events`
WHERE run_id = @run_id
  AND observed_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 2 DAY)
ORDER BY check_name
""".strip(),
                [bigquery.ScalarQueryParameter("run_id", "STRING", run_id)],
            )
        ]

    @staticmethod
    def _publication_is_committed(
        receipt: dict[str, Any],
        *,
        run_id: str,
        expected_window_end: datetime,
    ) -> bool:
        return (
            str(receipt.get("published_run_id") or "") == run_id
            and str(receipt.get("state_status") or "") == "succeeded"
            and str(receipt.get("run_status") or "") == "succeeded"
            and _as_utc_datetime(receipt.get("data_through"))
            == expected_window_end.astimezone(timezone.utc)
        )

    def _begin_run(
        self,
        *,
        run_id: str,
        window_start: datetime,
        window_end: datetime,
    ) -> None:
        run_started_at = datetime.now(timezone.utc)
        partition_day = run_started_at.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        self._query(
            _render_begin_run_sql(self._dataset),
            [
                bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
                bigquery.ScalarQueryParameter(
                    "execution_id", "STRING", self._execution_id
                ),
                bigquery.ScalarQueryParameter(
                    "trigger_source", "STRING", self._trigger_source
                ),
                bigquery.ScalarQueryParameter(
                    "run_started_at",
                    "TIMESTAMP",
                    run_started_at,
                ),
                bigquery.ScalarQueryParameter(
                    "run_partition_start",
                    "TIMESTAMP",
                    partition_day - timedelta(days=2),
                ),
                bigquery.ScalarQueryParameter(
                    "run_partition_end",
                    "TIMESTAMP",
                    partition_day + timedelta(days=1),
                ),
                bigquery.ScalarQueryParameter("window_start", "TIMESTAMP", window_start),
                bigquery.ScalarQueryParameter("window_end", "TIMESTAMP", window_end),
            ],
        )

    def _mark_failed(self, run_id: str, exc: Exception) -> None:
        self._query(
            f"""UPDATE `{self._dataset}.pipeline_runs`
            SET status = 'failed', finished_at = CURRENT_TIMESTAMP(), error_code = @error_code
            WHERE run_id = @run_id
              AND status = 'running'
              AND DATE(started_at) BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL 2 DAY) AND CURRENT_DATE()""",
            [
                bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
                bigquery.ScalarQueryParameter("error_code", "STRING", type(exc).__name__),
            ],
        )

    def run(
        self,
        *,
        now: datetime | None = None,
        sequence: int = 0,
    ) -> dict[str, Any]:
        frozen_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        lease_id = self._lease_id()
        lease = self._acquire_lease(lease_id)
        try:
            watermark = _as_utc_datetime(lease.get("data_through"))
            target_end = frozen_now - timedelta(
                minutes=self._settings.monitor_refresh_delay_minutes
            )
            if watermark is not None and watermark >= target_end:
                no_op_run_id = self._run_id(
                    sequence=sequence,
                    expected_watermark=watermark,
                    window_end=target_end,
                )
                self._release_lease(lease_id)
                return {
                    "runId": no_op_run_id,
                    "status": "up_to_date",
                    "dataThrough": watermark.isoformat(),
                }

            window_start, window_end = self.window(
                now=frozen_now,
                watermark=watermark,
            )
            run_id = self._run_id(
                sequence=sequence,
                expected_watermark=watermark,
                window_end=window_end,
            )
        except Exception:
            try:
                self._release_lease(lease_id)
            except Exception:
                pass
            raise
        run_started = False
        try:
            self._begin_run(
                run_id=run_id,
                window_start=window_start,
                window_end=window_end,
            )
            run_started = True
            matched_users = self._projector.resolve_chat_identities()
            scope_rows = self._projector.user_scope_rows()
            if not scope_rows:
                raise RuntimeError("canonical user scope is empty")
            conversation_rows, citation_rows, projection_issues = (
                self._projector.changed_conversation_rows(
                    window_start=window_start,
                    window_end=window_end,
                )
            )
            self._renew_lease(lease_id)
            fallback_start = window_start.date()
            fallback_end = window_end.date()
            conversation_dates = [row["updated_date"] for row in conversation_rows]
            citation_dates = [row["answer_date"] for row in citation_rows]
            publish_parameters = [
                bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
                bigquery.ScalarQueryParameter("lease_id", "STRING", lease_id),
                bigquery.ScalarQueryParameter("expected_watermark", "TIMESTAMP", watermark),
                bigquery.ScalarQueryParameter(
                    "conversation_partition_start",
                    "DATE",
                    min(conversation_dates, default=fallback_start),
                ),
                bigquery.ScalarQueryParameter(
                    "conversation_partition_end",
                    "DATE",
                    max(conversation_dates, default=fallback_end),
                ),
                bigquery.ScalarQueryParameter(
                    "citation_partition_start",
                    "DATE",
                    min(citation_dates, default=fallback_start),
                ),
                bigquery.ScalarQueryParameter(
                    "citation_partition_end",
                    "DATE",
                    max(citation_dates, default=fallback_end),
                ),
                struct_array_parameter("user_scope_rows", USER_SCOPE_SCHEMA, scope_rows),
                struct_array_parameter(
                    "conversation_rows", CONVERSATION_SCHEMA, conversation_rows
                ),
                struct_array_parameter("citation_rows", CITATION_SCHEMA, citation_rows),
                bigquery.ScalarQueryParameter("window_start", "TIMESTAMP", window_start),
                bigquery.ScalarQueryParameter("window_end", "TIMESTAMP", window_end),
                bigquery.ScalarQueryParameter(
                    "analytics_start",
                    "TIMESTAMP",
                    _parse_start(self._settings.monitor_analytics_start_at),
                ),
                bigquery.ScalarQueryParameter(
                    "event_future_tolerance_minutes",
                    "INT64",
                    max(
                        0,
                        int(
                            self._settings.monitor_event_future_tolerance_minutes
                        ),
                    ),
                ),
            ]
            publication_outcome = "query_result"
            try:
                checks = [
                    _row_dict(row)
                    for row in self._query(
                        render_publish_sql(self._settings),
                        publish_parameters,
                    )
                ]
            except Exception as publish_exc:
                try:
                    receipt = self._read_publication_receipt(
                        run_id=run_id,
                        expected_window_end=window_end,
                    )
                except Exception as receipt_exc:
                    raise PublicationOutcomeUnknownError(
                        "publish result is unknown; preserve the lease until expiry"
                    ) from receipt_exc
                if not self._publication_is_committed(
                    receipt,
                    run_id=run_id,
                    expected_window_end=window_end,
                ):
                    try:
                        retained_checks = self._read_quality_checks(run_id)
                    except Exception:
                        retained_checks = []
                    if _has_blocking_quality_failure(retained_checks):
                        raise DataQualityGateError(
                            run_id=run_id,
                            checks=retained_checks,
                        ) from publish_exc
                    raise publish_exc
                checks = []
                publication_outcome = "reconciled_after_transport_error"
            if _has_blocking_quality_failure(checks):
                raise DataQualityGateError(run_id=run_id, checks=checks)
            return {
                "runId": run_id,
                "status": "succeeded",
                "windowStart": window_start.isoformat(),
                "windowEnd": window_end.isoformat(),
                "matchedUsers": matched_users,
                "scopeRows": len(scope_rows),
                "conversationRows": len(conversation_rows),
                "citationRows": len(citation_rows),
                "projectionIssues": projection_issues,
                "dataQualityChecks": checks,
                "publicationOutcome": publication_outcome,
            }
        except PublicationOutcomeUnknownError:
            # A commit may already be durable. Updating the run or releasing the
            # lease here could overwrite truthful state, so preserve both for a
            # later receipt audit and let the bounded lease expire naturally.
            raise
        except Exception as exc:
            if run_started:
                try:
                    self._mark_failed(run_id, exc)
                except Exception:
                    pass
            try:
                self._release_lease(lease_id)
            except Exception:
                pass
            raise

    def run_until_current(
        self,
        *,
        now: datetime | None = None,
        max_runs: int = 1000,
    ) -> dict[str, Any]:
        frozen_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        target_end = frozen_now - timedelta(
            minutes=self._settings.monitor_refresh_delay_minutes
        )
        runs: list[dict[str, Any]] = []
        for sequence in range(max(1, int(max_runs))):
            result = self.run(now=frozen_now, sequence=sequence)
            if result.get("status") == "up_to_date":
                return {
                    "status": "succeeded",
                    "runCount": len(runs),
                    "windowStart": runs[0]["windowStart"] if runs else "",
                    "windowEnd": runs[-1]["windowEnd"] if runs else result["dataThrough"],
                    "lastRun": runs[-1] if runs else result,
                }
            runs.append(result)
            published_end = datetime.fromisoformat(
                str(result["windowEnd"]).replace("Z", "+00:00")
            )
            if published_end >= target_end:
                return {
                    "status": "succeeded",
                    "runCount": len(runs),
                    "windowStart": runs[0]["windowStart"],
                    "windowEnd": runs[-1]["windowEnd"],
                    "lastRun": runs[-1],
                }
        raise RuntimeError("incremental refresh did not reach the frozen target")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the single OurA Navi Monitor incremental pipeline"
    )
    parser.add_argument("--apply", action="store_true", help="execute cloud mutations")
    parser.add_argument(
        "--until-current",
        action="store_true",
        help="advance bounded windows until the frozen current target is published",
    )
    parser.add_argument(
        "--trigger-source",
        choices=sorted(_TRIGGER_SOURCES),
        default="manual",
        help="closed provenance for scheduler, controlled backfill or manual runs",
    )
    parser.add_argument(
        "--target-at",
        default="",
        help="immutable ISO-8601 UTC target used only by a controlled backfill",
    )
    args = parser.parse_args()
    settings = get_settings()
    if not args.apply:
        print(
            json.dumps(
                {
                    "mode": "plan",
                    "project": settings.monitor_project_id,
                    "dataset": settings.monitor_bq_dataset,
                },
                sort_keys=True,
            )
        )
        return 0
    try:
        target_at = None
        if args.target_at:
            if args.trigger_source != "manual_backfill" or not args.until_current:
                raise ValueError(
                    "--target-at requires --until-current and manual_backfill"
                )
            target_at = _parse_start(args.target_at)
        job = AnalyticsRefreshJob(settings, trigger_source=args.trigger_source)
        result = (
            job.run_until_current(now=target_at)
            if args.until_current
            else job.run(now=target_at)
        )
    except LeaseUnavailableError:
        print(
            json.dumps(
                {
                    "monitor_pipeline_event": True,
                    "status": "skipped_locked",
                    "error_code": "publisher_lease_unavailable",
                    "event_ts": datetime.now(timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z"),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 3
    except Exception as exc:
        if isinstance(exc, DataQualityGateError):
            for quality_event in _failed_quality_checks(
                {
                    "runId": exc.run_id,
                    "dataQualityChecks": exc.checks,
                }
            ):
                print(json.dumps(quality_event, sort_keys=True), flush=True)
        print(
            json.dumps(
                {
                    "monitor_pipeline_event": True,
                    "status": "failed",
                    "error_code": type(exc).__name__,
                    "event_ts": datetime.now(timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z"),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        raise
    for quality_event in _failed_quality_checks(result):
        print(json.dumps(quality_event, sort_keys=True), flush=True)
    print(
        json.dumps({"monitor_pipeline_event": True, **result}, sort_keys=True),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
