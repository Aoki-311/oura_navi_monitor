from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_bigquery_metrics_service, get_firestore_history_service
from app.security.auth import AdminIdentity, require_admin
from app.services.bigquery_metrics import BigQueryMetricsService
from app.services.firestore_history import FirestoreHistoryService
from app.settings import Settings, get_settings
from app.time_window import MetricsTimeWindow, TimeWindowValidationError, resolve_time_window

router = APIRouter(prefix="/api/trace", tags=["trace"])


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


def _window_payload(window: MetricsTimeWindow) -> dict:
    return {
        "source": window.source,
        "preset": window.preset,
        "start": window.start_utc.isoformat(),
        "end": window.end_utc.isoformat(),
        "timezone": window.timezone,
        "bucketMinutes": window.bucket_minutes,
    }


@router.get("/messages")
def trace_messages(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default="today"),
    start: str = Query(default=""),
    end: str = Query(default=""),
    conversation_id: str = Query(default=""),
    trace_id: str = Query(default=""),
    turn_id: str = Query(default=""),
    user_id: str = Query(default=""),
    user_email: str = Query(default=""),
    status: str = Query(default=""),
    mode: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=1000),
    cursor: str = Query(default=""),
    include_content: bool = Query(default=False),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
    fs: FirestoreHistoryService = Depends(get_firestore_history_service),
) -> dict:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    try:
        candidate_user_id = "" if conversation_id else user_id
        payload_events = bq.search_trace_payloads(
            window=window,
            conversation_id=conversation_id,
            trace_id=trace_id,
            turn_id=turn_id,
            user_id=candidate_user_id,
            limit=limit,
        ) if any([conversation_id, trace_id, turn_id, user_id]) else []
        result = fs.search_messages(
            window=window,
            conversation_id=conversation_id,
            trace_id=trace_id,
            turn_id=turn_id,
            user_id=user_id,
            user_email=user_email,
            status=status,
            mode=mode,
            limit=limit,
            cursor=cursor,
            include_content=include_content,
            candidates=payload_events,
        )
        result["payloadEvents"] = payload_events
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"trace message query failed: {exc}") from exc
    return {
        "window": _window_payload(window),
        **result,
    }
