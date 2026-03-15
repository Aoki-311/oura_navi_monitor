from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter

from app.settings import get_settings

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "service": "oura_navi_monitor",
        "sourceService": settings.monitor_source_service,
        "project": settings.monitor_project_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
