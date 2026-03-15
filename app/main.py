from __future__ import annotations

import logging
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app.routers.export import router as export_router
from app.routers.health import router as health_router
from app.routers.history import router as history_router
from app.routers.metrics import router as metrics_router
from app.security.auth import AdminIdentity, require_admin
from app.settings import get_settings

settings = get_settings()
logging.basicConfig(level=str(settings.monitor_log_level or "INFO").upper())

app = FastAPI(title="OurA Navi Monitor", version="0.1.0")

if settings.cors_allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(health_router)
app.include_router(metrics_router)
app.include_router(history_router)
app.include_router(export_router)

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
    if request.url.path.startswith("/dashboard-assets/"):
        response.headers.setdefault("Cache-Control", "public, max-age=3600")
    return response


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/dashboard")


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(_admin: AdminIdentity = Depends(require_admin)) -> HTMLResponse:
    file_path = frontend_dir / "index.html"
    if file_path.exists():
        return HTMLResponse(file_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>dashboard files not found</h1>", status_code=500)


@app.get("/ops", response_class=RedirectResponse)
def ops_dashboard_redirect(_admin: AdminIdentity = Depends(require_admin)) -> RedirectResponse:
    return RedirectResponse(url="/dashboard")


@app.get("/ops-legacy", response_class=HTMLResponse)
def ops_dashboard_legacy(_admin: AdminIdentity = Depends(require_admin)) -> HTMLResponse:
    file_path = Path(__file__).resolve().parent / "static" / "ops.html"
    return HTMLResponse(file_path.read_text(encoding="utf-8"))
