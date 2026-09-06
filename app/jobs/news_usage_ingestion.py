"""Independent News/Society usage ingestion inside the existing refresh Job.

The Chat publisher remains the first, unchanged owner.  This module uses a
separate ``pipeline_state`` row and a separate transaction, so a usage-source
failure is retryable without rolling Chat back or borrowing Chat's cursor.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from google.cloud import bigquery

from app.refresh_policy import REFRESH_POLICY
from app.settings import Settings


SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
NEWS_USAGE_STATE_SOURCE = "news_usage"
NEWS_USAGE_CONTRACT_VERSION = "news_usage_v1"
_SOURCE_SERVICE_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_UNSET = object()


class NewsUsageLeaseUnavailableError(RuntimeError):
    """Another execution owns the independent usage publisher lease."""


class NewsUsagePublicationOutcomeUnknownError(RuntimeError):
    """The usage commit may be durable but its receipt cannot be read."""


def _as_utc_datetime(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    resolved = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return resolved.astimezone(timezone.utc)


def _parse_utc(value: str, *, field_name: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _row_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    items = getattr(row, "items", None)
    return dict(items()) if callable(items) else {}


def news_usage_configuration_status(settings: Settings) -> str:
    """Return disabled/enabled/invalid without making global Settings fragile."""

    return settings.news_usage_configuration_status


def _validated_configuration(settings: Settings) -> tuple[str, datetime]:
    if news_usage_configuration_status(settings) != "enabled":
        raise ValueError("news usage configuration is incomplete")
    service = str(settings.monitor_news_usage_source_service).strip()
    if not _SOURCE_SERVICE_PATTERN.fullmatch(service):
        raise ValueError("MONITOR_NEWS_USAGE_SOURCE_SERVICE is invalid")
    start = _parse_utc(
        settings.monitor_news_usage_start_at,
        field_name="MONITOR_NEWS_USAGE_START_AT",
    )
    return service, start


def render_news_usage_sql(name: str, settings: Settings) -> str:
    """Render only the dedicated usage SQL with its explicit source binding."""

    text = (SQL_DIR / name).read_text(encoding="utf-8")
    service = str(settings.monitor_news_usage_source_service or "").strip()
    if "${NEWS_USAGE_SOURCE_SERVICE}" in text and not _SOURCE_SERVICE_PATTERN.fullmatch(
        service
    ):
        raise ValueError("MONITOR_NEWS_USAGE_SOURCE_SERVICE is invalid")
    values = {
        "PROJECT_ID": settings.monitor_project_id,
        "DATASET_ID": settings.monitor_bq_dataset,
        "MONITOR_TIMEZONE": settings.monitor_timezone,
        "NEWS_USAGE_SOURCE_SERVICE": service,
        "NEWS_USAGE_CONTRACT_VERSION": NEWS_USAGE_CONTRACT_VERSION,
    }
    for key, value in values.items():
        text = text.replace("${" + key + "}", str(value))
    if "${" in text:
        raise ValueError(f"unresolved SQL placeholder in {name}")
    return text


class NewsUsageRefreshJob:
    """Publish one bounded usage window against an independent state row."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: Any | None = None,
        execution_id: str = "",
        trigger_source: str = "manual",
    ) -> None:
        self._settings = settings
        self._source_service, self._measurement_start = _validated_configuration(
            settings
        )
        self._client = client or bigquery.Client(project=settings.monitor_project_id)
        self._dataset = f"{settings.monitor_project_id}.{settings.monitor_bq_dataset}"
        self._execution_id = str(execution_id or "").strip() or f"local-{uuid4().hex}"
        self._trigger_source = str(trigger_source or "manual").strip().lower()
        if self._trigger_source not in {
            "manual",
            "manual_backfill",
            "scheduler_hourly",
        }:
            raise ValueError("unsupported refresh trigger source")

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

    def _lease_id(self) -> str:
        digest = hashlib.sha256(
            f"{self._execution_id}:news-usage-lease".encode("utf-8")
        ).hexdigest()[:32]
        return f"news_usage_lease_{digest}"

    def _run_id(
        self,
        *,
        sequence: int,
        expected_watermark: datetime | None,
        window_end: datetime,
    ) -> str:
        watermark = expected_watermark.isoformat() if expected_watermark else "initial"
        digest = hashlib.sha256(
            (
                f"{self._execution_id}:{max(0, int(sequence))}:"
                f"{watermark}:{window_end.isoformat()}"
            ).encode("utf-8")
        ).hexdigest()[:32]
        return f"news_usage_{digest}"

    def _read_watermark(self) -> datetime | None:
        rows = list(
            self._query(
                f"SELECT MAX(data_through) AS data_through "
                f"FROM `{self._dataset}.pipeline_state` "
                f"WHERE source = '{NEWS_USAGE_STATE_SOURCE}' AND status = 'succeeded'",
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
        target_end = current - timedelta(minutes=REFRESH_POLICY.expected_delay_minutes)
        resolved = self._read_watermark() if watermark is _UNSET else _as_utc_datetime(watermark)
        base = resolved or self._measurement_start
        window_start = max(
            self._measurement_start,
            base - timedelta(minutes=REFRESH_POLICY.overlap_minutes),
        )
        window_end = min(
            target_end,
            window_start + timedelta(hours=REFRESH_POLICY.max_window_hours),
        )
        if window_end <= window_start:
            raise ValueError("news usage refresh window is empty")
        return window_start, window_end

    def _acquire_lease(self, lease_id: str) -> dict[str, Any]:
        rows = list(
            self._query(
                f"""
MERGE `{self._dataset}.pipeline_state` target
USING (SELECT '{NEWS_USAGE_STATE_SOURCE}' AS source) incoming
ON target.source = incoming.source
WHEN MATCHED AND target.source_service = @source_service
  AND target.measurement_start_at = @measurement_start
  AND (
    target.lease_run_id IS NULL
    OR target.lease_expires_at <= CURRENT_TIMESTAMP()
    OR target.lease_run_id = @lease_id
  ) THEN UPDATE SET
  lease_run_id = @lease_id,
  lease_acquired_at = IF(
    target.lease_run_id = @lease_id,
    target.lease_acquired_at,
    CURRENT_TIMESTAMP()
  ),
  lease_expires_at = TIMESTAMP_ADD(
    CURRENT_TIMESTAMP(), INTERVAL @lease_ttl_minutes MINUTE
  )
WHEN NOT MATCHED THEN INSERT (
  source, data_through, published_run_id, status, updated_at,
  lease_run_id, lease_acquired_at, lease_expires_at,
  roster_snapshot_run_id, measurement_start_at, source_service
) VALUES (
  '{NEWS_USAGE_STATE_SOURCE}', NULL, NULL, 'never_published', CURRENT_TIMESTAMP(),
  @lease_id, CURRENT_TIMESTAMP(),
  TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL @lease_ttl_minutes MINUTE),
  NULL, @measurement_start, @source_service
);
SELECT
  lease_run_id = @lease_id AS acquired,
  source_service = @source_service
    AND measurement_start_at = @measurement_start AS configuration_matches,
  data_through,
  lease_run_id,
  lease_expires_at
FROM `{self._dataset}.pipeline_state`
WHERE source = '{NEWS_USAGE_STATE_SOURCE}';
""".strip(),
                [
                    bigquery.ScalarQueryParameter("lease_id", "STRING", lease_id),
                    bigquery.ScalarQueryParameter(
                        "lease_ttl_minutes",
                        "INT64",
                        REFRESH_POLICY.lease_ttl_minutes,
                    ),
                    bigquery.ScalarQueryParameter(
                        "measurement_start", "TIMESTAMP", self._measurement_start
                    ),
                    bigquery.ScalarQueryParameter(
                        "source_service", "STRING", self._source_service
                    ),
                ],
            )
        )
        row = _row_dict(rows[0]) if rows else {}
        if row.get("configuration_matches") is not True:
            raise ValueError("news usage state configuration does not match runtime")
        if row.get("acquired") is not True:
            raise NewsUsageLeaseUnavailableError(
                "news usage publisher lease is already owned"
            )
        return row

    def _renew_lease(self, lease_id: str) -> None:
        rows = list(
            self._query(
                f"""
UPDATE `{self._dataset}.pipeline_state`
SET lease_expires_at = TIMESTAMP_ADD(
  CURRENT_TIMESTAMP(), INTERVAL @lease_ttl_minutes MINUTE
)
WHERE source = '{NEWS_USAGE_STATE_SOURCE}' AND lease_run_id = @lease_id;
SELECT COUNTIF(
  lease_run_id = @lease_id AND lease_expires_at > CURRENT_TIMESTAMP()
) = 1 AS renewed
FROM `{self._dataset}.pipeline_state`
WHERE source = '{NEWS_USAGE_STATE_SOURCE}';
""".strip(),
                [
                    bigquery.ScalarQueryParameter("lease_id", "STRING", lease_id),
                    bigquery.ScalarQueryParameter(
                        "lease_ttl_minutes",
                        "INT64",
                        REFRESH_POLICY.lease_ttl_minutes,
                    ),
                ],
            )
        )
        if not rows or _row_dict(rows[0]).get("renewed") is not True:
            raise RuntimeError("news usage publisher lease was lost")

    def _release_lease(self, lease_id: str) -> None:
        self._query(
            f"""
UPDATE `{self._dataset}.pipeline_state`
SET lease_run_id = NULL, lease_acquired_at = NULL, lease_expires_at = NULL
WHERE source = '{NEWS_USAGE_STATE_SOURCE}' AND lease_run_id = @lease_id
""".strip(),
            [bigquery.ScalarQueryParameter("lease_id", "STRING", lease_id)],
        )

    def _read_roster_pointer(self) -> dict[str, Any]:
        rows = list(
            self._query(
                f"""
SELECT
  published_run_id AS roster_snapshot_run_id,
  scope_policy_version,
  global_roster_fingerprint,
  global_content_fingerprint,
  user_map_roster_fingerprint,
  user_map_content_fingerprint
FROM `{self._dataset}.pipeline_state`
WHERE source = 'published' AND status = 'succeeded'
LIMIT 1
""".strip(),
                [],
            )
        )
        pointer = _row_dict(rows[0]) if rows else {}
        required = (
            "roster_snapshot_run_id",
            "scope_policy_version",
            "global_roster_fingerprint",
            "global_content_fingerprint",
            "user_map_roster_fingerprint",
            "user_map_content_fingerprint",
        )
        if any(not str(pointer.get(name) or "").strip() for name in required):
            raise RuntimeError("successful Chat roster publication is unavailable")
        return pointer

    def _begin_run(
        self,
        *,
        run_id: str,
        window_start: datetime,
        window_end: datetime,
    ) -> None:
        started_at = datetime.now(timezone.utc)
        partition_start = started_at.replace(hour=0, minute=0, second=0, microsecond=0)
        self._query(
            f"""
DECLARE matched_run_count INT64 DEFAULT 0;
BEGIN TRANSACTION;
UPDATE `{self._dataset}.pipeline_runs`
SET finished_at = NULL,
    execution_id = @execution_id,
    trigger_source = @trigger_source,
    window_start = @window_start,
    window_end = @window_end,
    source = '{NEWS_USAGE_STATE_SOURCE}',
    status = 'running',
    error_code = NULL
WHERE run_id = @run_id
  AND started_at >= @run_partition_start
  AND started_at < @run_partition_end;
SET matched_run_count = @@row_count;
ASSERT matched_run_count <= 1 AS 'duplicate news usage run identity';
IF matched_run_count = 0 THEN
  INSERT INTO `{self._dataset}.pipeline_runs` (
    run_id, execution_id, trigger_source, started_at, window_start, window_end,
    source, status
  ) VALUES (
    @run_id, @execution_id, @trigger_source, @run_started_at,
    @window_start, @window_end, '{NEWS_USAGE_STATE_SOURCE}', 'running'
  );
END IF;
COMMIT TRANSACTION;
""".strip(),
            [
                bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
                bigquery.ScalarQueryParameter(
                    "execution_id", "STRING", self._execution_id
                ),
                bigquery.ScalarQueryParameter(
                    "trigger_source", "STRING", self._trigger_source
                ),
                bigquery.ScalarQueryParameter(
                    "run_started_at", "TIMESTAMP", started_at
                ),
                bigquery.ScalarQueryParameter(
                    "run_partition_start",
                    "TIMESTAMP",
                    partition_start - timedelta(days=2),
                ),
                bigquery.ScalarQueryParameter(
                    "run_partition_end",
                    "TIMESTAMP",
                    partition_start + timedelta(days=1),
                ),
                bigquery.ScalarQueryParameter(
                    "window_start", "TIMESTAMP", window_start
                ),
                bigquery.ScalarQueryParameter("window_end", "TIMESTAMP", window_end),
            ],
        )

    def _mark_failed(self, run_id: str, exc: Exception) -> None:
        self._query(
            f"""
UPDATE `{self._dataset}.pipeline_runs`
SET status = 'failed', finished_at = CURRENT_TIMESTAMP(), error_code = @error_code
WHERE run_id = @run_id
  AND source = '{NEWS_USAGE_STATE_SOURCE}'
  AND status = 'running'
  AND DATE(started_at) BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL 2 DAY)
    AND CURRENT_DATE()
""".strip(),
            [
                bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
                bigquery.ScalarQueryParameter(
                    "error_code", "STRING", type(exc).__name__
                ),
            ],
        )

    def _read_publication_receipt(
        self,
        *,
        run_id: str,
        window_end: datetime,
        roster_snapshot_run_id: str,
    ) -> dict[str, Any]:
        rows = list(
            self._query(
                f"""
SELECT
  state.data_through,
  state.published_run_id,
  state.roster_snapshot_run_id,
  state.source_service,
  state.measurement_start_at,
  state.status AS state_status,
  (
    SELECT run.status
    FROM `{self._dataset}.pipeline_runs` run
    WHERE run.run_id = @run_id
      AND run.source = '{NEWS_USAGE_STATE_SOURCE}'
      AND DATE(run.started_at) BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL 2 DAY)
        AND CURRENT_DATE()
    ORDER BY run.started_at DESC
    LIMIT 1
  ) AS run_status
FROM `{self._dataset}.pipeline_state` state
WHERE state.source = '{NEWS_USAGE_STATE_SOURCE}'
LIMIT 1
""".strip(),
                [bigquery.ScalarQueryParameter("run_id", "STRING", run_id)],
            )
        )
        receipt = _row_dict(rows[0]) if rows else {}
        receipt["expected_window_end"] = window_end
        receipt["expected_roster_snapshot_run_id"] = roster_snapshot_run_id
        return receipt

    def _publication_is_committed(
        self,
        receipt: dict[str, Any],
        *,
        run_id: str,
        window_end: datetime,
        roster_snapshot_run_id: str,
    ) -> bool:
        return (
            str(receipt.get("published_run_id") or "") == run_id
            and str(receipt.get("state_status") or "") == "succeeded"
            and str(receipt.get("run_status") or "") == "succeeded"
            and str(receipt.get("roster_snapshot_run_id") or "")
            == roster_snapshot_run_id
            and str(receipt.get("source_service") or "") == self._source_service
            and _as_utc_datetime(receipt.get("measurement_start_at"))
            == self._measurement_start
            and _as_utc_datetime(receipt.get("data_through"))
            == window_end.astimezone(timezone.utc)
        )

    def run(
        self,
        *,
        now: datetime | None = None,
        sequence: int = 0,
    ) -> dict[str, Any]:
        frozen_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        target_end = frozen_now - timedelta(minutes=REFRESH_POLICY.expected_delay_minutes)
        lease_id = self._lease_id()
        lease = self._acquire_lease(lease_id)
        try:
            watermark = _as_utc_datetime(lease.get("data_through"))
            if watermark is None and target_end <= self._measurement_start:
                self._release_lease(lease_id)
                return {
                    "status": "before_start",
                    "measurementStart": self._measurement_start.isoformat(),
                }
            if watermark is not None and watermark >= target_end:
                self._release_lease(lease_id)
                return {
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
        commit_may_be_durable = False
        try:
            self._begin_run(
                run_id=run_id,
                window_start=window_start,
                window_end=window_end,
            )
            run_started = True
            roster = self._read_roster_pointer()
            roster_snapshot_run_id = str(roster["roster_snapshot_run_id"])
            self._renew_lease(lease_id)
            parameters = [
                bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
                bigquery.ScalarQueryParameter("lease_id", "STRING", lease_id),
                bigquery.ScalarQueryParameter(
                    "expected_watermark", "TIMESTAMP", watermark
                ),
                bigquery.ScalarQueryParameter(
                    "window_start", "TIMESTAMP", window_start
                ),
                bigquery.ScalarQueryParameter("window_end", "TIMESTAMP", window_end),
                bigquery.ScalarQueryParameter(
                    "measurement_start", "TIMESTAMP", self._measurement_start
                ),
                bigquery.ScalarQueryParameter(
                    "event_future_tolerance_minutes",
                    "INT64",
                    max(0, int(REFRESH_POLICY.event_future_tolerance_minutes)),
                ),
                bigquery.ScalarQueryParameter(
                    "source_service", "STRING", self._source_service
                ),
                *[
                    bigquery.ScalarQueryParameter(name, "STRING", str(roster[name]))
                    for name in (
                        "roster_snapshot_run_id",
                        "scope_policy_version",
                        "global_roster_fingerprint",
                        "global_content_fingerprint",
                        "user_map_roster_fingerprint",
                        "user_map_content_fingerprint",
                    )
                ],
            ]
            publication_outcome = "query_result"
            try:
                rows = [
                    _row_dict(row)
                    for row in self._query(
                        render_news_usage_sql("publish_news_usage.sql", self._settings),
                        parameters,
                    )
                ]
                summary = rows[0] if rows else {}
            except Exception as publish_exc:
                commit_may_be_durable = True
                try:
                    receipt = self._read_publication_receipt(
                        run_id=run_id,
                        window_end=window_end,
                        roster_snapshot_run_id=roster_snapshot_run_id,
                    )
                except Exception as receipt_exc:
                    raise NewsUsagePublicationOutcomeUnknownError(
                        "news usage result is unknown; preserve lease until expiry"
                    ) from receipt_exc
                if not self._publication_is_committed(
                    receipt,
                    run_id=run_id,
                    window_end=window_end,
                    roster_snapshot_run_id=roster_snapshot_run_id,
                ):
                    commit_may_be_durable = False
                    raise publish_exc
                summary = {}
                publication_outcome = "reconciled_after_transport_error"
            return {
                "runId": run_id,
                "status": "succeeded",
                "windowStart": window_start.isoformat(),
                "windowEnd": window_end.isoformat(),
                "publishedRunId": run_id,
                "rosterSnapshotRunId": roster_snapshot_run_id,
                "inputRows": int(summary.get("input_rows") or 0),
                "canonicalRows": int(summary.get("canonical_rows") or 0),
                "quarantinedRows": int(summary.get("quarantined_rows") or 0),
                "publicationOutcome": publication_outcome,
            }
        except NewsUsagePublicationOutcomeUnknownError:
            raise
        except Exception as exc:
            if run_started and not commit_may_be_durable:
                try:
                    self._mark_failed(run_id, exc)
                except Exception:
                    pass
            if not commit_may_be_durable:
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
        target_end = frozen_now - timedelta(minutes=REFRESH_POLICY.expected_delay_minutes)
        runs: list[dict[str, Any]] = []
        for sequence in range(max(1, int(max_runs))):
            result = self.run(now=frozen_now, sequence=sequence)
            if result.get("status") in {"before_start", "up_to_date"}:
                return {
                    "status": "succeeded",
                    "runCount": len(runs),
                    "lastRun": runs[-1] if runs else result,
                    "dataThrough": (
                        runs[-1]["windowEnd"]
                        if runs
                        else result.get("dataThrough", "")
                    ),
                }
            runs.append(result)
            published_end = _parse_utc(
                str(result["windowEnd"]),
                field_name="published window end",
            )
            if published_end >= target_end:
                return {
                    "status": "succeeded",
                    "runCount": len(runs),
                    "windowStart": runs[0]["windowStart"],
                    "windowEnd": runs[-1]["windowEnd"],
                    "lastRun": runs[-1],
                }
        raise RuntimeError("news usage incremental refresh did not reach frozen target")


def run_configured_news_usage(
    settings: Settings,
    *,
    now: datetime | None,
    until_current: bool,
    trigger_source: str,
) -> dict[str, Any] | None:
    """Run only the optional branch; disabled leaves the legacy result untouched."""

    status = news_usage_configuration_status(settings)
    if status == "disabled":
        return None
    if status == "invalid":
        raise ValueError("news usage configuration is incomplete")
    job = NewsUsageRefreshJob(settings, trigger_source=trigger_source)
    return (
        job.run_until_current(now=now)
        if until_current
        else job.run(now=now)
    )
