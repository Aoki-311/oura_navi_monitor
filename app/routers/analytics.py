from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.contracts.analytics import OverviewResponse, RegionsResponse, UserDetailResponse, UsersResponse
from app.dependencies import get_analytics_service
from app.security.auth import AdminIdentity, require_admin
from app.services.analytics_service import (
    AnalyticsService,
    AnalyticsSnapshotConflictError,
)
from app.settings import Settings, get_settings
from app.time_window import MetricsTimeWindow, TimeWindowValidationError, resolve_time_window

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _snapshot_conflict(exc: AnalyticsSnapshotConflictError) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": exc.code,
            "message": "公開済み分析スナップショットを一貫して確認できません。",
        },
    )


def analytics_window(
    *, settings: Settings, days: int, preset: str, start: str, end: str, as_of: str = ""
) -> MetricsTimeWindow:
    try:
        return resolve_time_window(
            settings=settings,
            days=days,
            preset=preset,
            start=start,
            end=end,
            as_of=as_of,
        )
    except TimeWindowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/overview", response_model=OverviewResponse)
def overview(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    as_of: str = Query(default="", max_length=80),
    area_key: str = Query(default="", max_length=80),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    service: AnalyticsService = Depends(get_analytics_service),
) -> dict:
    window = analytics_window(settings=settings, days=days, preset=preset, start=start, end=end, as_of=as_of)
    try:
        return service.overview(window=window, area_key=area_key)
    except AnalyticsSnapshotConflictError as exc:
        raise _snapshot_conflict(exc) from exc


@router.get("/regions", response_model=RegionsResponse)
def regions(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    as_of: str = Query(default="", max_length=80),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    service: AnalyticsService = Depends(get_analytics_service),
) -> dict:
    window = analytics_window(settings=settings, days=days, preset=preset, start=start, end=end, as_of=as_of)
    try:
        return service.regions(window=window)
    except AnalyticsSnapshotConflictError as exc:
        raise _snapshot_conflict(exc) from exc


@router.get("/users", response_model=UsersResponse)
def users(
    days: int = Query(default=30, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    as_of: str = Query(default="", max_length=80),
    q: str = Query(default="", max_length=120),
    area_key: str = Query(default="", max_length=80),
    activity: str = Query(default="", pattern="^(|high|middle|low|dormant)$"),
    sort: str = Query(default="last_desc", pattern="^(last_desc|name_asc|messages_desc|success_desc)$"),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    service: AnalyticsService = Depends(get_analytics_service),
) -> dict:
    window = analytics_window(settings=settings, days=days, preset=preset, start=start, end=end, as_of=as_of)
    try:
        return service.users(q=q, area_key=area_key, activity=activity, sort=sort, window=window)
    except AnalyticsSnapshotConflictError as exc:
        raise _snapshot_conflict(exc) from exc


@router.get("/overview/users", response_model=UsersResponse)
def overview_users(
    days: int = Query(default=30, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    as_of: str = Query(default="", max_length=80),
    q: str = Query(default="", max_length=120),
    area_key: str = Query(default="", max_length=80),
    activity: str = Query(default="", pattern="^(|high|middle|low|dormant)$"),
    sort: str = Query(default="last_desc", pattern="^(last_desc|name_asc|messages_desc|success_desc)$"),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    service: AnalyticsService = Depends(get_analytics_service),
) -> dict:
    window = analytics_window(settings=settings, days=days, preset=preset, start=start, end=end, as_of=as_of)
    try:
        return service.overview_users(
            q=q,
            area_key=area_key,
            activity=activity,
            sort=sort,
            window=window,
        )
    except AnalyticsSnapshotConflictError as exc:
        raise _snapshot_conflict(exc) from exc


@router.get("/users/{roster_id}", response_model=UserDetailResponse)
def user_detail(
    roster_id: str,
    days: int = Query(default=30, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    as_of: str = Query(default="", max_length=80),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    service: AnalyticsService = Depends(get_analytics_service),
) -> dict:
    window = analytics_window(settings=settings, days=days, preset=preset, start=start, end=end, as_of=as_of)
    try:
        return service.user_detail(roster_id, window=window)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="user not found") from exc
    except AnalyticsSnapshotConflictError as exc:
        raise _snapshot_conflict(exc) from exc
