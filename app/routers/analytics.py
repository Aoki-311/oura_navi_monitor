from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.contracts.analytics import OverviewResponse, RegionsResponse, UserDetailResponse, UsersResponse
from app.dependencies import get_analytics_service
from app.security.auth import AdminIdentity, require_admin
from app.services.analytics_service import AnalyticsService
from app.settings import Settings, get_settings
from app.time_window import MetricsTimeWindow, TimeWindowValidationError, resolve_time_window

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def analytics_window(
    *, settings: Settings, days: int, preset: str, start: str, end: str
) -> MetricsTimeWindow:
    try:
        return resolve_time_window(settings=settings, days=days, preset=preset, start=start, end=end)
    except TimeWindowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/overview", response_model=OverviewResponse)
def overview(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    area_key: str = Query(default="", max_length=80),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    service: AnalyticsService = Depends(get_analytics_service),
) -> dict:
    window = analytics_window(settings=settings, days=days, preset=preset, start=start, end=end)
    return service.overview(window=window, area_key=area_key)


@router.get("/regions", response_model=RegionsResponse)
def regions(
    days: int = Query(default=7, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    service: AnalyticsService = Depends(get_analytics_service),
) -> dict:
    window = analytics_window(settings=settings, days=days, preset=preset, start=start, end=end)
    return service.regions(window=window)


@router.get("/users", response_model=UsersResponse)
def users(
    days: int = Query(default=30, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    q: str = Query(default="", max_length=120),
    area_key: str = Query(default="", max_length=80),
    activity: str = Query(default="", pattern="^(|high|middle|low|dormant)$"),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    service: AnalyticsService = Depends(get_analytics_service),
) -> dict:
    window = analytics_window(settings=settings, days=days, preset=preset, start=start, end=end)
    return service.users(q=q, area_key=area_key, activity=activity, window=window)


@router.get("/users/{roster_id}", response_model=UserDetailResponse)
def user_detail(
    roster_id: str,
    days: int = Query(default=30, ge=1, le=365),
    preset: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    _admin: AdminIdentity = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    service: AnalyticsService = Depends(get_analytics_service),
) -> dict:
    window = analytics_window(settings=settings, days=days, preset=preset, start=start, end=end)
    try:
        return service.user_detail(roster_id, window=window)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="user not found") from exc
