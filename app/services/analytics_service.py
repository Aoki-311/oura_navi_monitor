from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.analytics_tasks import analytics_task, analytics_task_label
from app.domain.analysis_scopes import (
    AnalysisScope,
    Department,
    display_area,
    membership_for,
)
from app.domain.question_categories import (
    analytics_question_category,
    question_category_label,
)
from app.refresh_policy import next_scheduled_refresh
from app.settings import Settings
from app.time_window import MetricsTimeWindow


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


def _measurement_coverage(measured_count: int, total_count: int) -> dict[str, Any]:
    return {
        "measuredCount": measured_count,
        "totalCount": total_count,
        "measurementState": _measurement_state(measured_count, total_count),
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
    return {
        "value": _rate(
            sum(item.get("complete_delivery") is True for item in measured),
            len(measured),
        ),
        "measuredCount": len(measured),
        "totalCount": len(rows),
        "measurementState": _measurement_state(len(measured), len(rows)),
    }


def _complete_latency_measurement(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values: list[int] = []
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
    return {
        "valueMs": _p95(values),
        "measuredCount": len(values),
        "totalCount": len(rows),
        "measurementState": _measurement_state(len(values), len(rows)),
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
        measured = sum(
            str(item.get(field) or "").strip() == "measured" for item in rows
        )
        coverage = _measurement_coverage(measured, len(rows))
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
        common = {
            "refreshCadenceMinutes": int(
                self._settings.monitor_refresh_cadence_minutes
            ),
            "expectedDelayMinutes": int(
                self._settings.monitor_refresh_delay_minutes
            ),
            "staleAfterMinutes": int(
                self._settings.monitor_data_freshness_minutes
            ),
            "nextPlannedRefreshAt": next_scheduled_refresh(
                now=now,
                timezone_name=self._settings.monitor_timezone,
            )
            .isoformat()
            .replace("+00:00", "Z"),
        }
        if value is None:
            return {"state": "unknown", "dataThrough": "", **common}
        state = (
            "fresh"
            if (now - value).total_seconds() <= self._settings.monitor_data_freshness_minutes * 60
            else "stale"
        )
        return {
            "state": state,
            "dataThrough": value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            **common,
        }

    def _roster(self, scope: AnalysisScope, *, area_key: str = "") -> list[dict[str, Any]]:
        users = self._directory.list_users(include_inactive=False)
        return [
            item
            for item in users
            if membership_for(Department(item["department"]), is_active=bool(item["is_active"])).includes(scope)
            and (not area_key or str(item.get("area_key") or "") == area_key)
        ]

    def overview(self, *, window: MetricsTimeWindow, area_key: str = "") -> dict[str, Any]:
        publication = self._publication_snapshot()
        freshness = self._freshness(publication)
        data_through = _as_datetime(freshness.get("dataThrough"))
        partial_date = _partial_published_date(
            window,
            data_through=data_through,
        )
        roster = self._roster(AnalysisScope.GLOBAL, area_key=area_key)
        roster_ids = {str(item["roster_id"]) for item in roster}
        events = [
            item for item in self._analytics.overview_events(window=window, area_key=area_key)
            if str(item.get("roster_id") or "") in roster_ids and bool(item.get("valid_question", True))
        ]
        activity_events = [
            item for item in self._analytics.activity_events(end=window.end_utc, area_key=area_key)
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
        hourly = Counter()
        date_users: dict[str, set[str]] = defaultdict(set)
        date_questions = Counter()
        tasks = Counter()
        devices = Counter()
        modes = Counter()
        products = Counter()
        matrix = Counter()
        task_measured_count = 0
        device_measured_count = 0
        mode_measured_count = 0
        product_measured_count = 0
        product_candidate_count = 0
        product_resolved_count = 0
        product_unresolved_questions = 0
        display_timezone = ZoneInfo(window.timezone)
        for item in events:
            timestamp = _as_datetime(item.get("question_ts"))
            hour = timestamp.astimezone(display_timezone).hour if timestamp else None
            if hour is not None:
                hourly[f"{hour:02d}:00"] += 1
            day = str(item.get("question_date") or "")
            date_users[day].add(str(item.get("roster_id") or ""))
            date_questions[day] += 1
            item_tasks = _tasks_for_measured_item(item)
            if item_tasks:
                task_measured_count += 1
                for task in item_tasks:
                    tasks[task] += 1
            if device := _measured_dimension(item, "device_class"):
                devices[device] += 1
                device_measured_count += 1
            if mode := _measured_dimension(item, "mode"):
                modes[mode] += 1
                mode_measured_count += 1
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
            "scope": "global",
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
            "hourlyQuestions": [
                {"hour": key, "count": hourly.get(key, 0)}
                for key in _visible_hour_axis(
                    window,
                    data_through=data_through,
                    observed_hours=set(hourly),
                )
            ],
            "deviceDistribution": _distribution(devices, {"desktop": "PC", "mobile": "モバイル"}),
            "deviceMeasurement": _measurement_coverage(
                device_measured_count, len(events)
            ),
            "modeDistribution": _distribution(modes, {"internal": "社内モード", "websearch": "Web検索モード"}),
            "modeMeasurement": _measurement_coverage(
                mode_measured_count, len(events)
            ),
            "usageTrend": [
                {
                    "date": day,
                    "activeUsers": len(date_users[day]),
                    "questions": date_questions[day],
                    "isPartial": day == partial_date,
                }
                for day in _visible_date_axis(
                    window,
                    data_through=data_through,
                    observed_dates=set(date_questions),
                )
            ],
            "requestTasks": _distribution(
                tasks,
                {key: analytics_task_label(key) for key in tasks},
            ),
            "taskMeasurement": _measurement_coverage(task_measured_count, len(events)),
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
                **_measurement_coverage(product_measured_count, len(events)),
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
        freshness = self._freshness(self._publication_snapshot())
        roster = self._roster(AnalysisScope.USER_MAP)
        roster_ids = {str(item["roster_id"]) for item in roster}
        events = [
            item for item in self._analytics.overview_events(window=window)
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
            "scopeUserCount": len(roster),
            "freshness": freshness,
            "regions": regions,
        }

    def users(
        self,
        *,
        q: str = "",
        area_key: str = "",
        activity: str = "",
        window: MetricsTimeWindow,
    ) -> dict[str, Any]:
        freshness = self._freshness(self._publication_snapshot())
        roster = self._roster(AnalysisScope.USER_MAP, area_key=area_key)
        metrics = {str(item.get("roster_id") or ""): item for item in self._analytics.user_metrics()}
        activity_times: dict[str, list[datetime]] = defaultdict(list)
        roster_ids = {str(item["roster_id"]) for item in roster}
        for item in self._analytics.activity_events(
            end=window.end_utc,
            area_key=area_key,
        ):
            roster_id = str(item.get("roster_id") or "")
            timestamp = _as_datetime(item.get("question_ts"))
            if roster_id in roster_ids and timestamp is not None:
                activity_times[roster_id].append(timestamp)
        completion_by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in self._analytics.overview_events(window=window, area_key=area_key):
            if str(item.get("roster_id") or "") in roster_ids and bool(item.get("valid_question", True)):
                completion_by_user[str(item.get("roster_id") or "")].append(item)
        labels = {str(item.get("label_id") or ""): item for item in self._directory.list_labels(include_inactive=True)}
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
                "labels": _analytics_labels(list(user.get("label_ids", [])), labels),
                "lastActiveAt": (
                    parsed_last_active.isoformat()
                    if (parsed_last_active := _as_datetime(metric.get("last_active_at")))
                    else ""
                ),
                "activeDays7": int(metric.get("active_days_7") or 0),
                "userMessageCount7": int(metric.get("user_message_count_7") or 0),
                "completeDelivery": _complete_delivery_measurement(selected_events),
                "activity": level,
                "activityLabel": _ACTIVITY_LABELS[level],
            })
        rows.sort(key=lambda item: str(item.get("lastActiveAt") or ""), reverse=True)
        return {
            "scopeUserCount": len(roster),
            "freshness": freshness,
            "users": rows,
        }

    def user_detail(self, roster_id: str, *, window: MetricsTimeWindow) -> dict[str, Any]:
        publication = self._publication_snapshot()
        freshness = self._freshness(publication)
        data_through = _as_datetime(freshness.get("dataThrough"))
        user = self._directory.get_user(roster_id)
        if user is None or not membership_for(
            Department(user["department"]),
            is_active=bool(user["is_active"]),
        ).includes(AnalysisScope.USER_MAP):
            raise KeyError("user not found")
        events = self._analytics.user_detail_events(roster_id=roster_id, window=window)
        events = [item for item in events if bool(item.get("valid_question", True))]
        labels = {str(item.get("label_id") or ""): item for item in self._directory.list_labels(include_inactive=True)}
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
                for item in self._analytics.user_metrics()
                if str(item.get("roster_id") or "") == roster_id
            ),
            {},
        )
        last_active = _as_datetime(user_metric.get("last_active_at"))
        peer_roster = self._roster(AnalysisScope.USER_MAP)
        peer_ids = {str(item["roster_id"]) for item in peer_roster}
        peer_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in self._analytics.overview_events(window=window):
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
                **_measurement_coverage(product_measured_count, len(events)),
            },
            "tasks": _distribution(
                tasks,
                {key: analytics_task_label(key) for key in tasks},
            ),
            "taskMeasurement": _measurement_coverage(task_measured_count, len(events)),
            "questionCategories": _distribution(
                categories,
                {key: question_category_label(key) for key in categories},
            ),
            "questionCategoryMeasurement": _measurement_coverage(
                len(measured_category_rows), len(events)
            ),
            "modes": _distribution(modes, {"internal": "社内モード", "websearch": "Web検索モード"}),
            "modeMeasurement": _measurement_coverage(
                len(measured_modes), len(events)
            ),
            "devices": _distribution(devices, {"desktop": "PC", "mobile": "モバイル"}),
            "deviceMeasurement": _measurement_coverage(
                len(measured_devices), len(events)
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
        for item in peers:
            peer_rows = events.get(str(item["roster_id"]), [])
            if not peer_rows:
                continue
            measurement = _complete_delivery_measurement(peer_rows)
            if measurement["value"] is not None:
                complete_rates.append(float(measurement["value"]))
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
                "measuredCount": len(complete_rates),
                "totalCount": len(peers),
                "measurementState": _measurement_state(
                    len(complete_rates), len(peers)
                ),
            },
        }
