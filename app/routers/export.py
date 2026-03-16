from __future__ import annotations

import csv
from io import StringIO
from typing import Any, Dict, Iterable, List

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from app.dependencies import get_bigquery_metrics_service, get_firestore_history_service
from app.security.auth import AdminIdentity, require_admin
from app.services.bigquery_metrics import BigQueryMetricsService
from app.services.firestore_history import FirestoreHistoryService
from app.settings import Settings, get_settings
from app.time_window import MetricsTimeWindow, TimeWindowValidationError, resolve_time_window

router = APIRouter(prefix="/api/export", tags=["export"])


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


def _rows_to_csv(rows: Iterable[Dict[str, Any]]) -> str:
    rows_list = list(rows)
    if not rows_list:
        return ""

    headers: List[str] = []
    seen = set()
    for row in rows_list:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                headers.append(key)

    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    for row in rows_list:
        writer.writerow(row)
    return buf.getvalue()


def _csv_response(filename: str, rows: Iterable[Dict[str, Any]]) -> Response:
    body = "\ufeff" + _rows_to_csv(rows)
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/usage.csv")
def export_usage_csv(
    days: int = Query(default=30, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
) -> Response:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    try:
        rows = bq.get_usage_timeseries(window=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"usage export failed: {exc}") from exc
    return _csv_response(f"usage_{window.requested_days}d.csv", rows)


@router.get("/errors/trend.csv")
def export_errors_trend_csv(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
) -> Response:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    try:
        report = bq.get_error_report(window=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"errors trend export failed: {exc}") from exc
    return _csv_response(f"errors_trend_{window.requested_days}d.csv", report.get("trend", []))


@router.get("/errors/endpoints.csv")
def export_errors_endpoints_csv(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
) -> Response:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    try:
        report = bq.get_error_report(window=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"errors endpoint export failed: {exc}") from exc
    return _csv_response(f"errors_endpoints_{window.requested_days}d.csv", report.get("topEndpoints", []))


@router.get("/errors/types.csv")
def export_errors_types_csv(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
) -> Response:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    try:
        report = bq.get_error_report(window=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"errors type export failed: {exc}") from exc
    return _csv_response(f"errors_types_{window.requested_days}d.csv", report.get("topErrors", []))


@router.get("/devices.csv")
def export_devices_csv(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
) -> Response:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    try:
        rows = bq.get_device_report(window=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"devices export failed: {exc}") from exc
    return _csv_response(f"devices_{window.requested_days}d.csv", rows)


@router.get("/query-suggest/stages.csv")
def export_qs_stages_csv(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
) -> Response:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    try:
        report = bq.get_query_suggest_report(window=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"query-suggest stage export failed: {exc}") from exc
    return _csv_response(f"query_suggest_stages_{window.requested_days}d.csv", report.get("stages", []))


@router.get("/query-suggest/fallbacks.csv")
def export_qs_fallbacks_csv(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    bq: BigQueryMetricsService = Depends(get_bigquery_metrics_service),
) -> Response:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    try:
        report = bq.get_query_suggest_report(window=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"query-suggest fallback export failed: {exc}") from exc
    return _csv_response(f"query_suggest_fallbacks_{window.requested_days}d.csv", report.get("fallbackSources", []))


@router.get("/query-suggest/facts.csv")
def export_qs_facts_csv(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    fs: FirestoreHistoryService = Depends(get_firestore_history_service),
) -> Response:
    window = _build_window(settings=settings, days=days, preset=preset, start=start, end=end)
    try:
        facts = fs.aggregate_query_suggest_facts(window=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"query-suggest facts export failed: {exc}") from exc
    return _csv_response(f"query_suggest_facts_{window.requested_days}d.csv", [facts])


@router.get("/users.csv")
def export_users_csv(
    limit: int = Query(default=500, ge=1, le=500),
    q: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    fs: FirestoreHistoryService = Depends(get_firestore_history_service),
) -> Response:
    try:
        rows = fs.list_users(limit=limit, q=q)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"users export failed: {exc}") from exc
    return _csv_response("users.csv", rows)


@router.get("/conversations.csv")
def export_conversations_csv(
    user_id: str = Query(...),
    include_hidden: bool = Query(default=False),
    limit: int = Query(default=500, ge=1, le=500),
    q: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    fs: FirestoreHistoryService = Depends(get_firestore_history_service),
) -> Response:
    try:
        rows = fs.list_user_conversations(user_id=user_id, include_hidden=include_hidden, limit=limit, q=q)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"conversations export failed: {exc}") from exc
    return _csv_response(f"conversations_{user_id}.csv", rows)


@router.get("/messages.csv")
def export_messages_csv(
    user_id: str = Query(...),
    conversation_id: str = Query(...),
    limit: int = Query(default=2000, ge=1, le=2000),
    _admin: AdminIdentity = Depends(require_admin),
    fs: FirestoreHistoryService = Depends(get_firestore_history_service),
) -> Response:
    try:
        payload = fs.get_conversation_messages(user_id=user_id, conversation_id=conversation_id, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"messages export failed: {exc}") from exc
    if payload is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return _csv_response(f"messages_{conversation_id}.csv", payload.get("messages", []))
