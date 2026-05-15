from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import datetime
import threading
import time
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_bigquery_metrics_service, get_firestore_history_service
from app.security.auth import AdminIdentity, require_admin
from app.services.bigquery_metrics import BigQueryMetricsService
from app.services.firestore_history import FirestoreHistoryService
from app.settings import Settings, get_settings
from app.time_window import MetricsTimeWindow, TimeWindowValidationError, resolve_time_window

router = APIRouter(prefix="/api/metrics", tags=["metrics"])

_DASHBOARD_CACHE_LOCK = threading.Lock()
_DASHBOARD_CACHE: dict[str, tuple[float, dict]] = {}
_DASHBOARD_CACHE_MAX_ITEMS = 64


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


def _window_payload(window: MetricsTimeWindow) -> dict:
    return {
        "source": window.source,
        "preset": window.preset,
        "start": window.start_utc.isoformat(),
        "end": window.end_utc.isoformat(),
        "timezone": window.timezone,
        "bucketMinutes": window.bucket_minutes,
    }


def _meta_payload(window: MetricsTimeWindow) -> dict:
    try:
        tz = ZoneInfo(window.timezone)
        generated_at = datetime.now(tz).isoformat()
    except Exception:
        generated_at = datetime.utcnow().isoformat()
    return {
        "generatedAt": generated_at,
        "cacheHit": False,
        "dataDelaySec": None,
    }


def _mode_label(value: object) -> str:
    mode = str(value or "").strip().lower()
    if mode == "internal":
        return "社内モード"
    if mode == "websearch":
        return "Web検索モード"
    return "その他"


def _device_label(value: object) -> str:
    device = str(value or "").strip().lower()
    if device == "desktop":
        return "PC"
    if device == "mobile":
        return "モバイル"
    return "不明"


def _dashboard_cache_get(*, key: str, now_mono: float) -> dict | None:
    with _DASHBOARD_CACHE_LOCK:
        row = _DASHBOARD_CACHE.get(key)
        if not row:
            return None
        expire_at, payload = row
        if expire_at <= now_mono:
            _DASHBOARD_CACHE.pop(key, None)
            return None
        return copy.deepcopy(payload)


def _dashboard_cache_set(*, key: str, payload: dict, ttl_sec: int, now_mono: float) -> None:
    if ttl_sec <= 0:
        return
    expire_at = now_mono + float(ttl_sec)
    with _DASHBOARD_CACHE_LOCK:
        _DASHBOARD_CACHE[key] = (expire_at, copy.deepcopy(payload))
        if len(_DASHBOARD_CACHE) > _DASHBOARD_CACHE_MAX_ITEMS:
            oldest_key = min(_DASHBOARD_CACHE.items(), key=lambda item: item[1][0])[0]
            _DASHBOARD_CACHE.pop(oldest_key, None)


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


@router.get("/system-dashboard")
def metrics_system_dashboard(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default="today"),
    start: str = Query(default=""),
    end: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
) -> dict:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    now_mono = time.monotonic()
    cache_ttl_sec = max(0, int(settings.monitor_dashboard_cache_ttl_sec or 0))
    cache_key = "|".join(
        [
            "system-dashboard",
            str(window.timezone),
            str(window.source),
            str(window.preset),
            str(days),
            str(start or ""),
            str(end or ""),
            window.start_utc.isoformat(),
        ]
    )
    if cache_ttl_sec > 0:
        cached = _dashboard_cache_get(key=cache_key, now_mono=now_mono)
        if cached is not None:
            cached["meta"] = {
                **(cached.get("meta") or {}),
                "cacheHit": True,
                "fetchMs": 0,
            }
            return cached

    fetch_started = time.monotonic()
    try:
        dashboard = bq.get_system_dashboard_metrics(window=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"system dashboard query failed: {exc}") from exc

    metric_status = {}
    if isinstance(dashboard, dict):
        raw_metric_status = dashboard.pop("metricStatus", {})
        if isinstance(raw_metric_status, dict):
            metric_status = raw_metric_status
    payload = {
        "window": _window_payload(window),
        "meta": {
            **_meta_payload(window),
            "fetchMs": int((time.monotonic() - fetch_started) * 1000),
            **({"metricStatus": metric_status} if metric_status else {}),
        },
        **dashboard,
    }
    _dashboard_cache_set(key=cache_key, payload=payload, ttl_sec=cache_ttl_sec, now_mono=now_mono)
    return payload


@router.get("/answer-quality")
def metrics_answer_quality(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default="today"),
    start: str = Query(default=""),
    end: str = Query(default=""),
    user: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
) -> dict:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    try:
        report = bq.get_answer_quality_metrics(window=window, user_key=user)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"answer quality query failed: {exc}") from exc
    summary = dict(report.get("summary") or {})
    report["summary"] = summary
    return {
        "window": _window_payload(window),
        "meta": _meta_payload(window),
        **report,
    }


@router.get("/followup")
def metrics_followup(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default="today"),
    start: str = Query(default=""),
    end: str = Query(default=""),
    user: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
) -> dict:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    try:
        report = bq.get_followup_metrics(window=window, user_key=user)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"followup query failed: {exc}") from exc
    return {
        "window": _window_payload(window),
        "meta": _meta_payload(window),
        **report,
    }


@router.get("/users")
def metrics_users(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default="today"),
    start: str = Query(default=""),
    end: str = Query(default=""),
    activity: str = Query(default=""),
    q: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=1000),
    cursor: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
) -> dict:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    try:
        rows = bq.get_request_user_monitoring_rows(window=window, activity=activity, q=q, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"users query failed: {exc}") from exc
    return {
        "window": _window_payload(window),
        "meta": _meta_payload(window),
        "users": rows,
        "page": {
            "nextCursor": "",
            "cursor": cursor,
        },
    }


@router.get("/users/{user_id}")
def metrics_user_detail(
    user_id: str,
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default="today"),
    start: str = Query(default=""),
    end: str = Query(default=""),
    conversation_limit: int = Query(default=50, ge=1, le=200),
    conversation_cursor: str = Query(default=""),
    include_hidden: bool = Query(default=False),
    include_messages: bool = Query(default=False),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
    fs: FirestoreHistoryService = Depends(get_firestore_history_service),
) -> dict:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    now_mono = time.monotonic()
    cache_ttl_sec = max(0, int(settings.monitor_dashboard_cache_ttl_sec or 0))
    cache_key = "|".join(
        [
            "user-detail",
            str(user_id or "").strip().lower(),
            str(window.timezone),
            str(window.source),
            str(window.preset),
            str(days),
            str(start or ""),
            str(end or ""),
            window.start_utc.isoformat(),
            str(conversation_limit),
            str(conversation_cursor or ""),
            str(bool(include_hidden)),
        ]
    )
    if cache_ttl_sec > 0:
        cached = _dashboard_cache_get(key=cache_key, now_mono=now_mono)
        if cached is not None:
            cached["meta"] = {
                **(cached.get("meta") or {}),
                "cacheHit": True,
                "fetchMs": 0,
            }
            return cached

    fetch_started = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=3) as executor:
            profile_future = executor.submit(fs.get_user_profile, user_id=user_id)
            summary_future = executor.submit(bq.get_user_detail_summary, window=window, user_key=user_id)
            conversation_future = executor.submit(
                fs.list_user_conversation_summaries,
                user_id=user_id,
                include_hidden=include_hidden,
                limit=conversation_limit,
                cursor=conversation_cursor,
            )
            profile = profile_future.result()
            summary_payload = summary_future.result()
            conversation_page = conversation_future.result()
        if profile is None:
            raise HTTPException(status_code=404, detail="user not found")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"user detail query failed: {exc}") from exc

    summary = summary_payload.get("summary") or {}
    user = {
        **profile,
        "activityLevel": summary.get("activityLevel") or "",
        "activityKey": summary.get("activityKey") or "",
        "lastActiveAtJst": summary.get("lastActiveAtJst") or "",
    }
    meta = _meta_payload(window)
    meta["metricStatus"] = {
        "answerSuccessRate": "proxy",
        "badFeedbackRate": "pending",
    }
    meta["fetchMs"] = int((time.monotonic() - fetch_started) * 1000)
    payload = {
        "window": _window_payload(window),
        "meta": meta,
        "user": user,
        "summary": summary,
        "trend": summary_payload.get("trend") or [],
        "modeDistribution": summary_payload.get("modeDistribution") or [],
        "answerQualityDistribution": summary_payload.get("answerQualityDistribution") or {},
        "followup": summary_payload.get("followup") or {},
        "conversations": conversation_page.get("items") or [],
        "page": {
            "nextCursor": conversation_page.get("nextCursor") or "",
            "cursor": conversation_cursor,
        },
        "messageLoading": {
            "endpoint": "/api/trace/messages",
            "includeMessagesInThisResponse": False,
            "includeMessagesRequested": bool(include_messages),
        },
    }
    _dashboard_cache_set(key=cache_key, payload=payload, ttl_sec=cache_ttl_sec, now_mono=now_mono)
    return payload


@router.get("/schema-health")
def metrics_schema_health(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default="today"),
    start: str = Query(default=""),
    end: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
) -> dict:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    try:
        report = bq.get_schema_health_metrics(window=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"schema health query failed: {exc}") from exc
    return {
        "window": _window_payload(window),
        "meta": _meta_payload(window),
        **report,
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
) -> dict:
    payload = metrics_system_dashboard(
        days=days,
        preset=preset,
        start=start,
        end=end,
        _admin=_admin,
        settings=settings,
        bq=bq,
    )
    payload["meta"] = {
        **(payload.get("meta") or {}),
        "deprecated": True,
        "legacyEndpoint": "/api/metrics/dashboard",
        "replacementEndpoint": "/api/metrics/system-dashboard",
        **({"ignoredParameters": ["user"]} if str(user or "").strip() else {}),
    }
    return payload
