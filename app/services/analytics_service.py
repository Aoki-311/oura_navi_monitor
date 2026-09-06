from __future__ import annotations

import math
import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.analytics_tasks import analytics_task, analytics_task_label
from app.domain.analytics_snapshot import content_fingerprint, roster_fingerprint
from app.domain.analysis_scopes import (
    AnalysisScope,
    SCOPE_POLICY_VERSION,
    display_area,
    membership_for,
)
from app.domain.question_categories import (
    analytics_question_category,
    question_category_label,
)
from app.domain.label_records import read_canonical_label_collection
from app.domain.roster_records import (
    read_canonical_roster_collection,
)
from app.settings import Settings
from app.refresh_policy import REFRESH_POLICY
from app.time_window import MetricsTimeWindow


LOGGER = logging.getLogger(__name__)


_ACTIVITY_ORDER = ("high", "middle", "low", "dormant")
_ACTIVITY_LABELS = {
    "high": "高アクティブ",
    "middle": "中アクティブ",
    "low": "低アクティブ",
    "dormant": "休眠ユーザー",
}
_REPRESENTATIVE_DELIVERY_PROFILES = {
    "complete_delivery_full",
    "runtime_truth_full",
}


@dataclass(frozen=True)
class _RosterSnapshot:
    rows: list[dict[str, Any]]
    isolated_count: int
    issue_counts: dict[str, int]
    diagnostic_fingerprint: str
    labels: list[dict[str, Any]]
    label_catalog_status: str
    label_catalog_issues: list[str]


class AnalyticsSnapshotConflictError(RuntimeError):
    """The published pointer and its run-versioned BQ projection disagree."""

    code = "analytics_snapshot_conflict"


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator > 0 else None


def _p95(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return int(ordered[index])


def _measurement_state(measured_count: int, total_count: int) -> str:
    if total_count == 0:
        return "no_usage"
    if measured_count == 0:
        return "not_measured"
    if measured_count < total_count:
        return "partial"
    return "measured"


def _historical_measurement_row(item: dict[str, Any]) -> bool:
    origin = str(item.get("record_origin") or "").strip()
    contract = str(item.get("analytics_contract_version") or "").strip()
    return origin in {"firestore_history", "legacy_audit_history"} or contract == "request_spec_analytics_v1"


def _measurement_reason(
    measured_count: int,
    total_count: int,
    *,
    unmeasured_rows: list[dict[str, Any]] | None = None,
    unmeasured_reasons: list[str] | None = None,
) -> str:
    if total_count == 0:
        return "no_usage"
    if measured_count >= total_count:
        return "complete"
    historical_gap = False
    current_gap = False
    no_usage_gap = False
    for item in unmeasured_rows or []:
        if _historical_measurement_row(item):
            historical_gap = True
        else:
            current_gap = True
    for reason in unmeasured_reasons or []:
        no_usage_gap = no_usage_gap or reason in {
            "no_usage",
            "population_without_usage",
        }
        historical_gap = historical_gap or reason in {
            "historical_unavailable",
            "mixed_history_and_current_gap",
        }
        current_gap = current_gap or reason in {
            "current_data_gap",
            "mixed_history_and_current_gap",
        }
    if no_usage_gap and (historical_gap or current_gap):
        return "mixed_no_usage_and_data_gap"
    if no_usage_gap:
        return "population_without_usage"
    if not historical_gap and not current_gap:
        current_gap = True
    if historical_gap and current_gap:
        return "mixed_history_and_current_gap"
    return "historical_unavailable" if historical_gap else "current_data_gap"


def _measurement_coverage(
    measured_count: int,
    total_count: int,
    *,
    unmeasured_rows: list[dict[str, Any]] | None = None,
    unmeasured_reasons: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "measuredCount": measured_count,
        "totalCount": total_count,
        "measurementState": _measurement_state(measured_count, total_count),
        "measurementReason": _measurement_reason(
            measured_count,
            total_count,
            unmeasured_rows=unmeasured_rows,
            unmeasured_reasons=unmeasured_reasons,
        ),
    }


def _complete_delivery_measurement(rows: list[dict[str, Any]]) -> dict[str, Any]:
    measured = [
        item
        for item in rows
        if item.get("measurement_available") is True
        and isinstance(item.get("complete_delivery"), bool)
        and str(
            item.get("answer_measurement_profile")
            or item.get("measurement_profile")
            or ""
        )
        in _REPRESENTATIVE_DELIVERY_PROFILES
    ]
    coverage = _measurement_coverage(
        len(measured),
        len(rows),
        unmeasured_rows=[item for item in rows if item not in measured],
    )
    return {
        "value": _rate(
            sum(item.get("complete_delivery") is True for item in measured),
            len(measured),
        ),
        **coverage,
    }


def _complete_latency_measurement(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values: list[int] = []
    measured_rows: list[dict[str, Any]] = []
    for item in rows:
        value = item.get("total_latency_ms")
        if value is None:
            continue
        try:
            resolved = int(value)
        except (TypeError, ValueError):
            continue
        if resolved >= 0:
            values.append(resolved)
            measured_rows.append(item)
    return {
        "valueMs": _p95(values),
        **_measurement_coverage(
            len(values),
            len(rows),
            unmeasured_rows=[item for item in rows if item not in measured_rows],
        ),
    }


def _distribution(counter: Counter[str], labels: dict[str, str] | None = None) -> list[dict[str, Any]]:
    total = sum(counter.values())
    rows = []
    for key, count in counter.most_common():
        row = {
            "key": key,
            "count": count,
            "rate": _rate(count, total),
        }
        if labels is not None:
            row["label"] = labels.get(key, key)
        rows.append(row)
    return rows


def _local_date_axis(window: MetricsTimeWindow) -> list[str]:
    timezone_value = ZoneInfo(window.timezone)
    first = window.start_utc.astimezone(timezone_value).date()
    last = (window.end_utc - timedelta(microseconds=1)).astimezone(timezone_value).date()
    values: list[str] = []
    current = first
    while current <= last:
        values.append(current.isoformat())
        current += timedelta(days=1)
    return values


def _published_date_axis(
    window: MetricsTimeWindow,
    *,
    data_through: datetime | None,
) -> list[str]:
    if data_through is None:
        return []
    effective_end = min(window.end_utc, data_through.astimezone(timezone.utc))
    if effective_end <= window.start_utc:
        return []
    bounded = MetricsTimeWindow(
        start_utc=window.start_utc,
        end_utc=effective_end,
        timezone=window.timezone,
        source=window.source,
        preset=window.preset,
        requested_days=window.requested_days,
        bucket_minutes=window.bucket_minutes,
    )
    return _local_date_axis(bounded)


def _published_hour_axis(
    window: MetricsTimeWindow,
    *,
    data_through: datetime | None,
) -> list[str]:
    if data_through is None:
        return []
    effective_end = min(window.end_utc, data_through.astimezone(timezone.utc))
    if effective_end <= window.start_utc:
        return []
    bounded = MetricsTimeWindow(
        start_utc=window.start_utc,
        end_utc=effective_end,
        timezone=window.timezone,
        source=window.source,
        preset=window.preset,
        requested_days=window.requested_days,
        bucket_minutes=window.bucket_minutes,
    )
    if len(_local_date_axis(bounded)) > 1:
        return [f"{hour:02d}:00" for hour in range(24)]
    local_zone = ZoneInfo(window.timezone)
    cursor = window.start_utc.astimezone(local_zone).replace(
        minute=0,
        second=0,
        microsecond=0,
    )
    if cursor.astimezone(timezone.utc) < window.start_utc:
        cursor += timedelta(hours=1)
    labels: list[str] = []
    while cursor.astimezone(timezone.utc) < effective_end:
        labels.append(f"{cursor.hour:02d}:00")
        cursor += timedelta(hours=1)
    return labels


def _visible_date_axis(
    window: MetricsTimeWindow,
    *,
    data_through: datetime | None,
    observed_dates: set[str],
) -> list[str]:
    if data_through is not None:
        return _published_date_axis(window, data_through=data_through)
    allowed = set(_local_date_axis(window))
    return sorted(day for day in observed_dates if day in allowed)


def _visible_hour_axis(
    window: MetricsTimeWindow,
    *,
    data_through: datetime | None,
    observed_hours: set[str],
) -> list[str]:
    if data_through is not None:
        return _published_hour_axis(window, data_through=data_through)
    allowed = {f"{hour:02d}:00" for hour in range(24)}
    return sorted(
        (hour for hour in observed_hours if hour in allowed),
        key=lambda value: int(value[:2]),
    )


def _partial_published_date(
    window: MetricsTimeWindow,
    *,
    data_through: datetime | None,
) -> str:
    if data_through is None:
        return ""
    published_end = data_through.astimezone(timezone.utc)
    if published_end >= window.end_utc or published_end <= window.start_utc:
        return ""
    local_end = published_end.astimezone(ZoneInfo(window.timezone))
    if (
        local_end.hour == 0
        and local_end.minute == 0
        and local_end.second == 0
        and local_end.microsecond == 0
    ):
        return ""
    return local_end.date().isoformat()


def _return_rate(days_by_user: dict[str, set[str]], *, window: MetricsTimeWindow) -> float | None:
    active_users = len(days_by_user)
    if active_users == 0 or len(_local_date_axis(window)) < 2:
        return None
    return _rate(sum(len(days) >= 2 for days in days_by_user.values()), active_users)


def _analytics_labels(
    label_ids: list[str],
    labels: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    return [
        {
            "labelId": str(labels[label_id]["label_id"]),
            "name": str(labels[label_id]["name"]),
            "color": str(labels[label_id]["color"]),
        }
        for label_id in label_ids
        if label_id in labels
    ]


def _activity_level(
    question_times: list[datetime],
    *,
    end: datetime,
    timezone_name: str,
) -> str:
    timezone_value = ZoneInfo(timezone_name)
    end_date = (end - timedelta(microseconds=1)).astimezone(timezone_value).date()
    active_dates = {
        value.astimezone(timezone_value).date()
        for value in question_times
        if 0 <= (end_date - value.astimezone(timezone_value).date()).days <= 13
    }
    active_days = len(active_dates)
    if active_days >= 6:
        return "high"
    if active_days >= 3:
        return "middle"
    if active_days >= 1:
        return "low"
    return "dormant"


def _classification_is_measured(item: dict[str, Any]) -> bool:
    return str(item.get("classification_measurement_state") or "").strip() == "measured"


def _tasks_for_measured_item(item: dict[str, Any]) -> list[str]:
    if str(item.get("task_measurement_state") or "").strip() != "measured":
        return []
    source = item.get("analytics_tasks")
    if not isinstance(source, list) or not source:
        return []
    return [analytics_task(task).value for task in source]


def _product_is_measured(item: dict[str, Any]) -> bool:
    return str(item.get("product_measurement_state") or "").strip() == "measured"


def _analytics_quality(
    rows: list[dict[str, Any]],
    *,
    source_pipeline: dict[str, Any],
) -> dict[str, Any]:
    def axis(field: str) -> dict[str, Any]:
        unmeasured_rows = [
            item
            for item in rows
            if str(item.get(field) or "").strip() != "measured"
        ]
        measured = len(rows) - len(unmeasured_rows)
        coverage = _measurement_coverage(
            measured,
            len(rows),
            unmeasured_rows=unmeasured_rows,
        )
        return {
            **coverage,
            "isolatedCount": len(rows) - measured,
        }

    return {
        "contractVersion": "dashboard_events_v2",
        "isolatedEventCount": sum(
            any(
                str(item.get(field) or "").strip() != "measured"
                for field in (
                    "classification_measurement_state",
                    "task_measurement_state",
                    "product_measurement_state",
                )
            )
            for item in rows
        ),
        "totalEventCount": len(rows),
        "classification": axis("classification_measurement_state"),
        "task": axis("task_measurement_state"),
        "product": axis("product_measurement_state"),
        "sourcePipeline": source_pipeline,
    }


def _measured_dimension(item: dict[str, Any], field: str) -> str | None:
    value = str(item.get(field) or "").strip().lower()
    return value if value and value != "unknown" else None


def _usage_axes(
    events: list[dict[str, Any]], *, window: MetricsTimeWindow,
    data_through: datetime | None,
) -> dict[str, Any]:
    """The shared formulas for overview and independently dated usage panels."""
    hourly: Counter[str] = Counter()
    date_users: dict[str, set[str]] = defaultdict(set)
    date_questions: Counter[str] = Counter()
    tasks: Counter[str] = Counter()
    devices: Counter[str] = Counter()
    modes: Counter[str] = Counter()
    task_measured = device_measured = mode_measured = 0
    display_timezone = ZoneInfo(window.timezone)
    for item in events:
        timestamp = _as_datetime(item.get("question_ts"))
        if timestamp is not None:
            hourly[f"{timestamp.astimezone(display_timezone).hour:02d}:00"] += 1
        day = str(item.get("question_date") or "")
        date_users[day].add(str(item.get("roster_id") or ""))
        date_questions[day] += 1
        item_tasks = _tasks_for_measured_item(item)
        if item_tasks:
            task_measured += 1
            tasks.update(item_tasks)
        if device := _measured_dimension(item, "device_class"):
            devices[device] += 1
            device_measured += 1
        if mode := _measured_dimension(item, "mode"):
            modes[mode] += 1
            mode_measured += 1
    partial_date = _partial_published_date(window, data_through=data_through)
    return {
        "hourlyQuestions": [
            {"hour": hour, "count": hourly.get(hour, 0)}
            for hour in _visible_hour_axis(window, data_through=data_through, observed_hours=set(hourly))
        ],
        "deviceDistribution": _distribution(devices, {"desktop": "PC", "mobile": "モバイル"}),
        "deviceMeasurement": _measurement_coverage(
            device_measured, len(events),
            unmeasured_rows=[item for item in events if _measured_dimension(item, "device_class") is None],
        ),
        "modeDistribution": _distribution(modes, {"internal": "社内モード", "websearch": "Web検索モード"}),
        "modeMeasurement": _measurement_coverage(
            mode_measured, len(events),
            unmeasured_rows=[item for item in events if _measured_dimension(item, "mode") is None],
        ),
        "usageTrend": [
            {"date": day, "activeUsers": len(date_users[day]), "questions": date_questions[day], "isPartial": day == partial_date}
            for day in _visible_date_axis(window, data_through=data_through, observed_dates=set(date_questions))
        ],
        "requestTasks": _distribution(tasks, {key: analytics_task_label(key) for key in tasks}),
        "taskMeasurement": _measurement_coverage(
            task_measured, len(events),
            unmeasured_rows=[item for item in events if not _tasks_for_measured_item(item)],
        ),
    }


class AnalyticsService:
    def __init__(
        self,
        *,
        analytics: Any,
        pipeline: Any,
        directory: Any,
        settings: Settings,
    ) -> None:
        self._analytics = analytics
        self._pipeline = pipeline
        self._directory = directory
        self._settings = settings

    def _publication_snapshot(self) -> dict[str, Any]:
        snapshot_reader = getattr(self._pipeline, "publication_snapshot", None)
        if callable(snapshot_reader):
            snapshot = snapshot_reader()
            return dict(snapshot) if isinstance(snapshot, dict) else {}
        return {"data_through": self._pipeline.data_through()}

    @staticmethod
    def _run_versioned_snapshot_id(
        publication: dict[str, Any],
    ) -> str:
        receipt_fields = (
            "scope_policy_version",
            "global_roster_fingerprint",
            "global_content_fingerprint",
            "user_map_roster_fingerprint",
            "user_map_content_fingerprint",
        )
        receipt_values = {
            field: str(publication.get(field) or "").strip()
            for field in receipt_fields
        }
        if not any(receipt_values.values()):
            raise AnalyticsSnapshotConflictError(
                "published analytics scope receipt is unavailable"
            )
        published_run_id = str(
            publication.get("published_run_id") or ""
        ).strip()
        if (
            receipt_values["scope_policy_version"] != SCOPE_POLICY_VERSION
            or not published_run_id
            or any(not receipt_values[field] for field in receipt_fields[1:])
        ):
            raise AnalyticsSnapshotConflictError(
                "published analytics scope receipt is incomplete"
            )
        return published_run_id

    @staticmethod
    def _source_pipeline_quality(snapshot: dict[str, Any]) -> dict[str, Any]:
        def count(name: str) -> int:
            try:
                return max(0, int(snapshot.get(name) or 0))
            except (TypeError, ValueError):
                return 0

        published_run_id = str(snapshot.get("published_run_id") or "")
        latest_run_id = str(snapshot.get("latest_run_id") or "")
        latest_run_status = str(snapshot.get("latest_run_status") or "")
        latest_run_error_code = str(snapshot.get("latest_run_error_code") or "")
        latest_run_finished_at = _as_datetime(
            snapshot.get("latest_run_finished_at")
        )
        quarantined = count("quarantined_event_count")
        axis_findings = count("axis_unmeasured_finding_count")
        blocking = count("batch_blocking_failure_count")
        diagnostics_marker = snapshot.get("quality_diagnostics_available")
        if isinstance(diagnostics_marker, bool):
            diagnostics_available = diagnostics_marker
        else:
            diagnostics_available = any(
                key in snapshot
                for key in (
                    "latest_run_id",
                    "latest_run_status",
                    "quarantined_event_count",
                    "deduplicated_delivery_count",
                    "repaired_duplicate_fact_count",
                    "axis_unmeasured_finding_count",
                    "batch_blocking_failure_count",
                )
            )
        diagnostics_error_code = str(
            snapshot.get("quality_diagnostics_error_code") or ""
        )
        if not diagnostics_available:
            state = "unavailable"
        elif latest_run_status == "failed":
            state = "blocked"
        elif not published_run_id:
            state = "unknown"
        elif quarantined or axis_findings:
            state = "degraded"
        else:
            state = "clean"
        return {
            "publishedRunId": published_run_id,
            "latestRunId": latest_run_id,
            "latestRunStatus": latest_run_status,
            "latestRunErrorCode": latest_run_error_code,
            "latestRunFinishedAt": (
                latest_run_finished_at.astimezone(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
                if latest_run_finished_at
                else ""
            ),
            "diagnosticsStatus": (
                "available" if diagnostics_available else "unavailable"
            ),
            "diagnosticsErrorCode": diagnostics_error_code,
            "state": state,
            "quarantinedEventCount": quarantined,
            "deduplicatedDeliveryCount": count("deduplicated_delivery_count"),
            "repairedDuplicateFactCount": count("repaired_duplicate_fact_count"),
            "axisUnmeasuredFindingCount": axis_findings,
            "batchBlockingFailureCount": blocking,
        }

    def _freshness(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        value = _as_datetime(snapshot.get("data_through"))
        now = datetime.now(timezone.utc)
        if value is None:
            return {"state": "unknown", "dataThrough": ""}
        state = (
            "fresh"
            if (now - value).total_seconds() <= REFRESH_POLICY.freshness_stale_after_minutes * 60
            else "stale"
        )
        return {
            "state": state,
            "dataThrough": value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        }

    @staticmethod
    def _roster_fingerprint(
        roster: list[dict[str, Any]],
        *,
        diagnostic_fingerprint: str = "",
    ) -> str:
        return roster_fingerprint(
            roster,
            diagnostic_fingerprint=diagnostic_fingerprint,
        )

    @staticmethod
    def _content_fingerprint(
        *,
        roster_fingerprint: str,
        roster: list[dict[str, Any]],
        labels: list[dict[str, Any]],
        label_catalog_status: str,
        label_catalog_issues: list[str],
    ) -> str:
        return content_fingerprint(
            roster_fingerprint_value=roster_fingerprint,
            roster=roster,
            labels=labels,
            label_catalog_status=label_catalog_status,
            label_catalog_issues=label_catalog_issues,
        )

    def _scope_metadata(
        self,
        *,
        scope: AnalysisScope,
        snapshot: _RosterSnapshot,
        publication: dict[str, Any],
        window: MetricsTimeWindow,
    ) -> dict[str, str]:
        roster_fingerprint = self._roster_fingerprint(
            snapshot.rows,
            diagnostic_fingerprint=snapshot.diagnostic_fingerprint,
        )
        content_fingerprint_value = self._content_fingerprint(
            roster_fingerprint=roster_fingerprint,
            roster=snapshot.rows,
            labels=snapshot.labels,
            label_catalog_status=snapshot.label_catalog_status,
            label_catalog_issues=snapshot.label_catalog_issues,
        )
        return {
            "scope": scope.value,
            "scopePolicyVersion": SCOPE_POLICY_VERSION,
            "rosterFingerprint": roster_fingerprint,
            "contentFingerprint": content_fingerprint_value,
            "publishedRunId": str(publication.get("published_run_id") or ""),
            "windowStart": window.start_utc.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "windowEnd": window.end_utc.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "windowTimezone": window.timezone,
        }

    def _roster_snapshot(
        self,
        scope: AnalysisScope,
        *,
        publication: dict[str, Any],
    ) -> _RosterSnapshot:
        if publication.get("publication_state_available") is False:
            raise AnalyticsSnapshotConflictError(
                "published analytics scope receipt is unavailable"
            )
        published_run_id = self._run_versioned_snapshot_id(publication)
        raw_rows = self._analytics.published_roster_snapshot(
            published_run_id=published_run_id
        )
        if not isinstance(raw_rows, list) or not raw_rows:
            raise AnalyticsSnapshotConflictError(
                "published roster projection is unavailable"
            )

        def uniform_text(field: str) -> str:
            values = {str(row.get(field) or "") for row in raw_rows}
            if len(values) != 1:
                raise AnalyticsSnapshotConflictError(
                    f"published roster projection has mixed {field}"
                )
            return values.pop()

        if uniform_text("snapshot_run_id") != published_run_id:
            raise AnalyticsSnapshotConflictError(
                "published roster projection run does not match pointer"
            )

        def json_list(value: Any, field: str) -> list[Any]:
            try:
                parsed = json.loads(str(value or "[]"))
            except (TypeError, ValueError) as exc:
                raise AnalyticsSnapshotConflictError(
                    f"published roster projection has invalid {field}"
                ) from exc
            if not isinstance(parsed, list):
                raise AnalyticsSnapshotConflictError(
                    f"published roster projection has invalid {field}"
                )
            return parsed

        def json_object(value: Any, field: str) -> dict[str, Any]:
            try:
                parsed = json.loads(str(value or "{}"))
            except (TypeError, ValueError) as exc:
                raise AnalyticsSnapshotConflictError(
                    f"published roster projection has invalid {field}"
                ) from exc
            if not isinstance(parsed, dict):
                raise AnalyticsSnapshotConflictError(
                    f"published roster projection has invalid {field}"
                )
            return parsed

        projected_rows: list[dict[str, Any]] = []
        labels_by_id: dict[str, dict[str, Any]] = {}
        for raw in raw_rows:
            label_ids = [
                str(value).strip()
                for value in json_list(raw.get("label_ids_json"), "label_ids_json")
                if str(value).strip()
            ]
            projected_rows.append({**raw, "label_ids": label_ids})
            for label in json_list(raw.get("labels_json"), "labels_json"):
                if not isinstance(label, dict):
                    raise AnalyticsSnapshotConflictError(
                        "published roster projection has invalid label row"
                    )
                label_id = str(label.get("label_id") or "").strip()
                if not label_id:
                    raise AnalyticsSnapshotConflictError(
                        "published roster projection has label without id"
                    )
                previous = labels_by_id.get(label_id)
                if previous is not None and json.dumps(
                    previous, ensure_ascii=False, sort_keys=True, default=str
                ) != json.dumps(label, ensure_ascii=False, sort_keys=True, default=str):
                    raise AnalyticsSnapshotConflictError(
                        "published roster projection has conflicting label rows"
                    )
                labels_by_id[label_id] = label

        records = read_canonical_roster_collection(projected_rows)
        if len(records.analytics_records) != len(projected_rows):
            raise AnalyticsSnapshotConflictError(
                "published roster projection contains invalid rows"
            )
        for record in records.analytics_records:
            value = record.value
            structural = membership_for(
                role=value.get("role"),
                department=value.get("department", ""),
                is_active=True,
            )
            if (
                value.get("global_scope_enabled") is not structural.global_enabled
                or value.get("user_map_scope_enabled") is not structural.user_map_enabled
            ):
                raise AnalyticsSnapshotConflictError(
                    "published roster projection has invalid scope flags"
                )

        isolated_values = {
            int(row.get("roster_isolated_count") or 0) for row in raw_rows
        }
        if len(isolated_values) != 1:
            raise AnalyticsSnapshotConflictError(
                "published roster projection has mixed isolation metadata"
            )
        isolated_count = isolated_values.pop()
        issue_counts_raw = uniform_text("roster_issue_counts_json")
        issue_counts = {
            str(key): int(value)
            for key, value in json_object(
                issue_counts_raw, "roster_issue_counts_json"
            ).items()
        }
        diagnostic_fingerprint = uniform_text("roster_diagnostic_fingerprint")
        status_field = f"{scope.value}_label_catalog_status"
        issues_field = f"{scope.value}_label_catalog_issues_json"
        label_catalog_status = uniform_text(status_field)
        label_catalog_issues = [
            str(value)
            for value in json_list(uniform_text(issues_field), issues_field)
        ]
        label_records = read_canonical_label_collection(labels_by_id.values())
        if any(not record.catalog_eligible for record in label_records):
            raise AnalyticsSnapshotConflictError(
                "published roster projection contains invalid labels"
            )
        label_rows = [record.value for record in label_records]
        scope_rows = [
            record.value
            for record in records.analytics_records
            if record.value.get("is_active") is True
            and (
                record.value.get("global_scope_enabled") is True
                if scope is AnalysisScope.GLOBAL
                else record.value.get("user_map_scope_enabled") is True
            )
        ]
        try:
            roster_receipt = self._roster_fingerprint(
                scope_rows,
                diagnostic_fingerprint=diagnostic_fingerprint,
            )
            content_receipt = self._content_fingerprint(
                roster_fingerprint=roster_receipt,
                roster=scope_rows,
                labels=label_rows,
                label_catalog_status=label_catalog_status,
                label_catalog_issues=label_catalog_issues,
            )
        except ValueError as exc:
            raise AnalyticsSnapshotConflictError(
                "published roster projection has invalid receipt input"
            ) from exc
        if (
            roster_receipt
            != str(publication.get(f"{scope.value}_roster_fingerprint") or "")
            or content_receipt
            != str(publication.get(f"{scope.value}_content_fingerprint") or "")
        ):
            raise AnalyticsSnapshotConflictError(
                "published roster projection fingerprint does not match pointer"
            )
        return _RosterSnapshot(
            rows=scope_rows,
            isolated_count=isolated_count,
            issue_counts=issue_counts,
            diagnostic_fingerprint=diagnostic_fingerprint,
            labels=label_rows,
            label_catalog_status=label_catalog_status,
            label_catalog_issues=label_catalog_issues,
        )

    @staticmethod
    def _content_diagnostics(
        *,
        roster: _RosterSnapshot,
        labels: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        label_diagnostics = labels or {
            "state": "complete",
            "labelCatalogStatus": "not_applicable",
            "issues": [],
        }
        roster_issues = [
            f"roster_{issue}" for issue in sorted(roster.issue_counts)
        ]
        issues = list(
            dict.fromkeys(
                [*list(label_diagnostics.get("issues") or []), *roster_issues]
            )
        )
        return {
            "state": (
                "degraded"
                if roster.isolated_count
                or label_diagnostics.get("state") != "complete"
                or issues
                else "complete"
            ),
            "labelCatalogStatus": str(
                label_diagnostics.get("labelCatalogStatus") or "not_applicable"
            ),
            "rosterStatus": (
                "partial" if roster.isolated_count else "available"
            ),
            "rosterIsolatedCount": roster.isolated_count,
            "rosterIssueCounts": dict(roster.issue_counts),
            "issues": issues,
        }

    @staticmethod
    def _label_catalog(
        *,
        snapshot: _RosterSnapshot,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        issues = list(snapshot.label_catalog_issues)
        status = snapshot.label_catalog_status
        return list(snapshot.labels), {
            "state": (
                "complete" if status == "available" and not issues else "degraded"
            ),
            "labelCatalogStatus": status,
            "issues": issues,
        }

    def _usage_panel(self, *, window: MetricsTimeWindow, area_key: str = "") -> dict[str, Any]:
        publication = self._publication_snapshot()
        freshness = self._freshness(publication)
        scope_snapshot = self._roster_snapshot(AnalysisScope.GLOBAL, publication=publication)
        roster_ids = {
            str(row["roster_id"]) for row in scope_snapshot.rows
            if not area_key or str(row.get("area_key") or "") == area_key
        }
        events = [
            row for row in self._analytics.overview_events(
                window=window, area_key=area_key,
                published_run_id=self._run_versioned_snapshot_id(publication),
            )
            if str(row.get("roster_id") or "") in roster_ids and bool(row.get("valid_question", True))
        ]
        return {
            **self._scope_metadata(scope=AnalysisScope.GLOBAL, snapshot=scope_snapshot, publication=publication, window=window),
            "scopeUserCount": len(roster_ids),
            "freshness": freshness,
            **_usage_axes(events, window=window, data_through=_as_datetime(freshness.get("dataThrough"))),
        }

    def environment(self, *, window: MetricsTimeWindow, area_key: str = "") -> dict[str, Any]:
        payload = self._usage_panel(window=window, area_key=area_key)
        for field in ("usageTrend", "requestTasks", "taskMeasurement"):
            payload.pop(field)
        return payload

    def trend(self, *, window: MetricsTimeWindow, area_key: str = "") -> dict[str, Any]:
        payload = self._usage_panel(window=window, area_key=area_key)
        for field in ("hourlyQuestions", "deviceDistribution", "deviceMeasurement", "modeDistribution", "modeMeasurement"):
            payload.pop(field)
        return payload

    def overview(self, *, window: MetricsTimeWindow, area_key: str = "") -> dict[str, Any]:
        publication = self._publication_snapshot()
        freshness = self._freshness(publication)
        data_through = _as_datetime(freshness.get("dataThrough"))
        scope_snapshot = self._roster_snapshot(
            AnalysisScope.GLOBAL,
            publication=publication,
        )
        scope_roster = scope_snapshot.rows
        roster = [
            item
            for item in scope_roster
            if not area_key or str(item.get("area_key") or "") == area_key
        ]
        roster_ids = {str(item["roster_id"]) for item in roster}
        events = [
            item for item in self._analytics.overview_events(
                window=window,
                area_key=area_key,
                published_run_id=self._run_versioned_snapshot_id(publication),
            )
            if str(item.get("roster_id") or "") in roster_ids and bool(item.get("valid_question", True))
        ]
        activity_events = [
            item for item in self._analytics.activity_events(
                end=window.end_utc,
                area_key=area_key,
                published_run_id=self._run_versioned_snapshot_id(publication),
            )
            if str(item.get("roster_id") or "") in roster_ids
        ]
        active_ids = {str(item.get("roster_id") or "") for item in events}
        days_by_user: dict[str, set[str]] = defaultdict(set)
        times_by_user: dict[str, list[datetime]] = defaultdict(list)
        for item in activity_events:
            roster_id = str(item.get("roster_id") or "")
            timestamp = _as_datetime(item.get("question_ts"))
            if timestamp:
                times_by_user[roster_id].append(timestamp)
        for item in events:
            roster_id = str(item.get("roster_id") or "")
            days_by_user[roster_id].add(str(item.get("question_date") or ""))
        products = Counter()
        matrix = Counter()
        product_measured_count = 0
        product_candidate_count = 0
        product_resolved_count = 0
        product_unresolved_questions = 0
        for item in events:
            item_tasks = _tasks_for_measured_item(item)
            if _product_is_measured(item):
                product_measured_count += 1
                candidate_count = int(item.get("product_candidate_count") or 0)
                resolved_count = int(item.get("product_resolved_count") or 0)
                product_candidate_count += candidate_count
                product_resolved_count += resolved_count
                product_unresolved_questions += resolved_count < candidate_count
                product = str(item.get("primary_product_name") or "").strip()
                if product:
                    products[product] += 1
                    for task in item_tasks:
                        matrix[(product, task)] += 1

        levels = {
            str(user["roster_id"]): _activity_level(
                times_by_user.get(str(user["roster_id"]), []),
                end=window.end_utc,
                timezone_name=window.timezone,
            )
            for user in roster
        }
        activity_counter = Counter(levels.values())
        by_area: dict[str, Counter[str]] = defaultdict(Counter)
        by_role: dict[str, Counter[str]] = defaultdict(Counter)
        roster_by_id = {str(item["roster_id"]): item for item in roster}
        for roster_id, level in levels.items():
            roster_user = roster_by_id[roster_id]
            by_area[
                display_area(str(roster_user.get("area_key") or ""))
            ][level] += 1
            by_role[str(roster_by_id[roster_id].get("role") or "-")][level] += 1

        return {
            **self._scope_metadata(
                scope=AnalysisScope.GLOBAL,
                # A display filter must not change the publication identity.
                # Otherwise overview/regions/users would reject one another
                # after an area is selected even though they read one run.
                snapshot=scope_snapshot,
                publication=publication,
                window=window,
            ),
            "contentDiagnostics": self._content_diagnostics(
                roster=scope_snapshot
            ),
            "scopeUserCount": len(roster),
            "freshness": freshness,
            "analyticsQuality": _analytics_quality(
                events,
                source_pipeline=self._source_pipeline_quality(publication),
            ),
            "kpis": {
                "activeUsers": len(active_ids),
                "adoptionRate": _rate(len(active_ids), len(roster)),
                "returnRate": _return_rate(days_by_user, window=window),
                "questionsPerActiveUser": _rate(len(events), len(active_ids)),
                "completeDelivery": _complete_delivery_measurement(events),
                "p95Latency": _complete_latency_measurement(events),
            },
            **_usage_axes(events, window=window, data_through=data_through),
            "activityDistribution": [
                {"key": key, "label": _ACTIVITY_LABELS[key], "count": activity_counter.get(key, 0), "rate": _rate(activity_counter.get(key, 0), len(roster))}
                for key in _ACTIVITY_ORDER
            ],
            "activityByArea": [self._stacked_activity(label, values) for label, values in sorted(by_area.items())],
            "activityByRole": [self._stacked_activity(label, values) for label, values in sorted(by_role.items())],
            "topProducts": [{"label": key, "count": value} for key, value in products.most_common(10)],
            "productTaskMatrix": [
                {
                    "product": product,
                    "task": task,
                    "taskLabel": analytics_task_label(task),
                    "count": count,
                }
                for (product, task), count in matrix.items()
                if product in {name for name, _count in products.most_common(10)}
            ],
            "productResolution": {
                "candidateCount": product_candidate_count,
                "resolvedCount": product_resolved_count,
                "unresolvedQuestions": product_unresolved_questions,
                "resolutionRate": _rate(
                    product_resolved_count,
                    product_candidate_count,
                ),
                **_measurement_coverage(
                    product_measured_count,
                    len(events),
                    unmeasured_rows=[item for item in events if not _product_is_measured(item)],
                ),
            },
        }

    @staticmethod
    def _stacked_activity(label: str, values: Counter[str]) -> dict[str, Any]:
        total = sum(values.values())
        return {
            "label": label,
            "total": total,
            "segments": [
                {"key": key, "label": _ACTIVITY_LABELS[key], "count": values.get(key, 0), "rate": _rate(values.get(key, 0), total)}
                for key in _ACTIVITY_ORDER
            ],
        }

    def regions(self, *, window: MetricsTimeWindow) -> dict[str, Any]:
        publication = self._publication_snapshot()
        freshness = self._freshness(publication)
        roster_snapshot = self._roster_snapshot(
            AnalysisScope.GLOBAL,
            publication=publication,
        )
        roster = roster_snapshot.rows
        roster_ids = {str(item["roster_id"]) for item in roster}
        events = [
            item for item in self._analytics.overview_events(
                window=window,
                published_run_id=self._run_versioned_snapshot_id(publication),
            )
            if str(item.get("roster_id") or "") in roster_ids and bool(item.get("valid_question", True))
        ]
        by_area_users: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for user in roster:
            by_area_users[str(user.get("area_key") or "")].append(user)
        events_by_area: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in events:
            events_by_area[str(event.get("area_key") or "")].append(event)
        regions = []
        for area_key, users in sorted(by_area_users.items()):
            area_events = events_by_area.get(area_key, [])
            active = {str(item.get("roster_id") or "") for item in area_events}
            days: dict[str, set[str]] = defaultdict(set)
            for item in area_events:
                days[str(item.get("roster_id") or "")].add(str(item.get("question_date") or ""))
            regions.append({
                "areaKey": area_key,
                "area": display_area(area_key),
                "rosterUsers": len(users),
                "activeUsers": len(active),
                "questions": len(area_events),
                "adoptionRate": _rate(len(active), len(users)),
                "returnRate": _return_rate(days, window=window),
            })
        regions.sort(
            key=lambda item: (
                item["adoptionRate"] if item["adoptionRate"] is not None else -1,
                item["activeUsers"],
                item["questions"],
            ),
            reverse=True,
        )
        return {
            **self._scope_metadata(
                scope=AnalysisScope.GLOBAL,
                snapshot=roster_snapshot,
                publication=publication,
                window=window,
            ),
            "contentDiagnostics": self._content_diagnostics(
                roster=roster_snapshot
            ),
            "scopeUserCount": len(roster),
            "freshness": freshness,
            "regions": regions,
        }

    def _users_for_scope(
        self,
        *,
        scope: AnalysisScope,
        q: str = "",
        area_key: str = "",
        activity: str = "",
        sort: str = "last_desc",
        window: MetricsTimeWindow,
    ) -> dict[str, Any]:
        publication = self._publication_snapshot()
        freshness = self._freshness(publication)
        scope_snapshot = self._roster_snapshot(
            scope,
            publication=publication,
        )
        scope_roster = scope_snapshot.rows
        roster = [
            item
            for item in scope_roster
            if not area_key or str(item.get("area_key") or "") == area_key
        ]
        metrics = {
            str(item.get("roster_id") or ""): item
            for item in self._analytics.user_metrics(
                window=window,
                published_run_id=self._run_versioned_snapshot_id(publication),
            )
        }
        activity_times: dict[str, list[datetime]] = defaultdict(list)
        roster_ids = {str(item["roster_id"]) for item in roster}
        for item in self._analytics.activity_events(
            end=window.end_utc,
            area_key=area_key,
            published_run_id=self._run_versioned_snapshot_id(publication),
        ):
            roster_id = str(item.get("roster_id") or "")
            timestamp = _as_datetime(item.get("question_ts"))
            if roster_id in roster_ids and timestamp is not None:
                activity_times[roster_id].append(timestamp)
        completion_by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in self._analytics.overview_events(
            window=window,
            area_key=area_key,
            published_run_id=self._run_versioned_snapshot_id(publication),
        ):
            if str(item.get("roster_id") or "") in roster_ids and bool(item.get("valid_question", True)):
                completion_by_user[str(item.get("roster_id") or "")].append(item)
        label_rows, label_diagnostics = self._label_catalog(
            snapshot=scope_snapshot
        )
        content_diagnostics = self._content_diagnostics(
            roster=scope_snapshot,
            labels=label_diagnostics,
        )
        labels = {str(item.get("label_id") or ""): item for item in label_rows}
        keyword = str(q or "").strip().lower()
        rows = []
        for user in roster:
            metric = metrics.get(str(user["roster_id"]), {})
            level = _activity_level(
                activity_times.get(str(user["roster_id"]), []),
                end=window.end_utc,
                timezone_name=window.timezone,
            )
            if activity and activity != level:
                continue
            if keyword and keyword not in str(user.get("name") or "").lower() and keyword not in str(user.get("email") or "").lower():
                continue
            selected_events = completion_by_user.get(str(user["roster_id"]), [])
            rows.append({
                "rosterId": user["roster_id"],
                "name": user["name"],
                "email": user["email"],
                "area": user["area"],
                "areaKey": user["area_key"],
                "workplace": str(user.get("workplace") or ""),
                "role": str(user.get("role") or ""),
                "department": str(user.get("department") or ""),
                "labels": _analytics_labels(list(user.get("label_ids", [])), labels),
                "lastActiveAt": (
                    parsed_last_active.isoformat()
                    if (parsed_last_active := _as_datetime(metric.get("last_active_at")))
                    else ""
                ),
                "activeDays7": int(metric.get("active_days_7") or 0),
                "userMessageCount7": int(metric.get("user_message_count_7") or 0),
                "activeDaysInPeriod": len({str(item.get("question_date")) for item in selected_events if item.get("question_date")}),
                "userMessageCountInPeriod": len(selected_events),
                "completeDelivery": _complete_delivery_measurement(selected_events),
                "activity": level,
                "activityLabel": _ACTIVITY_LABELS[level],
            })
        if sort == "name_asc":
            rows.sort(key=lambda item: (str(item.get("name") or ""), str(item.get("rosterId") or "")))
        elif sort == "messages_desc":
            rows.sort(
                key=lambda item: (
                    int(item.get("userMessageCountInPeriod") or 0),
                    str(item.get("lastActiveAt") or ""),
                    str(item.get("name") or ""),
                ),
                reverse=True,
            )
        elif sort == "success_desc":
            rows.sort(
                key=lambda item: (
                    -1.0
                    if item["completeDelivery"].get("value") is None
                    else float(item["completeDelivery"]["value"]),
                    str(item.get("lastActiveAt") or ""),
                    str(item.get("name") or ""),
                ),
                reverse=True,
            )
        else:
            rows.sort(
                key=lambda item: (
                    str(item.get("lastActiveAt") or ""),
                    str(item.get("name") or ""),
                ),
                reverse=True,
            )
        return {
            **self._scope_metadata(
                scope=scope,
                snapshot=scope_snapshot,
                publication=publication,
                window=window,
            ),
            "contentDiagnostics": content_diagnostics,
            "scopeUserCount": len(roster),
            "freshness": freshness,
            "users": rows,
        }

    def overview_users(
        self,
        *,
        q: str = "",
        area_key: str = "",
        activity: str = "",
        sort: str = "last_desc",
        window: MetricsTimeWindow,
    ) -> dict[str, Any]:
        return self._users_for_scope(
            scope=AnalysisScope.GLOBAL,
            q=q,
            area_key=area_key,
            activity=activity,
            sort=sort,
            window=window,
        )

    def users(
        self,
        *,
        q: str = "",
        area_key: str = "",
        activity: str = "",
        sort: str = "last_desc",
        window: MetricsTimeWindow,
    ) -> dict[str, Any]:
        return self._users_for_scope(
            scope=AnalysisScope.USER_MAP,
            q=q,
            area_key=area_key,
            activity=activity,
            sort=sort,
            window=window,
        )

    def user_detail(self, roster_id: str, *, window: MetricsTimeWindow) -> dict[str, Any]:
        publication = self._publication_snapshot()
        freshness = self._freshness(publication)
        data_through = _as_datetime(freshness.get("dataThrough"))
        peer_snapshot = self._roster_snapshot(
            AnalysisScope.USER_MAP,
            publication=publication,
        )
        peer_roster = peer_snapshot.rows
        user = next(
            (
                item
                for item in peer_roster
                if str(item.get("roster_id") or "") == roster_id
            ),
            None,
        )
        if user is None:
            raise KeyError("user not found")
        events = self._analytics.user_detail_events(
            roster_id=roster_id,
            window=window,
            published_run_id=self._run_versioned_snapshot_id(publication),
        )
        events = [item for item in events if bool(item.get("valid_question", True))]
        label_rows, label_diagnostics = self._label_catalog(
            snapshot=peer_snapshot
        )
        content_diagnostics = self._content_diagnostics(
            roster=peer_snapshot,
            labels=label_diagnostics,
        )
        labels = {str(item.get("label_id") or ""): item for item in label_rows}
        dates = {str(item.get("question_date") or "") for item in events}
        day_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in events:
            day_rows[str(item.get("question_date") or "")].append(item)
        measured_product_rows = [item for item in events if _product_is_measured(item)]
        products = Counter(
            str(item.get("primary_product_name") or "")
            for item in measured_product_rows
            if str(item.get("primary_product_name") or "")
        )
        product_candidate_count = sum(
            int(item.get("product_candidate_count") or 0)
            for item in measured_product_rows
        )
        product_resolved_count = sum(
            int(item.get("product_resolved_count") or 0)
            for item in measured_product_rows
        )
        product_unresolved_questions = sum(
            int(item.get("product_resolved_count") or 0)
            < int(item.get("product_candidate_count") or 0)
            for item in measured_product_rows
        )
        tasks = Counter()
        task_measured_count = 0
        for item in events:
            source_tasks = _tasks_for_measured_item(item)
            if source_tasks:
                task_measured_count += 1
            for task in source_tasks:
                tasks[task] += 1
        measured_category_rows = [item for item in events if _classification_is_measured(item)]
        categories = Counter(
            analytics_question_category(item.get("primary_question_category")).value
            for item in measured_category_rows
        )
        product_measured_count = len(measured_product_rows)
        measured_modes = [
            value
            for item in events
            if (value := _measured_dimension(item, "mode")) is not None
        ]
        measured_devices = [
            value
            for item in events
            if (value := _measured_dimension(item, "device_class")) is not None
        ]
        modes = Counter(measured_modes)
        devices = Counter(measured_devices)
        user_metric = next(
            (
                item
                for item in self._analytics.user_metrics(
                    window=window,
                    published_run_id=self._run_versioned_snapshot_id(publication),
                )
                if str(item.get("roster_id") or "") == roster_id
            ),
            {},
        )
        last_active = _as_datetime(user_metric.get("last_active_at"))
        peer_ids = {str(item["roster_id"]) for item in peer_roster}
        peer_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in self._analytics.overview_events(
            window=window,
            published_run_id=self._run_versioned_snapshot_id(publication),
        ):
            peer_id = str(item.get("roster_id") or "")
            if peer_id in peer_ids and bool(item.get("valid_question", True)):
                peer_events[peer_id].append(item)
        comparisons = {
            "area": self._peer_comparison(user=user, roster=peer_roster, events=peer_events, field="area"),
            "role": self._peer_comparison(user=user, roster=peer_roster, events=peer_events, field="role"),
        }
        partial_date = _partial_published_date(
            window,
            data_through=data_through,
        )
        return {
            **self._scope_metadata(
                scope=AnalysisScope.USER_MAP,
                snapshot=peer_snapshot,
                publication=publication,
                window=window,
            ),
            "contentDiagnostics": content_diagnostics,
            "freshness": freshness,
            "analyticsQuality": _analytics_quality(
                events,
                source_pipeline=self._source_pipeline_quality(publication),
            ),
            "profile": {
                "rosterId": roster_id,
                "name": user["name"],
                "email": user["email"],
                "area": user["area"],
                "workplace": user["workplace"],
                "role": user["role"],
                "department": user["department"],
                "mrExperience": user.get("mr_experience") or "-",
                "labels": _analytics_labels(list(user.get("label_ids", [])), labels),
            },
            "summary": {
                "lastActiveAt": last_active.isoformat() if last_active else "",
                "activeDays": len(dates),
                "questions": len(events),
                "questionsPerActiveDay": _rate(len(events), len(dates)),
                "completeDelivery": _complete_delivery_measurement(events),
                "p95Latency": _complete_latency_measurement(events),
            },
            "comparisons": comparisons,
            "trend": [
                {
                    "date": day,
                    "questions": len(day_rows.get(day, [])),
                    "completeDelivery": _complete_delivery_measurement(
                        day_rows.get(day, [])
                    ),
                    "isPartial": day == partial_date,
                }
                for day in _visible_date_axis(
                    window,
                    data_through=data_through,
                    observed_dates=set(day_rows),
                )
            ],
            "products": [{"label": key, "count": value} for key, value in products.most_common(10)],
            "productResolution": {
                "candidateCount": product_candidate_count,
                "resolvedCount": product_resolved_count,
                "unresolvedQuestions": product_unresolved_questions,
                "resolutionRate": _rate(
                    product_resolved_count,
                    product_candidate_count,
                ),
                **_measurement_coverage(
                    product_measured_count,
                    len(events),
                    unmeasured_rows=[item for item in events if not _product_is_measured(item)],
                ),
            },
            "tasks": _distribution(
                tasks,
                {key: analytics_task_label(key) for key in tasks},
            ),
            "taskMeasurement": _measurement_coverage(
                task_measured_count,
                len(events),
                unmeasured_rows=[item for item in events if not _tasks_for_measured_item(item)],
            ),
            "questionCategories": _distribution(
                categories,
                {key: question_category_label(key) for key in categories},
            ),
            "questionCategoryMeasurement": _measurement_coverage(
                len(measured_category_rows),
                len(events),
                unmeasured_rows=[item for item in events if not _classification_is_measured(item)],
            ),
            "modes": _distribution(modes, {"internal": "社内モード", "websearch": "Web検索モード"}),
            "modeMeasurement": _measurement_coverage(
                len(measured_modes),
                len(events),
                unmeasured_rows=[
                    item
                    for item in events
                    if _measured_dimension(item, "mode") is None
                ],
            ),
            "devices": _distribution(devices, {"desktop": "PC", "mobile": "モバイル"}),
            "deviceMeasurement": _measurement_coverage(
                len(measured_devices),
                len(events),
                unmeasured_rows=[
                    item
                    for item in events
                    if _measured_dimension(item, "device_class") is None
                ],
            ),
        }

    @staticmethod
    def _peer_comparison(
        *,
        user: dict[str, Any],
        roster: list[dict[str, Any]],
        events: dict[str, list[dict[str, Any]]],
        field: str,
    ) -> dict[str, Any]:
        label = str(user.get(field) or "-")
        selected_roster_id = str(user.get("roster_id") or "")
        peers = [
            item
            for item in roster
            if str(item.get("roster_id") or "") != selected_roster_id
            and str(item.get(field) or "-") == label
        ]
        questions = [len(events.get(str(item["roster_id"]), [])) for item in peers]
        active_days = [
            len({str(event.get("question_date") or "") for event in events.get(str(item["roster_id"]), [])})
            for item in peers
        ]
        complete_rates: list[float] = []
        unmeasured_reasons: list[str] = []
        for item in peers:
            peer_rows = events.get(str(item["roster_id"]), [])
            if not peer_rows:
                unmeasured_reasons.append("no_usage")
                continue
            measurement = _complete_delivery_measurement(peer_rows)
            if measurement["value"] is not None:
                complete_rates.append(float(measurement["value"]))
            else:
                unmeasured_reasons.append(str(measurement["measurementReason"]))
        complete_delivery_coverage = _measurement_coverage(
            len(complete_rates),
            len(peers),
            unmeasured_reasons=unmeasured_reasons,
        )
        return {
            "label": label,
            "peerCount": len(peers),
            "averageQuestions": sum(questions) / len(questions) if questions else None,
            "averageActiveDays": sum(active_days) / len(active_days) if active_days else None,
            "averageCompleteDelivery": {
                "value": (
                    sum(complete_rates) / len(complete_rates)
                    if complete_rates
                    else None
                ),
                **complete_delivery_coverage,
            },
        }
