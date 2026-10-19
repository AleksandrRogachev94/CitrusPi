"""FastAPI web application — dashboard, live camera stream, API, and timelapse gallery."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from citruspi import camera
from citruspi.alerts import AlertManager
from citruspi.config import Config
from citruspi.sensors import SensorHub
from citruspi.store import Store

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"


def create_app(
    config: Config,
    store: Store,
    sensors: SensorHub,
    alerts: AlertManager | None = None,
) -> FastAPI:
    """Build and return the FastAPI application."""

    app = FastAPI(title="CitrusPi", version="0.1.0")
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    def static_version(filename: str = "style.css") -> str:
        target = _STATIC_DIR / filename
        if target.is_file():
            return str(int(target.stat().st_mtime))
        return "1"

    templates.env.globals["static_version"] = static_version

    # Serve static assets (CSS/JS)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # Serve captured photos
    config.captures_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/captures",
        StaticFiles(directory=str(config.captures_dir)),
        name="captures",
    )

    # ── Pages (synchronous def runs in anyio threadpool) ──────────────

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request) -> HTMLResponse:
        latest = store.latest()
        photo = camera.latest_photo(config)
        latest_report = store.latest_report()
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "reading": latest,
                "summary": store.summary_last_hours(24),
                "photo": photo,
                "latest_report": latest_report,
                "is_mock": sensors.is_mock,
                "config": config,
            },
        )

    @app.get("/gallery", response_class=HTMLResponse)
    def gallery(request: Request) -> HTMLResponse:
        raw_photos = camera.list_photos(config)
        reports_map = store.reports_by_date()
        photos: list[dict[str, Any]] = [
            {**p, "report": reports_map.get(p["date"])} for p in raw_photos
        ]

        all_reports = store.daily_reports(limit=365)
        # Create map of photos by date for the health log view
        photos_map = {p["date"]: p for p in photos}
        for r in all_reports:
            r["photo"] = photos_map.get(r["date"])

        return templates.TemplateResponse(
            request=request,
            name="gallery.html",
            context={
                "photos": photos,
                "reports": all_reports,
                "has_reports": bool(all_reports),
            },
        )

    # ── Live Camera Stream ────────────────────────────────────────────

    @app.get("/stream")
    def video_stream() -> StreamingResponse:
        """Stream MJPEG video on-demand at ~5 fps.

        Only active while the browser has the stream open.
        """
        return StreamingResponse(
            camera.generate_mjpeg_stream(config),
            media_type="multipart/x-mixed-replace; boundary=frame",
        )

    # ── JSON API ──────────────────────────────────────────────────────

    @app.get("/api/current")
    def api_current() -> dict[str, Any]:
        return store.latest() or {}

    @app.get("/api/history")
    def api_history(hours: int = 24) -> list[dict[str, Any]]:
        safe_hours = max(1, min(hours, 720))
        return store.history(hours=safe_hours)

    @app.get("/api/photos")
    def api_photos() -> list[dict[str, str]]:
        return camera.list_photos(config)

    @app.get("/api/plugs")
    def api_plugs() -> list[dict[str, Any]]:
        now = datetime.now(UTC)
        result = []
        for plug in config.plugs:
            latest = store.latest_power(plug.id)
            stale = latest is None or (
                now - datetime.fromisoformat(latest["timestamp"])
            ).total_seconds() > config.plug_poll_interval_sec * 3
            result.append({
                "id": plug.id, "name": plug.name, "role": plug.role,
                "latest": latest, "stale": stale,
                "monitoring": not config.mock_sensors,
                "stale_after_sec": config.plug_poll_interval_sec * 3,
                "summary": store.power_summary(plug.id, config.plug_poll_interval_sec, now),
            })
        return result

    @app.get("/api/plugs/history")
    def api_plug_history(hours: int = 24) -> list[dict[str, Any]]:
        safe_hours = max(1, min(hours, 720))
        return [{
            "id": plug.id, "name": plug.name, "role": plug.role,
            "max_gap_sec": config.plug_poll_interval_sec * 2,
            "activity": store.power_activity(
                plug.id, safe_hours, config.plug_poll_interval_sec * 2,
            ),
            "recorded_since": store.first_power_timestamp(plug.id),
            "readings": store.power_history(
                plug.id, safe_hours, max_gap_sec=config.plug_poll_interval_sec * 2,
            ),
        } for plug in config.plugs]

    @app.get("/api/reports")
    def api_reports(limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 365))
        return store.daily_reports(limit=safe_limit)

    @app.post("/api/capture")
    def api_capture() -> dict[str, Any]:
        """Manually trigger a test photo capture saved to manual.jpg."""
        path = camera.capture_frame(config, filename="manual.jpg")
        if path:
            return {"status": "ok", "path": str(path), "url": f"/captures/{path.name}"}
        return {"status": "error", "message": "capture failed"}

    # ── Fragments for htmx ────────────────────────────────────────────

    @app.get("/fragments/readings", response_class=HTMLResponse)
    def fragment_readings(request: Request) -> HTMLResponse:
        latest = store.latest()
        return templates.TemplateResponse(
            request=request,
            name="_readings.html",
            context={
                "reading": latest,
                "summary": store.summary_last_hours(24),
            },
        )

    return app
