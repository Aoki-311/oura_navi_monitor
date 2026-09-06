from __future__ import annotations

import csv
import io
import json
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from app.csv_safety import safe_csv_cell
from app.domain.analytics_snapshot import roster_fingerprint
from app.domain.analysis_scopes import AnalysisScope, SCOPE_POLICY_VERSION, membership_for
from app.domain.roster_records import read_canonical_roster_collection
from app.refresh_policy import REFRESH_POLICY
from app.repositories.news_usage_repository import (
    NewsUsageConfiguration,
    NewsUsageRepositoryError,
)
from app.time_window import MetricsTimeWindow


EVENT_NAMES = frozenset(
    {
        "tab_view",
        "filter_change",
        "detail_view",
        "outbound_click",
        "export_started",
        "export_finished",
        "summary_view",
    }
)
CHANNELS = frozenset({"news", "society"})
ACTIVE_EVENT_NAMES = frozenset(
    {
        "tab_view",
        "filter_change",
        "detail_view",
        "outbound_click",
        "export_started",
    }
)
MAX_POPULAR_ARTICLES = 100


@lru_cache(maxsize=1)
def _dashboard_catalog() -> dict[str, Any]:
    # Generated from the producer's taxonomy and source catalog by
    # scripts/sync_news_usage_catalog.py; it supplies labels, never usage facts.
    path = Path(__file__).resolve().parents[1] / "contracts" / "news_usage_catalog.json"
    return json.loads(path.read_text(encoding="utf-8"))


class NewsUsageSnapshotConflictError(RuntimeError):
    def __init__(
        self, message: str, *, code: str = "news_usage_snapshot_conflict"
    ) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class NewsUsageQuery:
    channel: str = ""
    environment: str = ""
    business_unit: str = ""
    geography: str = ""
    category: str = ""
    society: str = ""
    query: str = ""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _search_text(value: Any) -> str:
    return " ".join(
        unicodedata.normalize("NFKC", str(value or "")).casefold().split()
    )


def _as_datetime(value: Any, *, field: str) -> datetime:
    if isinstance(value, datetime):
        resolved = value
    else:
        text = _text(value)
        if not text:
            raise NewsUsageSnapshotConflictError(f"{field} is missing")
        try:
            resolved = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise NewsUsageSnapshotConflictError(f"{field} is invalid") from exc
    if resolved.tzinfo is None:
        resolved = resolved.replace(tzinfo=timezone.utc)
    return resolved.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str:
    if value is None:
        return ""
    resolved = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return resolved.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _list(value: Any, *, field: str) -> list[str]:
    if value is None:
        return []
    parsed = value
    if isinstance(value, str):
        if not value.strip():
            return []
        try:
            parsed = json.loads(value)
        except ValueError as exc:
            raise NewsUsageSnapshotConflictError(f"{field} is invalid") from exc
    if not isinstance(parsed, (list, tuple)):
        raise NewsUsageSnapshotConflictError(f"{field} is invalid")
    return list(dict.fromkeys(_text(item) for item in parsed if _text(item)))


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _is_active_event(item: dict[str, Any]) -> bool:
    name = _text(item.get("event_name"))
    return name in ACTIVE_EVENT_NAMES or (
        name == "summary_view" and _text(item.get("trigger")) == "manual"
    )


def _export_operation_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        _text(item.get("roster_id")),
        _text(item.get("channel")),
        _text(item.get("operation_id")),
    )


def _event_date(item: dict[str, Any]) -> str:
    value = item.get("usage_date_jst")
    if isinstance(value, date):
        return value.isoformat()
    return _text(value)


def _count_rows(
    rows: Iterable[dict[str, Any]],
    *,
    key: Callable[[dict[str, Any]], Any],
    labels: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    actions: Counter[str] = Counter()
    users: dict[str, set[str]] = defaultdict(set)
    for item in rows:
        resolved = _text(key(item))
        if not resolved:
            continue
        actions[resolved] += 1
        users[resolved].add(_text(item.get("roster_id")))
    return [
        {
            "key": value,
            "label": (labels or {}).get(value, value),
            "actions": count,
            "activeUsers": len(users[value] - {""}),
        }
        for value, count in sorted(
            actions.items(), key=lambda pair: (-pair[1], pair[0])
        )
    ]


class NewsUsageService:
    def __init__(self, *, repository: Any, settings: Any) -> None:
        self._repository = repository
        self._settings = settings

    @staticmethod
    def _selection(query: NewsUsageQuery) -> dict[str, str]:
        return {
            "channel": query.channel,
            "environment": query.environment,
            "businessUnit": query.business_unit,
            "geography": query.geography,
            "category": query.category,
            "society": query.society,
            "query": query.query,
        }

    @staticmethod
    def _base_options(source_service: str = "") -> dict[str, Any]:
        return {
            "channels": [
                {"value": "news", "label": "ニュース"},
                {"value": "society", "label": "学会"},
            ],
            "environments": (
                [{"value": source_service, "label": source_service}]
                if source_service
                else []
            ),
            "businessUnits": [],
            "geographies": [
                {"value": "domestic", "label": "国内"},
                {"value": "overseas", "label": "海外"},
            ],
            "categories": [],
            "societies": [],
        }

    @staticmethod
    def _empty_behaviors() -> dict[str, Any]:
        return {
            "kpis": {
                "scopeUsers": 0,
                "activeUsers": 0,
                "adoptionRate": None,
                "totalActions": 0,
                "tabViews": 0,
                "filterChanges": 0,
                "detailViews": 0,
                "outboundClicks": 0,
                "exportStarts": 0,
                "manualSummaryViews": 0,
            },
            "trend": [],
            "tabBehavior": {"views": 0, "activeUsers": 0, "byChannel": []},
            "filterBehavior": {
                "changes": 0,
                "activeUsers": 0,
                "searchChanges": 0,
                "searchEnabledAfterChange": 0,
                "byChangedField": [],
            },
            "detailBehavior": {
                "views": 0,
                "activeUsers": 0,
                "totalArticles": 0,
                "isTruncated": False,
                "popularArticles": [],
            },
            "outboundBehavior": {
                "clicks": 0,
                "activeUsers": 0,
                "totalArticles": 0,
                "isTruncated": False,
                "byLinkKind": [],
                "popularArticles": [],
            },
            "exportBehavior": {
                "started": 0,
                "activeUsers": 0,
                "finished": 0,
                "pending": 0,
                "orphanFinished": 0,
                "downloadHandoffRate": None,
                "results": [],
            },
            "summaryBehavior": {
                "manualViews": 0,
                "manualUsers": 0,
                "automaticViews": 0,
                "automaticUsers": 0,
            },
            "organizations": {"users": [], "departments": [], "regions": []},
        }

    def _empty_report(
        self,
        *,
        window: MetricsTimeWindow,
        query: NewsUsageQuery,
        availability: str,
        reason_code: str,
        message: str,
        source_service: str = "",
        measurement_start_at: datetime | None = None,
        history_coverage: str = "none",
    ) -> dict[str, Any]:
        return {
            "contractVersion": "news_usage_report_v1",
            "scope": "global",
            "scopePolicyVersion": SCOPE_POLICY_VERSION,
            "rosterFingerprint": "",
            "contentFingerprint": "",
            "publishedRunId": "",
            "rosterSnapshotRunId": "",
            "sourceService": source_service,
            "windowStart": _iso(window.start_utc),
            "windowEnd": _iso(window.end_utc),
            "windowTimezone": "Asia/Tokyo",
            "state": {
                "availability": availability,
                "usage": "not_measured",
                "freshness": "unknown",
                "historyCoverage": history_coverage,
                "publicationCoverage": "none",
                "reasonCode": reason_code,
                "message": message,
                "measurementStartAt": _iso(measurement_start_at),
                "dataThrough": "",
                "publishedAt": "",
            },
            "diagnostics": {
                "state": "not_applicable",
                "unmatchedEventCount": 0,
                "errorCode": "",
            },
            "selection": self._selection(query),
            "filterOptions": self._base_options(source_service),
            **self._empty_behaviors(),
        }

    @staticmethod
    def _validate_publication(
        publication: dict[str, Any], configuration: NewsUsageConfiguration,
        scope: AnalysisScope = AnalysisScope.GLOBAL,
    ) -> dict[str, Any]:
        required_text = (
            "published_run_id",
            "roster_snapshot_run_id",
            "source_service",
            "scope_policy_version",
            f"{scope.value}_roster_fingerprint",
            f"{scope.value}_content_fingerprint",
        )
        if any(not _text(publication.get(field)) for field in required_text):
            raise NewsUsageSnapshotConflictError(
                "news usage publication receipt is incomplete"
            )
        if _text(publication.get("status")) != "succeeded":
            raise NewsUsageSnapshotConflictError(
                "news usage publication is not successful"
            )
        if _text(publication.get("source")) != "news_usage":
            raise NewsUsageSnapshotConflictError("news usage source is invalid")
        if _text(publication.get("source_service")) != configuration.source_service:
            raise NewsUsageSnapshotConflictError(
                "news usage source service does not match configuration"
            )
        if _text(publication.get("scope_policy_version")) != SCOPE_POLICY_VERSION:
            raise NewsUsageSnapshotConflictError(
                "news usage scope policy does not match the application"
            )
        measurement_start = _as_datetime(
            publication.get("measurement_start_at"), field="measurement_start_at"
        )
        data_through = _as_datetime(
            publication.get("data_through"), field="data_through"
        )
        published_at = _as_datetime(
            publication.get("updated_at"), field="updated_at"
        )
        if configuration.measurement_start_at != measurement_start:
            raise NewsUsageSnapshotConflictError(
                "news usage measurement start does not match configuration"
            )
        if data_through < measurement_start:
            raise NewsUsageSnapshotConflictError(
                "news usage watermark precedes measurement start"
            )
        return {
            **publication,
            "measurement_start_at": measurement_start,
            "data_through": data_through,
            "updated_at": published_at,
        }

    @staticmethod
    def _publication_key(publication: dict[str, Any]) -> tuple[str, ...]:
        return (
            _text(publication.get("published_run_id")),
            _text(publication.get("roster_snapshot_run_id")),
            _iso(publication.get("data_through")),
            _iso(publication.get("measurement_start_at")),
            _text(publication.get("source_service")),
            _text(publication.get("scope_policy_version")),
            _text(publication.get("global_roster_fingerprint")),
            _text(publication.get("global_content_fingerprint")),
            _text(publication.get("user_map_roster_fingerprint")),
            _text(publication.get("user_map_content_fingerprint")),
            _iso(publication.get("updated_at")),
        )

    @staticmethod
    def _roster_snapshot(
        raw_rows: list[dict[str, Any]], publication: dict[str, Any],
        scope: AnalysisScope = AnalysisScope.GLOBAL,
    ) -> list[dict[str, Any]]:
        roster_run_id = _text(publication.get("roster_snapshot_run_id"))
        if not raw_rows:
            raise NewsUsageSnapshotConflictError(
                "referenced roster snapshot is unavailable"
            )
        run_ids = {_text(row.get("snapshot_run_id")) for row in raw_rows}
        if run_ids != {roster_run_id}:
            raise NewsUsageSnapshotConflictError(
                "referenced roster snapshot does not match publication"
            )
        diagnostic_fingerprints = {
            _text(row.get("roster_diagnostic_fingerprint")) for row in raw_rows
        }
        if len(diagnostic_fingerprints) != 1:
            raise NewsUsageSnapshotConflictError(
                "referenced roster has mixed diagnostics"
            )
        projected: list[dict[str, Any]] = []
        for raw in raw_rows:
            projected.append(
                {
                    **raw,
                    "label_ids": _list(
                        raw.get("label_ids_json"), field="label_ids_json"
                    ),
                }
            )
        records = read_canonical_roster_collection(projected)
        if len(records.analytics_records) != len(projected):
            raise NewsUsageSnapshotConflictError(
                "referenced roster contains invalid rows"
            )
        for record in records.analytics_records:
            structural = membership_for(
                role=record.value.get("role"),
                department=record.value.get("department", ""),
                is_active=True,
            )
            if (
                record.value.get("global_scope_enabled")
                is not structural.global_enabled
                or record.value.get("user_map_scope_enabled")
                is not structural.user_map_enabled
            ):
                raise NewsUsageSnapshotConflictError(
                    "referenced roster contains invalid scope flags"
                )
        scope_rows = [
            record.value
            for record in records.analytics_records
            if record.value.get("is_active") is True
            and record.value.get(f"{scope.value}_scope_enabled") is True
        ]
        try:
            fingerprint = roster_fingerprint(
                scope_rows,
                diagnostic_fingerprint=diagnostic_fingerprints.pop(),
            )
        except ValueError as exc:
            raise NewsUsageSnapshotConflictError(
                "referenced roster receipt input is invalid"
            ) from exc
        if fingerprint != _text(publication.get(f"{scope.value}_roster_fingerprint")):
            raise NewsUsageSnapshotConflictError(
                "referenced roster fingerprint does not match publication"
            )
        return scope_rows

    @staticmethod
    def _validate_events(
        raw_rows: list[dict[str, Any]], publication: dict[str, Any]
    ) -> list[dict[str, Any]]:
        unique: dict[str, dict[str, Any]] = {}
        roster_run_id = _text(publication.get("roster_snapshot_run_id"))
        publication_run_id = _text(publication.get("published_run_id"))
        source_service = _text(publication.get("source_service"))
        local = ZoneInfo("Asia/Tokyo")
        for raw in raw_rows:
            item = dict(raw)
            event_id = _text(item.get("event_id"))
            if (
                not event_id
                or not _text(item.get("usage_event_id"))
                or not _text(item.get("roster_id"))
            ):
                raise NewsUsageSnapshotConflictError(
                    "published usage event identity is incomplete"
                )
            event_name = _text(item.get("event_name"))
            channel = _text(item.get("channel"))
            if event_name not in EVENT_NAMES or channel not in CHANNELS:
                raise NewsUsageSnapshotConflictError(
                    "published usage event type is invalid"
                )
            occurred_at = _as_datetime(item.get("occurred_at"), field="occurred_at")
            if _event_date(item) != occurred_at.astimezone(local).date().isoformat():
                raise NewsUsageSnapshotConflictError(
                    "published usage event has an invalid JST date"
                )
            if (
                _text(item.get("publication_run_id")) != publication_run_id
                or _text(item.get("roster_snapshot_run_id")) != roster_run_id
                or _text(item.get("source_service")) != source_service
            ):
                raise NewsUsageSnapshotConflictError(
                    "published usage event does not match its pointer"
                )
            item["occurred_at"] = occurred_at
            for field in (
                "filter_domain_keys",
                "filter_source_ids",
                "filter_category_keys",
                "filter_event_types",
                "changed_fields",
            ):
                item[field] = _list(item.get(field), field=field)
            previous = unique.get(event_id)
            if previous is not None:
                if json.dumps(previous, default=str, sort_keys=True) != json.dumps(
                    item, default=str, sort_keys=True
                ):
                    raise NewsUsageSnapshotConflictError(
                        "published usage event identity is conflicting"
                    )
                continue
            unique[event_id] = item
        return sorted(
            unique.values(),
            key=lambda row: (row["occurred_at"], _text(row.get("event_id"))),
        )

    @staticmethod
    def _filter_options(
        rows: list[dict[str, Any]], source_service: str
    ) -> dict[str, Any]:
        domains: set[str] = set()
        categories: set[str] = set()
        geographies: set[str] = set()
        societies: set[str] = set()
        for item in rows:
            domains.update(item.get("filter_domain_keys") or [])
            categories.update(item.get("filter_category_keys") or [])
            domains.add(_text(item.get("content_domain_key")))
            categories.add(_text(item.get("content_category_key")))
            geographies.update(
                {
                    _text(item.get("content_geography_scope")),
                    _text(item.get("filter_news_geography_scope")),
                }
            )
            if _text(item.get("channel")) == "society":
                societies.update(item.get("filter_source_ids") or [])
                societies.add(_text(item.get("content_source_id")))

        def options(values: set[str], labels: dict[str, str] | None = None):
            return [
                {"value": value, "label": (labels or {}).get(value, value)}
                for value in sorted(values - {""})
            ]

        return {
            "channels": [
                {"value": "news", "label": "ニュース"},
                {"value": "society", "label": "学会"},
            ],
            "environments": [
                {"value": source_service, "label": source_service}
            ],
            "businessUnits": options(domains),
            "geographies": options(
                geographies, {"domestic": "国内", "overseas": "海外"}
            ),
            "categories": options(categories),
            "societies": options(societies),
        }

    @staticmethod
    def _attribute_export_terminals(
        rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Use the start snapshot to classify a minimal terminal event.

        Older v1 terminals contain only operation/result context. Copying the
        already-published start filters lets a BU/geography report keep the real
        terminal result without inventing success or changing its action time.
        A terminal whose start is outside the read window remains an explicit
        orphan and is never assigned guessed filter values.
        """

        starts: dict[tuple[str, str, str], dict[str, Any]] = {}
        for item in rows:
            if _text(item.get("event_name")) != "export_started":
                continue
            key = _export_operation_key(item)
            if not all(key):
                raise NewsUsageSnapshotConflictError(
                    "published export start identity is invalid"
                )
            # Rows are already sorted by action time and event ID. Keep the
            # earliest start for a duplicated producer delivery without making
            # unrelated users' report unavailable.
            starts.setdefault(key, item)
        fields = (
            "filter_snapshot_present",
            "filter_domain_keys",
            "filter_source_ids",
            "filter_category_keys",
            "filter_event_types",
            "filter_news_geography_scope",
            "filter_start_date",
            "filter_end_date",
            "filter_has_query",
        )
        attributed = []
        for item in rows:
            if (
                _text(item.get("event_name")) != "export_finished"
                or item.get("filter_snapshot_present") is True
            ):
                attributed.append(item)
                continue
            start = starts.get(_export_operation_key(item))
            if start is None:
                attributed.append(item)
                continue
            attributed.append(
                {
                    **item,
                    **{field: start.get(field) for field in fields},
                }
            )
        return attributed

    @staticmethod
    def _matches(
        item: dict[str, Any],
        query: NewsUsageQuery,
        roster_by_id: dict[str, dict[str, Any]],
    ) -> bool:
        if query.channel and _text(item.get("channel")) != query.channel:
            return False
        if query.environment and _text(item.get("source_service")) != query.environment:
            return False
        dimensions = {
            "business_unit": {
                _text(item.get("content_domain_key")),
                *(item.get("filter_domain_keys") or []),
            },
            "geography": {
                _text(item.get("content_geography_scope")),
                _text(item.get("filter_news_geography_scope")),
            },
            "category": {
                _text(item.get("content_category_key")),
                *(item.get("filter_category_keys") or []),
            },
            "society": {
                _text(item.get("content_source_id")),
                *(item.get("filter_source_ids") or []),
            },
        }
        for field, values in dimensions.items():
            selected = _text(getattr(query, field))
            if selected and selected not in values:
                return False
        needle = _search_text(query.query)
        if not needle:
            return True
        roster = roster_by_id.get(_text(item.get("roster_id")), {})
        haystack = " ".join(
            _search_text(value)
            for value in (
                item.get("content_event_id"),
                item.get("content_event_version"),
                item.get("content_source_id"),
                item.get("content_category_key"),
                item.get("content_domain_key"),
                roster.get("roster_id"),
                roster.get("name"),
                roster.get("area"),
                roster.get("area_key"),
                roster.get("workplace"),
                roster.get("role"),
                roster.get("department"),
            )
        )
        return needle in haystack

    @staticmethod
    def _article_rows(
        rows: list[dict[str, Any]], *, primary_action: str
    ) -> tuple[list[dict[str, Any]], int]:
        grouped: dict[tuple[str, ...], dict[str, Any]] = {}
        for item in rows:
            content_id = _text(item.get("content_event_id"))
            if not content_id:
                continue
            key = (
                content_id,
                _text(item.get("content_event_version")),
                _text(item.get("channel")),
                _text(item.get("content_domain_key")),
                _text(item.get("content_geography_scope")),
                _text(item.get("content_source_id")),
                _text(item.get("content_category_key")),
            )
            aggregate = grouped.setdefault(
                key,
                {
                    "contentEventId": key[0],
                    "contentEventVersion": key[1],
                    "channel": key[2],
                    "businessUnit": key[3],
                    "geography": key[4],
                    "sourceId": key[5],
                    "category": key[6],
                    "detailViews": 0,
                    "outboundClicks": 0,
                    "_users": set(),
                },
            )
            if _text(item.get("event_name")) == "detail_view":
                aggregate["detailViews"] += 1
            if _text(item.get("event_name")) == "outbound_click":
                aggregate["outboundClicks"] += 1
            aggregate["_users"].add(_text(item.get("roster_id")))
        output = []
        for aggregate in grouped.values():
            users = aggregate.pop("_users")
            aggregate["activeUsers"] = len(users - {""})
            output.append(aggregate)
        sort_field = "detailViews" if primary_action == "detail" else "outboundClicks"
        output.sort(
            key=lambda row: (
                -int(row[sort_field]),
                -int(row["activeUsers"]),
                row["contentEventId"],
                row["contentEventVersion"],
            )
        )
        return output[:MAX_POPULAR_ARTICLES], len(output)

    @staticmethod
    def _organization_rows(
        roster: list[dict[str, Any]],
        active_rows: list[dict[str, Any]],
        *,
        key_field: str,
        label_field: str,
    ) -> list[dict[str, Any]]:
        population: Counter[str] = Counter()
        labels: dict[str, str] = {}
        for item in roster:
            key = _text(item.get(key_field))
            if not key:
                continue
            population[key] += 1
            labels[key] = _text(item.get(label_field)) or key
        actions: Counter[str] = Counter()
        users: dict[str, set[str]] = defaultdict(set)
        roster_by_id = {_text(item.get("roster_id")): item for item in roster}
        for event in active_rows:
            roster_row = roster_by_id.get(_text(event.get("roster_id")))
            if not roster_row:
                continue
            key = _text(roster_row.get(key_field))
            if not key:
                continue
            actions[key] += 1
            users[key].add(_text(roster_row.get("roster_id")))
        return [
            {
                "key": key,
                "label": labels[key],
                "scopeUsers": population[key],
                "activeUsers": len(users[key] - {""}),
                "actions": actions[key],
                "adoptionRate": _rate(len(users[key] - {""}), population[key]),
            }
            for key in sorted(
                population,
                key=lambda value: (-len(users[value] - {""}), labels[value]),
            )
        ]

    @staticmethod
    def _user_rows(
        roster: list[dict[str, Any]],
        active_rows: list[dict[str, Any]],
        query: NewsUsageQuery,
    ) -> list[dict[str, Any]]:
        events_by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in active_rows:
            events_by_user[_text(item.get("roster_id"))].append(item)
        needle = _search_text(query.query)
        output = []
        for item in roster:
            roster_id = _text(item.get("roster_id"))
            events = events_by_user.get(roster_id, [])
            if needle:
                roster_match = needle in " ".join(
                    _search_text(item.get(field))
                    for field in (
                        "roster_id",
                        "name",
                        "area",
                        "area_key",
                        "workplace",
                        "role",
                        "department",
                    )
                )
                if not roster_match and not events:
                    continue
            timestamps = [event["occurred_at"] for event in events]
            output.append(
                {
                    "rosterId": roster_id,
                    "name": _text(item.get("name")),
                    "area": _text(item.get("area")),
                    "areaKey": _text(item.get("area_key")),
                    "workplace": _text(item.get("workplace")),
                    "role": _text(item.get("role")),
                    "department": _text(item.get("department")),
                    "actions": len(events),
                    "activeDays": len({_event_date(event) for event in events}),
                    "lastActiveAt": _iso(max(timestamps)) if timestamps else "",
                }
            )
        output.sort(
            key=lambda row: (
                -row["actions"],
                row["name"],
                row["rosterId"],
            )
        )
        return output

    @staticmethod
    def _trend(
        rows: list[dict[str, Any]],
        *,
        window: MetricsTimeWindow,
        measurement_start: datetime,
        data_through: datetime,
    ) -> list[dict[str, Any]]:
        effective_start = max(window.start_utc, measurement_start)
        effective_end = min(window.end_utc, data_through)
        if effective_end <= effective_start:
            return []
        local = ZoneInfo("Asia/Tokyo")
        first = effective_start.astimezone(local).date()
        last = (effective_end - timedelta(microseconds=1)).astimezone(local).date()
        rows_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in rows:
            rows_by_date[_event_date(item)].append(item)
        output = []
        current = first
        while current <= last:
            key = current.isoformat()
            day_rows = rows_by_date.get(key, [])
            active = [item for item in day_rows if _is_active_event(item)]
            local_start = datetime.combine(current, datetime.min.time(), tzinfo=local)
            local_end = local_start + timedelta(days=1)
            is_partial = (
                effective_start > local_start.astimezone(timezone.utc)
                or effective_end < local_end.astimezone(timezone.utc)
            )
            output.append(
                {
                    "date": key,
                    "activeUsers": len(
                        {_text(item.get("roster_id")) for item in active} - {""}
                    ),
                    "tabViews": sum(
                        _text(item.get("event_name")) == "tab_view"
                        for item in day_rows
                    ),
                    "filterChanges": sum(
                        _text(item.get("event_name")) == "filter_change"
                        for item in day_rows
                    ),
                    "detailViews": sum(
                        _text(item.get("event_name")) == "detail_view"
                        for item in day_rows
                    ),
                    "outboundClicks": sum(
                        _text(item.get("event_name")) == "outbound_click"
                        for item in day_rows
                    ),
                    "exportStarts": sum(
                        _text(item.get("event_name")) == "export_started"
                        for item in day_rows
                    ),
                    "manualSummaryViews": sum(
                        _text(item.get("event_name")) == "summary_view"
                        and _text(item.get("trigger")) == "manual"
                        for item in day_rows
                    ),
                    "isPartial": is_partial,
                }
            )
            current += timedelta(days=1)
        return output

    def dashboard(
        self, *, window: MetricsTimeWindow, roster_id: str = "",
        area_key: str = "", now: datetime | None = None,
    ) -> dict[str, Any]:
        """Serve overview and personal cards from the same published fact owner."""
        scope = AnalysisScope.USER_MAP if roster_id else AnalysisScope.GLOBAL
        payload = self.report(
            window=window, now=now, _scope=scope,
            _roster_id=roster_id, _area_key=area_key if not roster_id else "",
            _dashboard=True,
        )
        if payload["contractVersion"] == "news_usage_dashboard_v1":
            return payload
        # The legacy reader's explicit unavailable states contain no measured
        # facts. Dashboard consumers receive null totals instead of false zeros.
        return {
            "contractVersion": "news_usage_dashboard_v1",
            "scope": scope.value,
            "rosterId": roster_id,
            "windowStart": payload["windowStart"],
            "windowEnd": payload["windowEnd"],
            "state": payload["state"],
            "publishedRunId": payload["publishedRunId"],
            "rosterFingerprint": payload["rosterFingerprint"],
            "totals": None,
            "trend": [],
            "newsCategories": [],
            "societyCategories": [],
        }

    @staticmethod
    def _dashboard_report(
        *, rows: list[dict[str, Any]], window: MetricsTimeWindow,
        publication: dict[str, Any], measurement_start: datetime,
        scope: AnalysisScope, roster_id: str, now: datetime | None,
    ) -> dict[str, Any]:
        catalog = _dashboard_catalog()
        data_through = publication["data_through"]
        start = max(window.start_utc, measurement_start)
        end = min(window.end_utc, data_through)
        metrics = (
            "tabViews", "newsTabViews", "societyTabViews",
            "contentClicks", "newsContentClicks", "societyContentClicks",
        )
        totals = {key: 0 for key in metrics}
        totals.update(newsDomesticClicks=0, newsOverseasClicks=0,
                      newsUnknownGeographyClicks=0)
        days: dict[str, dict[str, Any]] = {}
        local = ZoneInfo("Asia/Tokyo")
        day = start.astimezone(local).date()
        last = (end - timedelta(microseconds=1)).astimezone(local).date()
        while end > start and day <= last:
            key = day.isoformat()
            days[key] = {"date": key, **{field: 0 for field in metrics}}
            day += timedelta(days=1)

        def news_category(key: str, label: str) -> dict[str, Any]:
            return {
                "key": key, "label": label, "clicks": 0, "domesticClicks": 0,
                "overseasClicks": 0, "unknownGeographyClicks": 0,
            }

        news = {
            item["key"]: news_category(item["key"], item["label"])
            for item in catalog["newsEventTypes"]
        }
        societies = {
            item["key"]: {
                "key": item["key"], "label": item["label"],
                "clicks": 0, "sources": {},
            }
            for item in catalog["societyCategories"]
        }
        source_labels = {item["key"]: item["label"] for item in catalog["societySources"]}
        for item in rows:
            if not start <= item["occurred_at"] < end:
                continue
            channel = _text(item.get("channel"))
            name = _text(item.get("event_name"))
            prefix = "news" if channel == "news" else "society"
            bucket = days[_event_date(item)]
            if name == "tab_view":
                for target in (totals, bucket):
                    target["tabViews"] += 1
                    target[f"{prefix}TabViews"] += 1
            is_click = name == "detail_view" or (
                name == "outbound_click" and item.get("link_kind") == "primary"
            )
            if not is_click:
                continue
            for target in (totals, bucket):
                target["contentClicks"] += 1
                target[f"{prefix}ContentClicks"] += 1
            if channel == "news":
                key = _text(item.get("content_event_type")) or "__unclassified__"
                category = news.setdefault(
                    key, news_category(key, "未分類" if key == "__unclassified__" else key)
                )
                category["clicks"] += 1
                geography = _text(item.get("content_geography_scope"))
                axis = {"domestic": "domestic", "overseas": "overseas"}.get(
                    geography, "unknownGeography"
                )
                category[f"{axis}Clicks"] += 1
                totals[f"news{axis[0].upper()}{axis[1:]}Clicks"] += 1
            else:
                # Article metadata owns attribution. A selected filter or a
                # missing category never invents readership of every source.
                key = _text(item.get("content_category_key")) or "__unclassified__"
                category = societies.setdefault(
                    key, {"key": key, "label": "未分類" if key == "__unclassified__" else key,
                          "clicks": 0, "sources": {}},
                )
                category["clicks"] += 1
                source = _text(item.get("content_source_id")) or "__unclassified__"
                source_row = category["sources"].setdefault(
                    source, {"key": source, "label": source_labels.get(
                        source, "未分類" if source == "__unclassified__" else source
                    ), "clicks": 0},
                )
                source_row["clicks"] += 1

        def ranked(values):
            return sorted(values, key=lambda item: (-item["clicks"], item["key"]))

        society_rows = [
            {**item, "sources": ranked(item["sources"].values())}
            for item in ranked(societies.values())
        ]
        has_usage = totals["tabViews"] + totals["contentClicks"] > 0
        return {
            "contractVersion": "news_usage_dashboard_v1",
            "scope": scope.value, "rosterId": roster_id,
            "windowStart": _iso(window.start_utc), "windowEnd": _iso(window.end_utc),
            "publishedRunId": _text(publication["published_run_id"]),
            "rosterFingerprint": _text(publication[f"{scope.value}_roster_fingerprint"]),
            "state": {
                "availability": "available",
                "usage": "has_usage" if has_usage else "no_usage",
                "freshness": NewsUsageService._freshness(data_through, now=now),
                "historyCoverage": "partial" if window.start_utc < measurement_start else "full",
                "publicationCoverage": "full" if data_through >= window.end_utc else "partial",
                "reasonCode": "complete" if has_usage else "no_usage",
                "message": "選択期間の利用回数を表示しています。" if has_usage
                           else "公開済み範囲に利用記録はありません。",
                "measurementStartAt": _iso(measurement_start),
                "dataThrough": _iso(data_through),
                "publishedAt": _iso(publication["updated_at"]),
            },
            "totals": totals, "trend": list(days.values()),
            "newsCategories": ranked(news.values()), "societyCategories": society_rows,
        }

    def report(
        self,
        *,
        window: MetricsTimeWindow,
        query: NewsUsageQuery | None = None,
        now: datetime | None = None,
        _scope: AnalysisScope = AnalysisScope.GLOBAL,
        _roster_id: str = "",
        _area_key: str = "",
        _dashboard: bool = False,
    ) -> dict[str, Any]:
        selection = query or NewsUsageQuery()
        configuration = self._repository.configuration()
        if configuration.state == "disabled":
            return self._empty_report(
                window=window,
                query=selection,
                availability="not_enabled",
                reason_code="not_enabled",
                message="News / 学会の利用計測はまだ有効化されていません。",
            )
        if configuration.state == "invalid":
            return self._empty_report(
                window=window,
                query=selection,
                availability="unavailable",
                reason_code=configuration.error_code or "invalid_config",
                message="News / 学会の利用計測設定が不完全です。",
                source_service=configuration.source_service,
            )
        measurement_start = configuration.measurement_start_at
        if measurement_start is None:
            raise NewsUsageSnapshotConflictError(
                "enabled news usage configuration has no measurement start"
            )
        if window.end_utc <= measurement_start:
            return self._empty_report(
                window=window,
                query=selection,
                availability="before_measurement",
                reason_code="before_measurement",
                message="選択期間は利用計測の開始前です。過去の利用数は推測しません。",
                source_service=configuration.source_service,
                measurement_start_at=measurement_start,
            )
        try:
            publication = self._repository.publication_snapshot(
                source_service=configuration.source_service
            )
        except NewsUsageRepositoryError as error:
            return self._empty_report(
                window=window,
                query=selection,
                availability="unavailable",
                reason_code=error.code,
                message="News / 学会の公開済み利用データを確認できません。",
                source_service=configuration.source_service,
                measurement_start_at=measurement_start,
                history_coverage=(
                    "partial" if window.start_utc < measurement_start else "full"
                ),
            )
        if not publication:
            return self._empty_report(
                window=window,
                query=selection,
                availability="unavailable",
                reason_code="never_published",
                message="News / 学会の成功済み利用データはまだ公開されていません。",
                source_service=configuration.source_service,
                measurement_start_at=measurement_start,
                history_coverage=(
                    "partial" if window.start_utc < measurement_start else "full"
                ),
            )
        publication = self._validate_publication(publication, configuration, _scope)
        data_through = publication["data_through"]
        history_coverage = (
            "partial" if window.start_utc < measurement_start else "full"
        )
        if data_through <= max(window.start_utc, measurement_start):
            payload = self._empty_report(
                window=window,
                query=selection,
                availability="unavailable",
                reason_code="window_not_published",
                message="選択期間まで利用データが公開されていません。",
                source_service=configuration.source_service,
                measurement_start_at=measurement_start,
                history_coverage=history_coverage,
            )
            payload.update(
                {
                    "scopePolicyVersion": _text(
                        publication.get("scope_policy_version")
                    ),
                    "rosterFingerprint": _text(
                        publication.get("global_roster_fingerprint")
                    ),
                    "contentFingerprint": _text(
                        publication.get("global_content_fingerprint")
                    ),
                    "publishedRunId": _text(publication.get("published_run_id")),
                    "rosterSnapshotRunId": _text(
                        publication.get("roster_snapshot_run_id")
                    ),
                }
            )
            payload["state"].update(
                {
                    "freshness": self._freshness(data_through, now=now),
                    "dataThrough": _iso(data_through),
                    "publishedAt": _iso(publication["updated_at"]),
                }
            )
            return payload
        try:
            raw_roster = self._repository.published_roster_snapshot(
                roster_snapshot_run_id=_text(
                    publication.get("roster_snapshot_run_id")
                )
            )
            roster = self._roster_snapshot(raw_roster, publication, _scope)
            # Verify the complete scope receipt first. Area selection uses the
            # same canonical roster keys as the Chat overview, never event metadata.
            if _area_key:
                roster = [
                    item for item in roster
                    if str(item.get("area_key") or "") == _area_key
                ]
            if _roster_id and not any(
                _text(item.get("roster_id")) == _roster_id for item in roster
            ):
                raise KeyError("user not found")
            raw_events = self._repository.published_events(
                window=window,
                published_run_id=_text(publication.get("published_run_id")),
                roster_snapshot_run_id=_text(
                    publication.get("roster_snapshot_run_id")
                ),
                publication_data_through=data_through,
                source_service=configuration.source_service,
                scope=_scope,
                roster_id=_roster_id,
                area_key=_area_key,
            )
        except NewsUsageRepositoryError as error:
            return self._empty_report(
                window=window,
                query=selection,
                availability="unavailable",
                reason_code=error.code,
                message="News / 学会の公開済み利用データを読み込めません。",
                source_service=configuration.source_service,
                measurement_start_at=measurement_start,
                history_coverage=history_coverage,
            )
        try:
            confirmed_publication = self._repository.publication_snapshot(
                source_service=configuration.source_service
            )
        except NewsUsageRepositoryError as error:
            raise NewsUsageSnapshotConflictError(
                "news usage publication could not be confirmed",
                code="snapshot_unconfirmed",
            ) from error
        if not confirmed_publication:
            raise NewsUsageSnapshotConflictError(
                "news usage publication disappeared during the read",
                code="snapshot_changed",
            )
        confirmed_publication = self._validate_publication(
            confirmed_publication, configuration, _scope
        )
        if self._publication_key(confirmed_publication) != self._publication_key(
            publication
        ):
            raise NewsUsageSnapshotConflictError(
                "news usage publication changed during the read",
                code="snapshot_changed",
            )
        events = self._attribute_export_terminals(
            self._validate_events(raw_events, publication)
        )
        roster_by_id = {_text(item.get("roster_id")): item for item in roster}
        filtered = [
            item
            for item in events
            if _text(item.get("roster_id")) in roster_by_id
            and (not _roster_id or _text(item.get("roster_id")) == _roster_id)
            and self._matches(item, selection, roster_by_id)
        ]
        active = [item for item in filtered if _is_active_event(item)]
        if _dashboard:
            return self._dashboard_report(
                rows=filtered, window=window, publication=publication,
                measurement_start=measurement_start, scope=_scope,
                roster_id=_roster_id, now=now,
            )
        diagnostics = self._repository.unmatched_event_diagnostics(
            window=window,
            publication_data_through=data_through,
        )
        freshness = self._freshness(data_through, now=now)
        publication_coverage = (
            "full" if data_through >= window.end_utc else "partial"
        )
        usage = "has_usage" if active else "no_usage"
        report = {
            "contractVersion": "news_usage_report_v1",
            "scope": "global",
            "scopePolicyVersion": _text(publication.get("scope_policy_version")),
            "rosterFingerprint": _text(
                publication.get("global_roster_fingerprint")
            ),
            "contentFingerprint": _text(
                publication.get("global_content_fingerprint")
            ),
            "publishedRunId": _text(publication.get("published_run_id")),
            "rosterSnapshotRunId": _text(
                publication.get("roster_snapshot_run_id")
            ),
            "sourceService": configuration.source_service,
            "windowStart": _iso(window.start_utc),
            "windowEnd": _iso(window.end_utc),
            "windowTimezone": "Asia/Tokyo",
            "state": {
                "availability": "available",
                "usage": usage,
                "freshness": freshness,
                "historyCoverage": history_coverage,
                "publicationCoverage": publication_coverage,
                "reasonCode": "complete" if active else "no_usage",
                "message": (
                    "選択条件の利用記録を表示しています。"
                    if active
                    else "公開済み範囲に、選択条件の利用記録はありません。"
                ),
                "measurementStartAt": _iso(measurement_start),
                "dataThrough": _iso(data_through),
                "publishedAt": _iso(publication["updated_at"]),
            },
            "diagnostics": {
                "state": _text(diagnostics.get("state")) or "unavailable",
                "unmatchedEventCount": max(
                    0, int(diagnostics.get("unmatched_event_count") or 0)
                ),
                "errorCode": _text(diagnostics.get("error_code")),
            },
            "selection": self._selection(selection),
            "filterOptions": self._filter_options(
                events, configuration.source_service
            ),
        }
        report.update(
            self._aggregate(
                rows=filtered,
                active_rows=active,
                roster=roster,
                query=selection,
                window=window,
                measurement_start=measurement_start,
                data_through=data_through,
            )
        )
        return report

    @staticmethod
    def _freshness(data_through: datetime, *, now: datetime | None) -> str:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        return (
            "fresh"
            if (current - data_through).total_seconds()
            <= REFRESH_POLICY.freshness_stale_after_minutes * 60
            else "stale"
        )

    def _aggregate(
        self,
        *,
        rows: list[dict[str, Any]],
        active_rows: list[dict[str, Any]],
        roster: list[dict[str, Any]],
        query: NewsUsageQuery,
        window: MetricsTimeWindow,
        measurement_start: datetime,
        data_through: datetime,
    ) -> dict[str, Any]:
        by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in rows:
            by_name[_text(item.get("event_name"))].append(item)
        active_users = {
            _text(item.get("roster_id")) for item in active_rows
        } - {""}
        tab_rows = by_name["tab_view"]
        filter_rows = by_name["filter_change"]
        detail_rows = by_name["detail_view"]
        outbound_rows = by_name["outbound_click"]
        export_starts = by_name["export_started"]
        export_finished = by_name["export_finished"]
        summary_rows = by_name["summary_view"]
        manual_summary = [
            item for item in summary_rows if _text(item.get("trigger")) == "manual"
        ]
        automatic_summary = [
            item for item in summary_rows if _text(item.get("trigger")) == "auto"
        ]

        changed_field_rows: list[dict[str, Any]] = []
        for item in filter_rows:
            for field in item.get("changed_fields") or []:
                changed_field_rows.append({**item, "_changed_field": field})

        detail_articles, detail_article_count = self._article_rows(
            [*detail_rows, *outbound_rows], primary_action="detail"
        )
        outbound_articles, outbound_article_count = self._article_rows(
            [*detail_rows, *outbound_rows], primary_action="outbound"
        )

        started_by_operation: dict[tuple[str, str, str], dict[str, Any]] = {}
        for item in export_starts:
            key = _export_operation_key(item)
            if not all(key):
                raise NewsUsageSnapshotConflictError(
                    "published export start identity is invalid"
                )
            started_by_operation.setdefault(key, item)
        finished_by_operation: dict[tuple[str, str, str], dict[str, Any]] = {}
        for item in export_finished:
            key = _export_operation_key(item)
            if not all(key):
                raise NewsUsageSnapshotConflictError(
                    "published export terminal identity is invalid"
                )
            finished_by_operation.setdefault(key, item)
        export_starts = list(started_by_operation.values())
        export_finished = list(finished_by_operation.values())
        matched_operations = set(started_by_operation) & set(finished_by_operation)
        handed_off = sum(
            _text(finished_by_operation[key].get("result"))
            == "download_handed_off"
            for key in matched_operations
        )
        result_counts = Counter(
            _text(item.get("result")) or "unknown"
            for item in finished_by_operation.values()
        )

        return {
            "kpis": {
                "scopeUsers": len(roster),
                "activeUsers": len(active_users),
                "adoptionRate": _rate(len(active_users), len(roster)),
                "totalActions": len(active_rows),
                "tabViews": len(tab_rows),
                "filterChanges": len(filter_rows),
                "detailViews": len(detail_rows),
                "outboundClicks": len(outbound_rows),
                "exportStarts": len(export_starts),
                "manualSummaryViews": len(manual_summary),
            },
            "trend": self._trend(
                rows,
                window=window,
                measurement_start=measurement_start,
                data_through=data_through,
            ),
            "tabBehavior": {
                "views": len(tab_rows),
                "activeUsers": len(
                    {_text(item.get("roster_id")) for item in tab_rows} - {""}
                ),
                "byChannel": _count_rows(
                    tab_rows,
                    key=lambda item: item.get("channel"),
                    labels={"news": "ニュース", "society": "学会"},
                ),
            },
            "filterBehavior": {
                "changes": len(filter_rows),
                "activeUsers": len(
                    {_text(item.get("roster_id")) for item in filter_rows}
                    - {""}
                ),
                "searchChanges": sum(
                    "query" in (item.get("changed_fields") or [])
                    for item in filter_rows
                ),
                "searchEnabledAfterChange": sum(
                    item.get("filter_has_query") is True for item in filter_rows
                ),
                "byChangedField": _count_rows(
                    changed_field_rows,
                    key=lambda item: item.get("_changed_field"),
                ),
            },
            "detailBehavior": {
                "views": len(detail_rows),
                "activeUsers": len(
                    {_text(item.get("roster_id")) for item in detail_rows} - {""}
                ),
                "totalArticles": detail_article_count,
                "isTruncated": detail_article_count > MAX_POPULAR_ARTICLES,
                "popularArticles": detail_articles,
            },
            "outboundBehavior": {
                "clicks": len(outbound_rows),
                "activeUsers": len(
                    {_text(item.get("roster_id")) for item in outbound_rows}
                    - {""}
                ),
                "totalArticles": outbound_article_count,
                "isTruncated": outbound_article_count > MAX_POPULAR_ARTICLES,
                "byLinkKind": _count_rows(
                    outbound_rows, key=lambda item: item.get("link_kind")
                ),
                "popularArticles": outbound_articles,
            },
            "exportBehavior": {
                "started": len(started_by_operation),
                "activeUsers": len(
                    {_text(item.get("roster_id")) for item in export_starts}
                    - {""}
                ),
                "finished": len(finished_by_operation),
                "pending": len(set(started_by_operation) - set(finished_by_operation)),
                "orphanFinished": len(
                    set(finished_by_operation) - set(started_by_operation)
                ),
                "downloadHandoffRate": _rate(
                    handed_off, len(started_by_operation)
                ),
                "results": [
                    {"result": result, "attempts": count}
                    for result, count in sorted(
                        result_counts.items(), key=lambda pair: (-pair[1], pair[0])
                    )
                ],
            },
            "summaryBehavior": {
                "manualViews": len(manual_summary),
                "manualUsers": len(
                    {
                        _text(item.get("roster_id"))
                        for item in manual_summary
                    }
                    - {""}
                ),
                "automaticViews": len(automatic_summary),
                "automaticUsers": len(
                    {
                        _text(item.get("roster_id"))
                        for item in automatic_summary
                    }
                    - {""}
                ),
            },
            "organizations": {
                "users": self._user_rows(roster, active_rows, query),
                "departments": self._organization_rows(
                    roster,
                    active_rows,
                    key_field="department",
                    label_field="department",
                ),
                "regions": self._organization_rows(
                    roster,
                    active_rows,
                    key_field="area_key",
                    label_field="area",
                ),
            },
        }

    @staticmethod
    def csv_bytes(report: dict[str, Any]) -> bytes:
        columns = (
            "record_type",
            "key",
            "label",
            "channel",
            "content_event_version",
            "business_unit",
            "geography",
            "source_id",
            "category",
            "department",
            "region",
            "workplace",
            "role",
            "scope_users",
            "active_users",
            "actions",
            "detail_views",
            "outbound_clicks",
            "active_days",
            "last_active_at",
        )
        rows: list[dict[str, Any]] = []
        for item in report.get("organizations", {}).get("users", []):
            rows.append(
                {
                    "record_type": "user",
                    "key": item.get("rosterId"),
                    "label": item.get("name"),
                    "channel": report.get("selection", {}).get("channel"),
                    "department": item.get("department"),
                    "region": item.get("area"),
                    "workplace": item.get("workplace"),
                    "role": item.get("role"),
                    "active_users": 1 if item.get("actions") else 0,
                    "actions": item.get("actions"),
                    "active_days": item.get("activeDays"),
                    "last_active_at": item.get("lastActiveAt"),
                }
            )
        for record_type, collection in (
            ("department", report.get("organizations", {}).get("departments", [])),
            ("region", report.get("organizations", {}).get("regions", [])),
        ):
            for item in collection:
                rows.append(
                    {
                        "record_type": record_type,
                        "key": item.get("key"),
                        "label": item.get("label"),
                        "channel": report.get("selection", {}).get("channel"),
                        "scope_users": item.get("scopeUsers"),
                        "active_users": item.get("activeUsers"),
                        "actions": item.get("actions"),
                    }
                )
        seen_articles: set[tuple[str, ...]] = set()
        article_collections = (
            report.get("detailBehavior", {}).get("popularArticles", []),
            report.get("outboundBehavior", {}).get("popularArticles", []),
        )
        for item in (article for rows in article_collections for article in rows):
            key = (
                _text(item.get("contentEventId")),
                _text(item.get("contentEventVersion")),
                _text(item.get("channel")),
                _text(item.get("businessUnit")),
                _text(item.get("geography")),
                _text(item.get("sourceId")),
                _text(item.get("category")),
            )
            if key in seen_articles:
                continue
            seen_articles.add(key)
            rows.append(
                {
                    "record_type": "article",
                    "key": "/".join(value for value in key[:2] if value),
                    "label": item.get("sourceId"),
                    "channel": item.get("channel"),
                    "content_event_version": item.get("contentEventVersion"),
                    "business_unit": item.get("businessUnit"),
                    "geography": item.get("geography"),
                    "source_id": item.get("sourceId"),
                    "category": item.get("category"),
                    "active_users": item.get("activeUsers"),
                    "detail_views": item.get("detailViews"),
                    "outbound_clicks": item.get("outboundClicks"),
                }
            )
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    column: safe_csv_cell(row.get(column, ""))
                    for column in columns
                }
            )
        return ("\ufeff" + stream.getvalue()).encode("utf-8")
