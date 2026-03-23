from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_bigquery_metrics_service, get_firestore_history_service
from app.security.auth import AdminIdentity, require_admin
from app.services.bigquery_metrics import BigQueryMetricsService
from app.services.firestore_history import FirestoreHistoryService
from app.settings import Settings, get_settings
from app.time_window import MetricsTimeWindow, TimeWindowValidationError, resolve_time_window

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


def _build_window(
    *,
    settings: Settings,
    days: int,
    preset: str,
    start: str,
    end: str,
) -> MetricsTimeWindow:
    try:
        return resolve_time_window(
            settings=settings,
            days=days,
            preset=preset,
            start=start,
            end=end,
        )
    except TimeWindowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _safe_int(value: object) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _safe_float(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except Exception:
        return None
    return parsed


def _user_key(user_id: str, user_email: str) -> str:
    return f"{str(user_id or '').strip()}::{str(user_email or '').strip().lower()}"


@router.get("/overview")
def metrics_overview(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
    fs: FirestoreHistoryService = Depends(get_firestore_history_service),
) -> dict:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    try:
        bq_overview = bq.get_overview(window=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"bigquery overview failed: {exc}") from exc

    try:
        fs_usage = fs.aggregate_usage(window=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"firestore usage failed: {exc}") from exc

    return {
        "days": window.requested_days,
        "window": {
            "source": window.source,
            "preset": window.preset,
            "start": window.start_utc.isoformat(),
            "end": window.end_utc.isoformat(),
            "timezone": window.timezone,
            "bucketMinutes": window.bucket_minutes,
        },
        "overview": bq_overview,
        "usage": fs_usage,
    }


@router.get("/usage")
def metrics_usage(
    days: int = Query(default=30, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
    fs: FirestoreHistoryService = Depends(get_firestore_history_service),
) -> dict:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    try:
        timeseries = bq.get_usage_timeseries(window=window)
        firestore_usage = fs.aggregate_usage(window=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"usage query failed: {exc}") from exc

    return {
        "days": window.requested_days,
        "window": {
            "source": window.source,
            "preset": window.preset,
            "start": window.start_utc.isoformat(),
            "end": window.end_utc.isoformat(),
            "timezone": window.timezone,
            "bucketMinutes": window.bucket_minutes,
        },
        "timeseries": timeseries,
        "usage": firestore_usage,
    }


@router.get("/errors")
def metrics_errors(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
) -> dict:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    try:
        report = bq.get_error_report(window=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"error report query failed: {exc}") from exc

    return {
        "days": window.requested_days,
        "window": {
            "source": window.source,
            "preset": window.preset,
            "start": window.start_utc.isoformat(),
            "end": window.end_utc.isoformat(),
            "timezone": window.timezone,
            "bucketMinutes": window.bucket_minutes,
        },
        **report,
    }


@router.get("/devices")
def metrics_devices(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
) -> dict:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    try:
        rows = bq.get_device_report(window=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"device report query failed: {exc}") from exc

    return {
        "days": window.requested_days,
        "window": {
            "source": window.source,
            "preset": window.preset,
            "start": window.start_utc.isoformat(),
            "end": window.end_utc.isoformat(),
            "timezone": window.timezone,
            "bucketMinutes": window.bucket_minutes,
        },
        "devices": rows,
    }


@router.get("/query-suggest")
def metrics_query_suggest(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
    fs: FirestoreHistoryService = Depends(get_firestore_history_service),
) -> dict:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    try:
        log_report = bq.get_query_suggest_report(window=window)
        fact_report = fs.aggregate_query_suggest_facts(window=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"query-suggest report failed: {exc}") from exc

    return {
        "days": window.requested_days,
        "window": {
            "source": window.source,
            "preset": window.preset,
            "start": window.start_utc.isoformat(),
            "end": window.end_utc.isoformat(),
            "timezone": window.timezone,
            "bucketMinutes": window.bucket_minutes,
        },
        "logs": log_report,
        "facts": fact_report,
    }


@router.get("/dashboard")
def metrics_dashboard(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    user: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
    fs: FirestoreHistoryService = Depends(get_firestore_history_service),
) -> dict:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    try:
        bq_overview = bq.get_overview(window=window)
        usage_timeseries = bq.get_usage_timeseries(window=window)
        error_report = bq.get_error_report(window=window)
        device_report = bq.get_device_report(window=window)
        fs_metrics = fs.aggregate_monitor_metrics(window=window)
        followup_report = bq.get_followup_open_aggregates(window=window)
        request_user_rows = bq.get_request_user_aggregates(window=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"dashboard query failed: {exc}") from exc

    request_user_map: dict[str, dict] = {}
    for row in request_user_rows:
        user_id = str(row.get("user_id") or "").strip() or "unknown"
        user_email = str(row.get("user_email") or "").strip().lower()
        key = _user_key(user_id, user_email)
        request_user_map[key] = {
            "totalRequestCount": _safe_int(row.get("request_count")),
            "coreRequestCount": _safe_int(row.get("core_request_count")),
            "systemRequestCount": _safe_int(row.get("system_request_count")),
            "desktopRequestCount": _safe_int(row.get("desktop_request_count")),
            "mobileRequestCount": _safe_int(row.get("mobile_request_count")),
            "unknownRequestCount": _safe_int(row.get("unknown_request_count")),
        }

    followup_user_map: dict[str, dict] = {}
    for row in followup_report.get("users", []) or []:
        user_id = str(row.get("userId") or "").strip() or "unknown"
        user_email = str(row.get("userEmail") or "").strip().lower()
        key = _user_key(user_id, user_email)
        followup_user_map[key] = {
            "followupRecognizedCount": _safe_int(row.get("recognizedCount")),
            "followupSuccessCount": _safe_int(row.get("successCount")),
            "followupOpenSuccessRate": _safe_float(row.get("successRate")),
        }

    users: list[dict] = []
    for user_row in fs_metrics.get("users", []) or []:
        user_id = str(user_row.get("userId") or "").strip()
        user_email = str(user_row.get("userEmail") or "").strip().lower()
        key = _user_key(user_id, user_email)
        merged = dict(user_row)
        request_metrics = request_user_map.get(key, {})
        followup_metrics = followup_user_map.get(key, {})
        merged.update(request_metrics)
        merged.update(followup_metrics)

        total_request_count = _safe_int(merged.get("totalRequestCount"))
        desktop_request_count = _safe_int(merged.get("desktopRequestCount"))
        mobile_request_count = _safe_int(merged.get("mobileRequestCount"))
        merged["desktopRequestRate"] = (
            desktop_request_count / total_request_count if total_request_count > 0 else None
        )
        merged["mobileRequestRate"] = mobile_request_count / total_request_count if total_request_count > 0 else None
        users.append(merged)

    existing_keys = {_user_key(str(u.get("userId") or ""), str(u.get("userEmail") or "")) for u in users}
    for key, request_metrics in request_user_map.items():
        if key in existing_keys:
            continue
        user_id, user_email = key.split("::", 1)
        fallback_user = {
            "userId": user_id,
            "userEmail": user_email,
            "subject": "",
            "updatedAt": "",
            "updatedAtJst": "",
            "conversationCount": 0,
            "activeConversationCount": 0,
            "messageCount": 0,
            "activeSessionStickiness": None,
            "feedbackGoodCount": 0,
            "feedbackTotalCount": 0,
            "feedbackLikeRate": None,
            "messageFailureCount": 0,
            "messageFailureRate": None,
            "assistantMessageCount": 0,
            "citationCoveredCount": 0,
            "citationCoverageRate": None,
            "favoriteConversationCount": 0,
            "favoriteConversationRate": None,
            "integrityRiskConversationCount": 0,
            "integrityRiskRate": None,
            "modeCounts": {"internal": 0, "websearch": 0, "deepthinking": 0, "standard": 0, "unknown": 0},
            "modeDistribution": [],
            "newQuestionCount": 0,
            "followupCount": 0,
            "followupRate": None,
            "topMessageErrors": [],
            **request_metrics,
        }
        followup_metrics = followup_user_map.get(key, {})
        fallback_user.update(followup_metrics)
        total_request_count = _safe_int(fallback_user.get("totalRequestCount"))
        desktop_request_count = _safe_int(fallback_user.get("desktopRequestCount"))
        mobile_request_count = _safe_int(fallback_user.get("mobileRequestCount"))
        fallback_user["desktopRequestRate"] = (
            desktop_request_count / total_request_count if total_request_count > 0 else None
        )
        fallback_user["mobileRequestRate"] = (
            mobile_request_count / total_request_count if total_request_count > 0 else None
        )
        users.append(fallback_user)

    users.sort(
        key=lambda item: (
            _safe_int(item.get("messageCount")),
            _safe_int(item.get("totalRequestCount")),
            _safe_int(item.get("conversationCount")),
        ),
        reverse=True,
    )

    lookup = str(user or "").strip().lower()
    selected_user = None
    for candidate in users:
        uid = str(candidate.get("userId") or "").lower()
        email = str(candidate.get("userEmail") or "").lower()
        if not lookup:
            continue
        if lookup == uid or lookup == email or lookup in uid or lookup in email:
            selected_user = candidate
            break

    selected_user_timeseries: list[dict] = []
    if selected_user is not None:
        selected_key = str(selected_user.get("userId") or "").strip() or str(selected_user.get("userEmail") or "").strip()
        try:
            selected_user_timeseries = bq.get_request_user_timeseries(window=window, user_key=selected_key)
        except Exception:
            selected_user_timeseries = []

    summary = {
        "dau": _safe_int(fs_metrics.get("dau")),
        "wau": _safe_int(fs_metrics.get("wau")),
        "activeUsersInWindow": _safe_int(fs_metrics.get("activeUsersInWindow")),
        "activeSessionStickiness": _safe_float(fs_metrics.get("activeSessionStickiness")),
        "conversationCount": _safe_int(fs_metrics.get("conversationCount")),
        "messageCount": _safe_int(fs_metrics.get("messageCount")),
        "firstAnswerAvgMs": _safe_float(bq_overview.get("first_answer_avg_ms")),
        "enhanceAnswerAvgMs": _safe_float(bq_overview.get("enhance_answer_avg_ms")),
        "followupOpenSuccessRate": _safe_float(followup_report.get("successRate")),
        "followupRecognizedCount": _safe_int(followup_report.get("recognizedCount")),
        "followupSuccessCount": _safe_int(followup_report.get("successCount")),
        "feedbackLikeRate": _safe_float(fs_metrics.get("feedbackLikeRate")),
        "querySuggestStableRate": _safe_float(bq_overview.get("qs_stable_rate")),
        "querySuggestAvgLatencyMs": _safe_float(bq_overview.get("qs_avg_latency_ms")),
        "messageFailureRate": _safe_float(fs_metrics.get("messageFailureRate")),
        "citationCoverageRate": _safe_float(fs_metrics.get("citationCoverageRate")),
        "restoreSuccessRate": _safe_float(bq_overview.get("restore_success_rate")),
        "requestCount": _safe_int(bq_overview.get("request_count")),
        "coreRequestCount": _safe_int(bq_overview.get("core_request_count")),
        "error5xxRate": _safe_float(bq_overview.get("error_5xx_rate")),
        "requestP95LatencyMs": _safe_float(bq_overview.get("request_p95_latency_ms")),
    }

    return {
        "days": window.requested_days,
        "window": {
            "source": window.source,
            "preset": window.preset,
            "start": window.start_utc.isoformat(),
            "end": window.end_utc.isoformat(),
            "timezone": window.timezone,
            "bucketMinutes": window.bucket_minutes,
        },
        "summary": summary,
        "charts": {
            "requestTrend": usage_timeseries,
            "coreRequestTrend": usage_timeseries,
            "deviceQuality": device_report,
            "modeDistribution": fs_metrics.get("modeDistribution", []),
            "questionFlow": {
                "newQuestionCount": _safe_int(fs_metrics.get("newQuestionCount")),
                "followupCount": _safe_int(fs_metrics.get("followupCount")),
                "followupRate": _safe_float(fs_metrics.get("followupRate")),
            },
            "favoriteConversation": {
                "count": _safe_int(fs_metrics.get("favoriteConversationCount")),
                "total": _safe_int(fs_metrics.get("conversationCount")),
                "rate": _safe_float(fs_metrics.get("favoriteConversationRate")),
            },
            "integrityRisk": {
                "count": _safe_int(fs_metrics.get("integrityRiskConversationCount")),
                "total": _safe_int(fs_metrics.get("conversationCount")),
                "rate": _safe_float(fs_metrics.get("integrityRiskRate")),
            },
            "citationCoverage": {
                "covered": _safe_int(fs_metrics.get("citationCoveredCount")),
                "assistantTotal": _safe_int(fs_metrics.get("assistantMessageCount")),
                "rate": _safe_float(fs_metrics.get("citationCoverageRate")),
            },
            "topErrorEndpoints": error_report.get("topEndpoints", []),
            "topMessageErrors": fs_metrics.get("topMessageErrors", []),
        },
        "users": users,
        "selectedUser": selected_user,
        "selectedUserTimeseries": selected_user_timeseries,
    }
