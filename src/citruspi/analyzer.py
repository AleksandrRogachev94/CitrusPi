"""AI Analysis — daily visual health comparison of tent timelapse photos.

Analyzes plant canopy photos across frames (T-7, T-1, and today) using
Google Gemini to detect physical changes such as leaf drop, curl, yellowing, or wilt.

Verdict is dashboard-only: never sends ntfy alerts.
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
from collections.abc import Iterable
from datetime import date, datetime, timedelta
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

        try:
            if f.stat().st_size == 0:
                continue
        except OSError:
            continue

        diff = abs((f_date - target_date).days)
        if diff <= tolerance_days:
            candidates.append((diff, f))

    if not candidates:
        return None

    # Sort primarily by day difference from target. In case of a tie (e.g. diff=1 for both
    # T-8 and T-6 when target is T-7), sorting ascending by filename picks the older date (T-8),
    # which is preferred as it provides a wider baseline away from T-1.
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


ANALYSIS_SYSTEM_INSTRUCTION = (
    "You monitor potted citrus trees in a grow tent from a fixed camera. Each request has "
    "today's photo (the subject) and, when available, photos from about 1 and 7 days ago, "
    "each labelled with its date. Assess the visible canopy today and use earlier photos "
    "to identify changes. Report visible evidence, not diagnoses or treatment advice.\n\n"
    "Plant layout:\n<<PLANT_LAYOUT>>\n\n"
    "Assess the visible canopy:\n"
    "- Condition today: drooping or wilting, leaf curl, yellowing or browning, "
    "spotting, thin branches.\n"
    "- Change since the earlier photos: newly fallen leaves (floor, pot) and anything "
    "new, better or worse. The 1-day photo shows sudden events, the 7-day photo slow trends.\n"
    "Describe a persistent problem as ongoing; do not imply it is healthy or worsening.\n\n"
    "Rules:\n"
    "- Judge structure directly. Describe visible colour cautiously (grow lights tint colour); "
    "claim colour changes only relative to the same visible area under comparable lighting.\n"
    "- Do not mistake camera shift, glare, condensation, exposure or moved plants for health "
    "changes. Report clearly supported growth or recovery; pale new growth alone is not damage.\n"
    "- Do not count trees or assign overlapping leaves to individual trees. Do not assume "
    "hidden foliage is healthy.\n"
    "- If a comparison is uncertain, say so. If the view prevents judging condition, use "
    "'watch' unless a clear visible concern already warrants 'alert'.\n"
    "- Report fallen leaves only when recognizable as leaves, not bark or potting mix. "
    "Avoid counts or quantity words unless clearly supported; leaf_drop requires clear evidence "
    "of newly fallen or missing leaves compared with an earlier photo.\n"
    "- If only today's photo is provided, describe current condition only. Changes cannot be "
    "assessed: leave each change field empty and set leaf_drop to false. "
    "Visible fallen leaves alone do not establish new leaf drop.\n\n"
    "Status reflects the strongest visible concern today, not just whether it changed:\n"
    "- healthy: no visible concerns; continue monitoring.\n"
    "- watch: minor or uncertain concern; check again.\n"
    "- alert: clear concern warranting inspection (obvious wilting, widespread yellowing or "
    "browning, substantial new leaf drop, or a minor problem clearly worsening), "
    "even if it is ongoing or this is the first photo.\n\n"
    "Output plain text in the JSON fields, without Markdown.\n"
    "- summary: one short headline (at most 15 words) highlighting the main concern "
    "or that no visible concerns were found.\n"
    "- trees: only notable findings tied to physical areas, most concerning first; "
    "not one entry per tree or area. "
    "Omit repetitive descriptions of healthy foliage; use an empty list if there are no findings. "
    "Position must be a physical location such as 'Right side' or 'Rear center', or a configured "
    "tree name when clearly identifiable.\n"
    "- observation: at most 20 words describing the finding today. "
    "Use ordinary language, such as 'yellowing' rather than 'chlorotic'.\n"
    "- change: at most 15 words describing a supported change or an ongoing issue "
    "relative to the dated photos. Leave empty when there are no reference photos. "
    "If a comparison is obscured or uncertain, say so.\n"
    "Do not repeat baseline disclaimers in the headline or observations; the app shows "
    "which reference photos are available."
)

ANALYSIS_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "summary": {
            "type": "STRING",
            "description": "Main concern or no visible concerns, at most 15 words.",
        },
        # Preserve the existing key/storage shape; entries now describe findings by area.
        "trees": {
            "type": "ARRAY",
            "description": "Notable plant findings tied to physical locations; empty if none.",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "position": {
                        "type": "STRING",
                        "description": "Physical area or identifiable tree; never a report topic.",
                    },
                    "observation": {
                        "type": "STRING",
                        "description": "Visible condition today, at most 20 words.",
                    },
                    "change": {
                        "type": "STRING",
                        "description": "Photo-supported comparison, or empty without references.",
                    },
                },
                "required": ["position", "observation", "change"],
                "propertyOrdering": ["position", "observation", "change"],
            },
        },
        "leaf_drop": {
            "type": "BOOLEAN",
            "description": (
                "Clear evidence of newly fallen or missing leaves since an earlier photo. "
                "Existing fallen leaves don't count. False without references or if uncertain."
            ),
        },
        "status": {
            "type": "STRING",
            "enum": ["healthy", "watch", "alert"],
            "description": "Strongest visible concern today, including ongoing concerns.",
        },
    },
    "required": ["summary", "trees", "leaf_drop", "status"],
    "propertyOrdering": ["summary", "trees", "leaf_drop", "status"],
}


def get_system_instruction(config: Config) -> str:
    """Format the system instruction with the configured plant layout."""
    layout = (
        config.plant_layout.strip()
        if config.plant_layout.strip()
        else "Not specified (describe visible findings by camera-relative area)."
    )
    return ANALYSIS_SYSTEM_INSTRUCTION.replace("<<PLANT_LAYOUT>>", layout)


def build_analysis_request(
    config: Config,
    today_photo: Path | str,
    today_date: date | str,
    *,
    frames: list[tuple[date, Path, int]] | None = None,
) -> list[dict[str, Any]]:
    """Build the Gemini generateContent parts list for the daily comparison."""
    today_path = Path(today_photo)
    if isinstance(today_date, str):
        parsed_date = datetime.strptime(today_date, "%Y-%m-%d").date()
    else:
        parsed_date = today_date

    if frames is None:
        frames = select_analysis_frames(config.captures_dir, today_path, parsed_date)

    parts: list[dict[str, Any]] = []
    has_t7 = False
    has_t1 = False

    for f_date, f_path, delta in frames:
        if delta == 0:
            delta_str = "today"
        else:
            delta_str = f"{abs(delta)}d ago"
            if abs(delta) >= 5:
                has_t7 = True
            else:
                has_t1 = True

        parts.append({"text": f"Photo {f_date} ({delta_str}):"})
        b64_img = base64.b64encode(downscale_image(f_path.read_bytes())).decode("ascii")
        parts.append({"inlineData": {"mimeType": "image/jpeg", "data": b64_img}})

    if has_t7 and has_t1:
        baseline_info = "7-day and 1-day reference photos available."
    elif has_t1:
        baseline_info = "1-day reference photo available."
    elif has_t7:
        baseline_info = "7-day reference photo available."
    else:
        baseline_info = "No prior reference photos available (today's photo only)."

    parts.append({
        "text": (
            f"Today is {parsed_date}. Reference photos: {baseline_info}\n"
            "Return a short headline and concise findings by visible area. "
            "Only describe changes supported by the reference photos."
        )
    })
    return parts


class PermanentAIError(Exception):
    """Fatal non-retryable error from AI API (e.g. 400, 401, 403, 404)."""


def check_frame_brightness(image_bytes: bytes, min_mean_brightness: float = 15.0) -> bool:
    """Check if the frame is sufficiently illuminated for visual analysis.

    Returns False if an image frame is empty, corrupted/unreadable, or too dark.
    Returns True if valid and bright (or if OpenCV is not installed).
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return True

    try:
        if not image_bytes:
            logger.warning("Frame is empty (0 bytes)")
            return False

        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)
        if img is None:
            logger.warning("Frame could not be decoded (corrupt or invalid image format)")
            return False

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
        logger.exception("Error decoding image for brightness check")
        return False


def call_gemini_api(
    model: str,
    api_key: str,
    parts: list[dict[str, Any]],
    timeout: float = 60.0,
    system_instruction: str | None = None,
    response_schema: dict[str, Any] | None = None,
) -> str | None:
    """Call Google Gemini generateContent API and return raw response text."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    gen_config: dict[str, Any] = {
        "responseMimeType": "application/json",
        "responseSchema": response_schema or ANALYSIS_RESPONSE_SCHEMA,
        "mediaResolution": "MEDIA_RESOLUTION_HIGH",
    }
    payload: dict[str, Any] = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": gen_config,
    }
    if system_instruction:
        payload["systemInstruction"] = {
            "parts": [{"text": system_instruction}]
        }

    try:
        response = httpx.post(
            url,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=payload,
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
        prompt_feedback = data.get("promptFeedback", {})
        if "blockReason" in prompt_feedback:
            logger.warning("Gemini prompt blocked: %s", prompt_feedback.get("blockReason"))
        return None

    candidate = candidates[0]
    finish_reason = candidate.get("finishReason")
    if finish_reason == "MAX_TOKENS":
        logger.warning("Gemini generation hit MAX_TOKENS")
        return None

    content = candidate.get("content", {})
    parts_out = content.get("parts", [])
    text_parts = [
        p.get("text", "")
        for p in parts_out
        if isinstance(p, dict) and "text" in p and not p.get("thought", False)
    ]
    raw_output = "".join(text_parts)
    logger.debug("Gemini raw response: %s", raw_output)
    return raw_output


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

    status = str(parsed.get("status", "")).strip().lower()
    if status not in {"healthy", "watch", "alert"}:
        logger.warning("Invalid or missing status in AI response: %s", parsed.get("status"))
        return None

    leaf_drop = parsed.get("leaf_drop")
    if not isinstance(leaf_drop, bool):
        logger.warning("Missing or non-boolean leaf_drop in AI response: %s", leaf_drop)
        return None

    summary = parsed.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        logger.warning("Missing or empty summary in AI response")
        return None

    verdict: dict[str, Any] = {
        "status": status,
        "leaf_drop": leaf_drop,
        "summary": summary.strip(),
    }
    # Accept older responses too; new API requests require structured tree entries.
    if "trees" in parsed:
        trees = parsed["trees"]
        if not isinstance(trees, list):
            logger.warning("Non-list trees in AI response")
            return None
        cleaned_trees = []
        for tree in trees:
            if not isinstance(tree, dict) or any(
                not isinstance(tree.get(key), str)
                for key in ("position", "observation", "change")
            ):
                logger.warning("Invalid tree entry in AI response")
                return None
            cleaned = {key: tree[key].strip() for key in ("position", "observation", "change")}
            if not cleaned["position"] or not cleaned["observation"]:
                logger.warning("Empty tree position or observation in AI response")
                return None
            cleaned_trees.append(cleaned)
        verdict["trees"] = cleaned_trees
    return verdict


def run_daily_analysis(
    config: Config,
    store: Store,
    today_photo: Path | str,
    now: datetime | None = None,
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

        curr_now = now or datetime.now()
        try:
            today_date = datetime.strptime(today_path.stem, "%Y-%m-%d").date()
        except ValueError:
            today_date = curr_now.date()
        today_str = today_date.strftime("%Y-%m-%d")

        photo_bytes = today_path.read_bytes()
        if not check_frame_brightness(photo_bytes):
            dark_verdict: dict[str, Any] = {
                "status": "watch",
                "leaf_drop": False,
                "summary": (
                    "Photo too dark or invalid for canopy health analysis "
                    "(check camera, tent lighting, or capture schedule)."
                ),
            }
            store.save_daily_report(
                today_str,
                str(dark_verdict["status"]),
                str(dark_verdict["summary"]),
                bool(dark_verdict["leaf_drop"]),
            )
            return {
                "date": today_str,
                "status": str(dark_verdict["status"]),
                "leaf_drop": bool(dark_verdict["leaf_drop"]),
                "summary": str(dark_verdict["summary"]),
            }

        frames = select_analysis_frames(config.captures_dir, today_path, today_date)
        reference_dates = [frame_date.isoformat() for frame_date, _, delta in frames if delta < 0]
        parts = build_analysis_request(config, today_path, today_date, frames=frames)
        system_instruction = get_system_instruction(config)
        raw_text = call_gemini_api(
            config.ai_model,
            config.ai_api_key,
            parts,
            system_instruction=system_instruction,
        )
        if not raw_text:
            return None

        verdict: dict[str, Any] | None = parse_analysis_response(raw_text)
        if not verdict:
            return None

        trees = verdict.get("trees", [])
        if not reference_dates:
            verdict["leaf_drop"] = False
            for tree in trees:
                tree["change"] = ""

        store.save_daily_report(
            today_str,
            str(verdict["status"]),
            str(verdict["summary"]),
            bool(verdict["leaf_drop"]),
            trees=trees,
            reference_dates=reference_dates,
        )

        return {
            "date": today_str,
            "status": verdict["status"],
            "leaf_drop": verdict["leaf_drop"],
            "summary": verdict["summary"],
            "trees": trees,
            "reference_dates": reference_dates,
        }
    except PermanentAIError:
        raise
    except Exception:
        logger.exception("Daily AI analysis failed")
        return None
