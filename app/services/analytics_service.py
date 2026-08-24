from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.analysis_scopes import AnalysisScope, Department, display_area, membership_for
from app.domain.question_categories import QuestionCategory
from app.settings import Settings
from app.time_window import MetricsTimeWindow


_ACTIVITY_ORDER = ("high", "middle", "low", "dormant")
_ACTIVITY_LABELS = {
    "high": "高アクティブ",
    "middle": "中アクティブ",
    "low": "低アクティブ",
    "dormant": "休眠ユーザー",
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


def _complete_delivery_rate(rows: list[dict[str, Any]]) -> float | None:
    """Never publish a rate calculated from a silently reduced denominator."""

    if not rows or any(item.get("measurement_available") is not True for item in rows):
        return None
    return _rate(sum(item.get("complete_delivery") is True for item in rows), len(rows))


def _complete_latency_p95(rows: list[dict[str, Any]]) -> int | None:
    if not rows or any(item.get("total_latency_ms") is None for item in rows):
        return None
    return _p95([int(item["total_latency_ms"]) for item in rows])


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
    dates = [value.astimezone(timezone_value).date() for value in question_times]
    count_3d = sum(0 <= (end_date - value).days <= 2 for value in dates)
    count_7d = sum(0 <= (end_date - value).days <= 6 for value in dates)
    count_14d = sum(0 <= (end_date - value).days <= 13 for value in dates)
    if count_3d >= 3:
        return "high"
    if 1 <= count_7d <= 2:
        return "middle"
    if count_14d >= 1:
        return "low"
    return "dormant"


class AnalyticsService:
    def __init__(
        self,
        *,
        analytics: Any,
        pipeline: Any,
        directory: Any,
        conversations: Any,
        settings: Settings,
    ) -> None:
        self._analytics = analytics
        self._pipeline = pipeline
        self._directory = directory
        self._conversations = conversations
        self._settings = settings

    def _freshness(self) -> tuple[str, str]:
        value = self._pipeline.data_through()
        if value is None:
            return "unavailable", ""
        now = datetime.now(timezone.utc)
        status = (
            "ready"
            if (now - value).total_seconds() <= self._settings.monitor_data_freshness_minutes * 60
            else "unavailable"
        )
        return status, value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def _roster(self, scope: AnalysisScope, *, area_key: str = "") -> list[dict[str, Any]]:
        users = self._directory.list_users(include_inactive=False)
        return [
            item
            for item in users
            if membership_for(Department(item["department"]), is_active=bool(item["is_active"])).includes(scope)
            and (not area_key or str(item.get("area_key") or "") == area_key)
        ]

    def overview(self, *, window: MetricsTimeWindow, area_key: str = "") -> dict[str, Any]:
        status, data_through = self._freshness()
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
        returning = sum(len(days) >= 2 for days in days_by_user.values())
        hourly = Counter()
        date_users: dict[str, set[str]] = defaultdict(set)
        date_questions = Counter()
        categories = Counter()
        devices = Counter()
        modes = Counter()
        products = Counter()
        matrix = Counter()
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
            category = QuestionCategory(
                str(item.get("primary_question_category") or "")
            ).value
            categories[category] += 1
            devices[str(item.get("device_class") or "unknown")] += 1
            modes[str(item.get("mode") or "unknown")] += 1
            candidate_count = int(item.get("product_candidate_count") or 0)
            resolved_count = int(item.get("product_resolved_count") or 0)
            product_candidate_count += candidate_count
            product_resolved_count += resolved_count
            product_unresolved_questions += resolved_count < candidate_count
            product = str(item.get("primary_product_name") or "").strip()
            if product:
                products[product] += 1
                matrix[(product, category)] += 1

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
            "status": status,
            "dataThrough": data_through,
            "kpis": {
                "activeUsers": len(active_ids),
                "adoptionRate": _rate(len(active_ids), len(roster)),
                "returnRate": _rate(returning, len(active_ids)),
                "questionsPerActiveUser": _rate(len(events), len(active_ids)),
                "completeDeliveryRate": _complete_delivery_rate(events),
                "p95LatencyMs": _complete_latency_p95(events),
            },
            "hourlyQuestions": [{"hour": key, "count": hourly.get(key, 0)} for key in (f"{hour:02d}:00" for hour in range(24))],
            "deviceDistribution": _distribution(devices, {"desktop": "PC", "mobile": "モバイル", "unknown": "不明"}),
            "modeDistribution": _distribution(modes, {"internal": "社内モード", "websearch": "Web検索モード"}),
            "usageTrend": [
                {"date": day, "activeUsers": len(date_users[day]), "questions": date_questions[day]}
                for day in sorted(date_questions)
            ],
            "questionCategories": _distribution(categories),
            "activityDistribution": [
                {"key": key, "label": _ACTIVITY_LABELS[key], "count": activity_counter.get(key, 0), "rate": _rate(activity_counter.get(key, 0), len(roster))}
                for key in _ACTIVITY_ORDER
            ],
            "activityByArea": [self._stacked_activity(label, values) for label, values in sorted(by_area.items())],
            "activityByRole": [self._stacked_activity(label, values) for label, values in sorted(by_role.items())],
            "topProducts": [{"label": key, "count": value} for key, value in products.most_common(10)],
            "productQuestionMatrix": [
                {"product": product, "category": category, "count": count}
                for (product, category), count in matrix.items()
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
        status, data_through = self._freshness()
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
                "returnRate": _rate(sum(len(value) >= 2 for value in days.values()), len(active)),
            })
        regions.sort(key=lambda item: (item["activeUsers"], item["questions"]), reverse=True)
        return {"status": status, "dataThrough": data_through, "regions": regions}

    def users(
        self,
        *,
        q: str = "",
        area_key: str = "",
        activity: str = "",
        window: MetricsTimeWindow,
    ) -> dict[str, Any]:
        status, data_through = self._freshness()
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
                "questionCount7": int(metric.get("question_count_7") or 0),
                "completeDeliveryRate": _complete_delivery_rate(selected_events),
                "activity": level,
                "activityLabel": _ACTIVITY_LABELS[level],
            })
        rows.sort(key=lambda item: str(item.get("lastActiveAt") or ""), reverse=True)
        return {"status": status, "dataThrough": data_through, "users": rows}

    def user_detail(self, roster_id: str, *, window: MetricsTimeWindow) -> dict[str, Any]:
        status, data_through = self._freshness()
        user = self._directory.get_user(roster_id)
        if user is None or not membership_for(Department(user["department"]), is_active=bool(user["is_active"])).includes(AnalysisScope.USER_MAP):
            raise KeyError("user not found")
        events = self._analytics.user_detail_events(roster_id=roster_id, window=window)
        labels = {str(item.get("label_id") or ""): item for item in self._directory.list_labels(include_inactive=True)}
        dates = {str(item.get("question_date") or "") for item in events}
        day_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in events:
            day_rows[str(item.get("question_date") or "")].append(item)
        products = Counter(str(item.get("primary_product_name") or "") for item in events if str(item.get("primary_product_name") or ""))
        product_candidate_count = sum(int(item.get("product_candidate_count") or 0) for item in events)
        product_resolved_count = sum(int(item.get("product_resolved_count") or 0) for item in events)
        product_unresolved_questions = sum(
            int(item.get("product_resolved_count") or 0)
            < int(item.get("product_candidate_count") or 0)
            for item in events
        )
        tasks = Counter(task for item in events for task in list(item.get("analytics_tasks") or []) if str(task or ""))
        categories = Counter(
            QuestionCategory(
                str(item.get("primary_question_category") or "")
            ).value
            for item in events
        )
        modes = Counter(str(item.get("mode") or "unknown") for item in events)
        devices = Counter(str(item.get("device_class") or "unknown") for item in events)
        user_metric = next(
            (
                item
                for item in self._analytics.user_metrics()
                if str(item.get("roster_id") or "") == roster_id
            ),
            {},
        )
        last_active = _as_datetime(user_metric.get("last_active_at"))
        conversations = self._conversations.list_conversations(chat_user_id=str(user.get("chat_user_id") or ""))
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
        return {
            "status": status,
            "dataThrough": data_through,
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
                "completeDeliveryRate": _complete_delivery_rate(events),
            },
            "comparisons": comparisons,
            "trend": [
                {
                    "date": day,
                    "questions": len(rows),
                    "completeDeliveryRate": _complete_delivery_rate(rows),
                }
                for day, rows in sorted(day_rows.items())
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
            },
            "tasks": _distribution(tasks),
            "questionCategories": _distribution(categories),
            "modes": _distribution(modes, {"internal": "社内モード", "websearch": "Web検索モード"}),
            "devices": _distribution(devices, {"desktop": "PC", "mobile": "モバイル", "unknown": "不明"}),
            "conversations": conversations,
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
        peers = [item for item in roster if str(item.get(field) or "-") == label]
        questions = [len(events.get(str(item["roster_id"]), [])) for item in peers]
        active_days = [
            len({str(event.get("question_date") or "") for event in events.get(str(item["roster_id"]), [])})
            for item in peers
        ]
        complete_rates: list[float] = []
        completion_coverage_complete = True
        for item in peers:
            peer_rows = events.get(str(item["roster_id"]), [])
            if not peer_rows:
                continue
            complete_rate = _complete_delivery_rate(peer_rows)
            if complete_rate is None:
                completion_coverage_complete = False
                continue
            complete_rates.append(complete_rate)
        return {
            "label": label,
            "peerCount": len(peers),
            "averageQuestions": sum(questions) / len(questions) if questions else None,
            "averageActiveDays": sum(active_days) / len(active_days) if active_days else None,
            "averageCompleteDeliveryRate": (
                sum(complete_rates) / len(complete_rates)
                if completion_coverage_complete and complete_rates
                else None
            ),
        }
