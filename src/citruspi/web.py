"""FastAPI web application — dashboard, live camera stream, API, and timelapse gallery."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from citruspi import camera
from citruspi.config import Config
from citruspi.sensors import SensorHub
from citruspi.store import Store

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"


def create_app(config: Config, store: Store, sensors: SensorHub) -> FastAPI:
    """Build and return the FastAPI application."""

    app = FastAPI(title="CitrusPi", version="0.1.0")
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

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
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "reading": latest,
                "photo": photo,
                "is_mock": sensors.is_mock,
                "config": config,
            },
        )

    @app.get("/gallery", response_class=HTMLResponse)
    def gallery(request: Request) -> HTMLResponse:
        photos = camera.list_photos(config)
        return templates.TemplateResponse(
            request=request,
            name="gallery.html",
            context={
                "photos": photos,
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
        return store.history(hours=hours)

    @app.get("/api/photos")
    def api_photos() -> list[dict[str, str]]:
        return camera.list_photos(config)

    @app.post("/api/capture")
    def api_capture() -> dict[str, Any]:
        """Manually trigger a photo capture."""
        path = camera.capture_frame(config)
        if path:
            return {"status": "ok", "path": str(path), "url": f"/captures/{path.name}"}
        return {"status": "error", "message": "capture failed"}

    @app.post("/api/poll")
    def api_poll() -> dict[str, Any]:
        """Manually poll sensors and store in SQLite."""
        reading = sensors.read()
        store.insert(reading)
        return reading.to_dict() if hasattr(reading, "to_dict") else reading

    # ── Fragments for htmx ────────────────────────────────────────────

    @app.get("/fragments/readings", response_class=HTMLResponse)
    def fragment_readings(request: Request) -> HTMLResponse:
        latest = store.latest()
        return templates.TemplateResponse(
            request=request,
            name="_readings.html",
            context={
                "reading": latest,
            },
        )

    return app
