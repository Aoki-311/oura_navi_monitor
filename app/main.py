from __future__ import annotations

import logging
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app.routers.admin import router as admin_router
from app.routers.analytics import router as analytics_router
from app.routers.export import router as export_router
from app.routers.health import router as health_router
from app.routers.news_usage import router as news_usage_router
from app.routers.trace import router as trace_router
from app.security.auth import AdminIdentity, require_admin
from app.settings import get_settings

settings = get_settings()
logging.basicConfig(level=str(settings.monitor_log_level or "INFO").upper())

app = FastAPI(title="OurA Navi Monitor", version="1.0.0")

if settings.cors_allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-Monitor-Admin-Email"],
    )

app.include_router(health_router)
app.include_router(analytics_router)
app.include_router(admin_router)
app.include_router(trace_router)
app.include_router(export_router)
app.include_router(news_usage_router)

frontend_dir = Path(__file__).resolve().parents[1] / "frontend"
if frontend_dir.exists():
    app.mount("/dashboard-assets", StaticFiles(directory=frontend_dir), name="dashboard-assets")


@app.middleware("http")
async def add_security_headers(request: Request, call_next) -> Response:
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    if (
        request.url.path == "/dashboard"
        or request.url.path.startswith("/api/")
    ):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    elif request.url.path.startswith("/dashboard-assets/"):
        # StaticFiles supplies ETag/Last-Modified. Revalidation keeps updated
        # releases correct while avoiding another full chart/map asset download.
        response.headers["Cache-Control"] = "private, no-cache"
    return response


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/dashboard")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> RedirectResponse:
    return RedirectResponse(url="/dashboard-assets/favicon.svg")


@app.head("/favicon.ico", include_in_schema=False)
def favicon_head() -> RedirectResponse:
    return RedirectResponse(url="/dashboard-assets/favicon.svg")


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(_admin: AdminIdentity = Depends(require_admin)) -> HTMLResponse:
    file_path = frontend_dir / "index.html"
    if file_path.exists():
        return HTMLResponse(file_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>dashboard files not found</h1>", status_code=500)
