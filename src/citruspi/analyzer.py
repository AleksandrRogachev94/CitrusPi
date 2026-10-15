"""AI Analysis — daily visual health comparison of tent timelapse photos.

Analyzes plant canopy photos across frames (T-7, T-1, and today) using
Google Gemini to detect physical changes such as leaf drop, curl, yellowing, or wilt.

Verdict is dashboard-only: never sends ntfy alerts.
"""

from __future__ import annotations

import base64
from collections.abc import Iterable
import contextlib
from datetime import date, datetime, timedelta
import json
import logging
from pathlib import Path
from typing import Any

import httpx

from citruspi.config import Config
from citruspi.store import Store

logger = logging.getLogger(__name__)


def find_historical_photo(
    captures_dir: Path | str,
    today_date: date | str,
    days_ago: int,
    tolerance_days: int = 0,
    exclude: Iterable[Path | str] | None = None,
) -> Path | None:
    """Look for YYYY-MM-DD.jpg closest to today_date - days_ago.

    Must be within tolerance_days, strictly earlier than today_date,
    and not in exclude.
    """
    captures_path = Path(captures_dir)
    if not captures_path.is_dir():
        return None

    if isinstance(today_date, str):
        today = datetime.strptime(today_date, "%Y-%m-%d").date()
    else:
        today = today_date

    target_date = today - timedelta(days=days_ago)
    exclude_set = {Path(e).name for e in exclude} if exclude else set()

    candidates: list[tuple[int, Path]] = []
    for f in captures_path.glob("*.jpg"):
        try:
            f_date = datetime.strptime(f.stem, "%Y-%m-%d").date()
        except ValueError:
            continue

        if f_date >= today or f.name in exclude_set:
            continue

        diff = abs((f_date - target_date).days)
        if diff <= tolerance_days:
            candidates.append((diff, f))

    if not candidates:
        return None

    candidates.sort(key=lambda c: (c[0], c[1].name))
    return candidates[0][1]


def select_analysis_frames(
    captures_dir: Path | str,
    today_photo: Path | str,
    today_date: date | str,
) -> list[tuple[date, Path, int]]:
    """Select frames in chronological order: T-7 (tolerance 2), T-1 (tolerance 1), today."""
    captures_path = Path(captures_dir)
    today_path = Path(today_photo)
    if isinstance(today_date, str):
        today = datetime.strptime(today_date, "%Y-%m-%d").date()
    else:
        today = today_date

    frames: list[tuple[date, Path, int]] = []
    exclude: set[str] = {today_path.name}

    # T-7 (tolerance 2)
    t7 = find_historical_photo(captures_path, today, days_ago=7, tolerance_days=2, exclude=exclude)
    if t7:
        exclude.add(t7.name)
        d7 = datetime.strptime(t7.stem, "%Y-%m-%d").date()
        frames.append((d7, t7, (d7 - today).days))

    # T-1 (tolerance 1, excluding T-7)
    t1 = find_historical_photo(captures_path, today, days_ago=1, tolerance_days=1, exclude=exclude)
    if t1:
        d1 = datetime.strptime(t1.stem, "%Y-%m-%d").date()
        frames.append((d1, t1, (d1 - today).days))

    # Today
    frames.append((today, today_path, 0))

    frames.sort(key=lambda x: x[0])
    return frames


def downscale_image(image_bytes: bytes, max_dim: int = 1280) -> bytes:
    """Downscale an image to max_dim on longest edge using cv2, or return original."""
    with contextlib.suppress(Exception):
        import cv2
        import numpy as np

        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is not None:
            h, w = img.shape[:2]
            if max(h, w) > max_dim:
                scale = max_dim / max(h, w)
                resized = cv2.resize(
                    img,
                    (max(1, int(w * scale)), max(1, int(h * scale))),
                    interpolation=cv2.INTER_AREA,
                )
                ok, buf = cv2.imencode(".jpg", resized, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if ok:
                    return buf.tobytes()
    return image_bytes


def strip_code_fences(text: str) -> str:
    """Defensively strip markdown code fences from JSON text."""
    text = text.strip()
    if "```" in text:
        first = text.find("```")
        last = text.rfind("```")
        if first != -1 and last > first:
            content = text[first:last]
            lines = content.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            return "\n".join(lines).strip()
    return text


def build_analysis_request(
    config: Config,
    store: Store,
    today_path: Path,
    today_date: date,
) -> list[dict[str, Any]]:
    """Build the Gemini generateContent parts list for the daily comparison."""
    frames = select_analysis_frames(config.captures_dir, today_path, today_date)

    summary = store.summary_last_hours(hours=24)
    t_min, t_max = summary.get("temp_min_f"), summary.get("temp_max_f")
    r_min, r_max = summary.get("root_temp_min_f"), summary.get("root_temp_max_f")

    canopy_info = (
        f"{t_min:.1f}°F min / {t_max:.1f}°F max"
        if t_min is not None and t_max is not None
        else "N/A"
    )
    root_info = (
        f"{r_min:.1f}°F min / {r_max:.1f}°F max"
        if r_min is not None and r_max is not None
        else "N/A"
    )

    prompt = (
        f"Plant layout: {config.plant_layout}\n"
        f"24h temperatures — Canopy: {canopy_info}, Root: {root_info}\n\n"
        "Report per tree using the layout. "
        "Describe ONLY visible physical differences between frames "
        "(fallen leaves on the floor or pots, curl, yellowing, wilt). "
        "If nothing clearly differs, say 'No visible change'. "
        "Do NOT speculate about causes.\n\n"
        "Output strictly valid JSON with this structure:\n"
        '{"status": "healthy"|"watch"|"alert", "leaf_drop": bool, "summary": "str"}'
    )

    parts: list[dict[str, Any]] = []
    for f_date, f_path, delta in frames:
        delta_str = "today" if delta == 0 else f"{abs(delta)}d ago"
        parts.append({"text": f"Frame {f_date} ({delta_str}):"})
        b64_img = base64.b64encode(downscale_image(f_path.read_bytes())).decode("ascii")
        parts.append({"inlineData": {"mimeType": "image/jpeg", "data": b64_img}})
    parts.append({"text": prompt})
    return parts


class PermanentAIError(Exception):
    """Fatal non-retryable error from AI API (e.g. 400, 401, 403, 404)."""


def check_frame_brightness(image_bytes: bytes, min_mean_brightness: float = 15.0) -> bool:
    """Check if the frame is sufficiently illuminated for visual analysis.

    Returns False if an image frame (larger than 10x10) is too dark/black.
    """
    try:
        import cv2
        import numpy as np

        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)
        if img is not None:
            h, w = img.shape[:2]
            if h > 10 and w > 10:
                mean_val = float(np.mean(img))
                if mean_val < min_mean_brightness:
                    logger.warning(
                        "Frame too dark for AI analysis (mean brightness: %.1f < %.1f)",
                        mean_val,
                        min_mean_brightness,
                    )
                    return False
        return True
    except Exception:
        return True


def call_gemini_api(
    model: str,
    api_key: str,
    parts: list[dict[str, Any]],
    timeout: float = 60.0,
) -> str | None:
    """Call Google Gemini generateContent API and return raw response text."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    try:
        response = httpx.post(
            url,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json={
                "contents": [{"parts": parts}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
            },
            timeout=timeout,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if 400 <= status < 500 and status != 429:
            # Permanent client error (invalid API key, non-existent model, bad request)
            logger.error(
                "Permanent HTTP %d error calling Gemini API: %s",
                status,
                exc.response.text,
            )
            raise PermanentAIError(f"HTTP {status}: {exc.response.text}") from exc
        logger.warning("Transient HTTP %d error calling Gemini API: %s", status, exc)
        return None
    except Exception as exc:
        logger.warning("Network or unexpected error calling Gemini API: %s", exc)
        return None

    data = response.json()
    candidates = data.get("candidates", [])
    if not candidates:
        return None

    return candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")


def parse_analysis_response(raw_text: str) -> dict[str, Any] | None:
    """Parse JSON text into validated verdict dict."""
    try:
        parsed = json.loads(strip_code_fences(raw_text))
    except Exception:
        logger.warning("Failed to parse AI JSON response: %s", raw_text)
        return None

    if not isinstance(parsed, dict):
        logger.warning("AI model returned non-dict JSON: %s", type(parsed).__name__)
        return None

    status = str(parsed.get("status", "watch")).strip().lower()
    if status not in {"healthy", "watch", "alert"}:
        status = "watch"

    leaf_drop = bool(parsed.get("leaf_drop", False))
    summary_text = str(parsed.get("summary", "")).strip()

    return {
        "status": status,
        "leaf_drop": leaf_drop,
        "summary": summary_text,
    }


def run_daily_analysis(
    config: Config,
    store: Store,
    today_photo: Path | str,
) -> dict[str, Any] | None:
    """Run daily AI comparison across timelapse frames.

    Returns parsed report dict or None on error or when AI is disabled.
    Verdict is dashboard-only: never sends ntfy.
    """
    try:
        if not config.ai_enabled or not config.ai_api_key:
            return None

        today_path = Path(today_photo)
        if not today_path.is_file():
            today_path = config.captures_dir / today_path.name
        if not today_path.is_file():
            logger.warning("Today's photo not found: %s", today_photo)
            return None

        try:
            today_date = datetime.strptime(today_path.stem, "%Y-%m-%d").date()
        except ValueError:
            today_date = datetime.now().date()
        today_str = today_date.strftime("%Y-%m-%d")

        photo_bytes = today_path.read_bytes()
        if not check_frame_brightness(photo_bytes):
            verdict = {
                "status": "watch",
                "leaf_drop": False,
                "summary": (
                    "Photo too dark for canopy health analysis "
                    "(check tent lighting or capture schedule)."
                ),
            }
            store.save_daily_report(
                today_str,
                verdict["status"],
                verdict["summary"],
                verdict["leaf_drop"],
            )
            return {
                "date": today_str,
                "status": verdict["status"],
                "leaf_drop": verdict["leaf_drop"],
                "summary": verdict["summary"],
            }

        parts = build_analysis_request(config, store, today_path, today_date)
        raw_text = call_gemini_api(config.ai_model, config.ai_api_key, parts)
        if not raw_text:
            return None

        verdict = parse_analysis_response(raw_text)
        if not verdict:
            return None

        store.save_daily_report(
            today_str,
            verdict["status"],
            verdict["summary"],
            verdict["leaf_drop"],
        )

        return {
            "date": today_str,
            "status": verdict["status"],
            "leaf_drop": verdict["leaf_drop"],
            "summary": verdict["summary"],
        }
    except PermanentAIError:
        raise
    except Exception:
        logger.exception("Daily AI analysis failed")
        return None
