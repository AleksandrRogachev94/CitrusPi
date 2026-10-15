"""Tests for CitrusPi daily AI photo analyzer module."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from citruspi.analyzer import (
    find_historical_photo,
    run_daily_analysis,
    select_analysis_frames,
)
from citruspi.config import Config
from citruspi.store import Store


@pytest.fixture
def test_config(tmp_path: Path) -> Config:
    return Config(
        data_dir=tmp_path / "data",
        captures_dir=tmp_path / "captures",
        ai_enabled=True,
        ai_api_key="test-gemini-key-12345",
        ai_model="gemini-3.5-flash",
        plant_layout="Left to right: Kishu A, Kishu B, Meyer, Sumo",
    )


@pytest.fixture
def store(test_config: Config) -> Store:
    return Store(test_config.data_dir)


def _make_dummy_photo(path: Path) -> Path:
    """Create a minimal valid file for photo testing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Minimal 1x1 JPEG
    path.write_bytes(
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c"
        b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xda\x00\x08"
        b"\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
    )
    return path


# ── 1. Frame Selection Tests ──────────────────────────────────────────────────


def test_find_historical_photo_never_returns_today(tmp_path: Path) -> None:
    captures = tmp_path / "captures"
    _make_dummy_photo(captures / "2026-10-13.jpg")
    yesterday_file = _make_dummy_photo(captures / "2026-10-12.jpg")

    today_date = date(2026, 10, 13)

    # Looking for today (days_ago=0) must return None because it must be strictly earlier
    match_today = find_historical_photo(
        captures, today_date=today_date, days_ago=0, tolerance_days=0
    )
    assert match_today is None

    # T-1 returns yesterday's photo
    match_t1 = find_historical_photo(
        captures, today_date=today_date, days_ago=1, tolerance_days=1
    )
    assert match_t1 is not None
    assert match_t1.name == yesterday_file.name


def test_find_historical_photo_closest_within_tolerance(tmp_path: Path) -> None:
    captures = tmp_path / "captures"
    _make_dummy_photo(captures / "2026-10-05.jpg")  # T-8
    _make_dummy_photo(captures / "2026-10-07.jpg")  # T-6
    _make_dummy_photo(captures / "2026-10-13.jpg")  # today

    today_date = date(2026, 10, 13)

    # Target is T-7 (2026-10-06). Both 2026-10-05 and 2026-10-07 are within tolerance 2 (diff 1).
    match = find_historical_photo(
        captures, today_date=today_date, days_ago=7, tolerance_days=2
    )
    assert match is not None
    assert match.name in {"2026-10-05.jpg", "2026-10-07.jpg"}

    # Target is T-7 (2026-10-06) with tolerance 0. Neither matches.
    match_strict = find_historical_photo(
        captures, today_date=today_date, days_ago=7, tolerance_days=0
    )
    assert match_strict is None


def test_find_historical_photo_respects_exclude(tmp_path: Path) -> None:
    captures = tmp_path / "captures"
    photo_t1 = _make_dummy_photo(captures / "2026-10-12.jpg")
    photo_t2 = _make_dummy_photo(captures / "2026-10-11.jpg")
    today_date = date(2026, 10, 13)

    # Exclude photo_t1 -> should pick photo_t2
    match = find_historical_photo(
        captures,
        today_date=today_date,
        days_ago=1,
        tolerance_days=1,
        exclude={photo_t1},
    )
    assert match is not None
    assert match.name == photo_t2.name


def test_frame_selection_full_chronological_no_duplicates(tmp_path: Path) -> None:
    captures = tmp_path / "captures"
    t7 = _make_dummy_photo(captures / "2026-10-06.jpg")
    t1 = _make_dummy_photo(captures / "2026-10-12.jpg")
    today = _make_dummy_photo(captures / "2026-10-13.jpg")

    today_date = date(2026, 10, 13)
    frames = select_analysis_frames(captures, today, today_date)

    assert len(frames) == 3
    # Chronological ordering
    dates = [f[0] for f in frames]
    assert dates == [date(2026, 10, 6), date(2026, 10, 12), date(2026, 10, 13)]

    # Delays
    deltas = [f[2] for f in frames]
    assert deltas == [-7, -1, 0]

    # No duplicate paths
    paths = [f[1] for f in frames]
    assert len(paths) == len(set(paths))
    assert t7 in paths
    assert t1 in paths
    assert today in paths


def test_frame_selection_when_t1_missing(tmp_path: Path) -> None:
    captures = tmp_path / "captures"
    t7 = _make_dummy_photo(captures / "2026-10-06.jpg")
    today = _make_dummy_photo(captures / "2026-10-13.jpg")
    # No photo for 2026-10-12 or 2026-10-11!

    today_date = date(2026, 10, 13)
    frames = select_analysis_frames(captures, today, today_date)

    # Only T-7 and today should be returned
    assert len(frames) == 2
    dates = [f[0] for f in frames]
    assert dates == [date(2026, 10, 6), date(2026, 10, 13)]

    # Never returns today's photo for T-1
    paths = [f[1] for f in frames]
    assert paths == [t7, today]
    assert len(paths) == len(set(paths))


def test_frame_selection_never_returns_same_file_twice(tmp_path: Path) -> None:
    captures = tmp_path / "captures"
    # Only one historical photo exists, near both T-7 and T-1 boundaries (e.g. 5 days ago)
    hist = _make_dummy_photo(captures / "2026-10-08.jpg")
    today = _make_dummy_photo(captures / "2026-10-13.jpg")

    today_date = date(2026, 10, 13)
    # T-7 (tol 2) matches 10-08 (diff 2).
    # T-1 (tol 1) target 10-12: 10-08 is diff 4 > 1, so no match.
    frames = select_analysis_frames(captures, today, today_date)

    paths = [f[1] for f in frames]
    assert len(paths) == len(set(paths))
    assert hist in paths
    assert today in paths


# ── 2. API Key Header & Absent from URL ───────────────────────────────────────


def test_api_key_sent_as_header_and_absent_from_url(
    test_config: Config, store: Store
) -> None:
    today_photo = _make_dummy_photo(test_config.captures_dir / "2026-10-13.jpg")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": json.dumps(
                                {
                                    "status": "healthy",
                                    "leaf_drop": False,
                                    "summary": "Trees look vigorous. No leaf drop observed.",
                                }
                            )
                        }
                    ]
                }
            }
        ]
    }

    with patch("httpx.post", return_value=mock_resp) as mock_post:
        result = run_daily_analysis(test_config, store, today_photo)

        assert result is not None
        assert mock_post.call_count == 1
        call_args = mock_post.call_args

        # URL verification
        url = call_args[0][0] if call_args[0] else call_args.kwargs["url"]
        expected_url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{test_config.ai_model}:generateContent"
        )
        assert url == expected_url
        assert "key=" not in url
        assert test_config.ai_api_key not in url

        # Headers verification: key ONLY in x-goog-api-key header
        headers = call_args.kwargs["headers"]
        assert headers.get("x-goog-api-key") == test_config.ai_api_key

        # Timeout and generationConfig verification
        assert call_args.kwargs["timeout"] == 60.0 or call_args.kwargs["timeout"] == 60
        gen_config = call_args.kwargs["json"]["generationConfig"]
        assert gen_config["responseMimeType"] == "application/json"
        assert gen_config["temperature"] == 0.2


# ── 3. Malformed and Fenced JSON Handling ─────────────────────────────────────


def test_fenced_json_response_parsed_and_saved(
    test_config: Config, store: Store
) -> None:
    today_photo = _make_dummy_photo(test_config.captures_dir / "2026-10-13.jpg")

    fenced_content = """```json
{
  "status": "watch",
  "leaf_drop": true,
  "summary": "Sumo tree has 2 fallen leaves on soil surface. No yellowing."
}
```"""

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": fenced_content}]
                }
            }
        ]
    }

    with patch("httpx.post", return_value=mock_resp):
        result = run_daily_analysis(test_config, store, today_photo)

        assert result is not None
        assert result["status"] == "watch"
        assert result["leaf_drop"] is True
        assert "Sumo tree" in result["summary"]

        # Verify saved in SQLite store
        report = store.latest_report()
        assert report is not None
        assert report["date"] == "2026-10-13"
        assert report["status"] == "watch"
        assert report["leaf_drop"] == 1
        assert "Sumo tree" in report["summary"]


def test_invalid_status_defaults_to_watch(
    test_config: Config, store: Store
) -> None:
    today_photo = _make_dummy_photo(test_config.captures_dir / "2026-10-13.jpg")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": json.dumps(
                                {
                                    "status": "critical_concern",  # invalid status
                                    "leaf_drop": False,
                                    "summary": "Minor curl on Meyer lemon.",
                                }
                            )
                        }
                    ]
                }
            }
        ]
    }

    with patch("httpx.post", return_value=mock_resp):
        result = run_daily_analysis(test_config, store, today_photo)

        assert result is not None
        assert result["status"] == "watch"
        report = store.latest_report()
        assert report is not None
        assert report["status"] == "watch"


def test_malformed_json_returns_none_without_raising(
    test_config: Config, store: Store
) -> None:
    today_photo = _make_dummy_photo(test_config.captures_dir / "2026-10-13.jpg")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": "I could not analyze this image. {invalid json"}]
                }
            }
        ]
    }

    with patch("httpx.post", return_value=mock_resp):
        result = run_daily_analysis(test_config, store, today_photo)
        assert result is None
        assert store.latest_report() is None


def test_non_dict_json_returns_none_without_raising(
    test_config: Config, store: Store
) -> None:
    today_photo = _make_dummy_photo(test_config.captures_dir / "2026-10-13.jpg")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": '["healthy", "no leaf drop"]'}]
                }
            }
        ]
    }

    with patch("httpx.post", return_value=mock_resp):
        result = run_daily_analysis(test_config, store, today_photo)
        assert result is None
        assert store.latest_report() is None


# ── 4. HTTP Failure Returns None Without Raising ──────────────────────────────


def test_http_connect_error_returns_none_without_raising(
    test_config: Config, store: Store
) -> None:
    today_photo = _make_dummy_photo(test_config.captures_dir / "2026-10-13.jpg")

    with patch("httpx.post", side_effect=httpx.ConnectError("Network unreachable")):
        result = run_daily_analysis(test_config, store, today_photo)
        assert result is None


def test_http_status_error_returns_none_without_raising(
    test_config: Config, store: Store
) -> None:
    today_photo = _make_dummy_photo(test_config.captures_dir / "2026-10-13.jpg")

    req = httpx.Request("POST", "https://generativelanguage.googleapis.com")
    resp = httpx.Response(status_code=500, request=req)

    error = httpx.HTTPStatusError("500 Server Error", request=req, response=resp)
    with patch("httpx.post", side_effect=error):
        result = run_daily_analysis(test_config, store, today_photo)
        assert result is None


def test_http_timeout_returns_none_without_raising(
    test_config: Config, store: Store
) -> None:
    today_photo = _make_dummy_photo(test_config.captures_dir / "2026-10-13.jpg")

    with patch("httpx.post", side_effect=httpx.TimeoutException("Read timeout")):
        result = run_daily_analysis(test_config, store, today_photo)
        assert result is None


# ── 5. Disabled / Missing Key Returns None ────────────────────────────────────


def test_ai_disabled_returns_none_without_http(
    test_config: Config, store: Store
) -> None:
    test_config.ai_enabled = False
    today_photo = _make_dummy_photo(test_config.captures_dir / "2026-10-13.jpg")

    with patch("httpx.post") as mock_post:
        result = run_daily_analysis(test_config, store, today_photo)
        assert result is None
        mock_post.assert_not_called()


def test_ai_missing_key_returns_none_without_http(
    test_config: Config, store: Store
) -> None:
    test_config.ai_api_key = ""
    today_photo = _make_dummy_photo(test_config.captures_dir / "2026-10-13.jpg")

    with patch("httpx.post") as mock_post:
        result = run_daily_analysis(test_config, store, today_photo)
        assert result is None
        mock_post.assert_not_called()


def test_parse_analysis_response() -> None:
    from citruspi.analyzer import parse_analysis_response

    # Valid response
    valid_json = '{"status": "healthy", "leaf_drop": false, "summary": "Looking great"}'
    res = parse_analysis_response(valid_json)
    assert res == {"status": "healthy", "leaf_drop": False, "summary": "Looking great"}

    # Fenced markdown
    fenced_json = '```json\n{"status": "alert", "leaf_drop": true, "summary": "Heavy drop"}\n```'
    res_fenced = parse_analysis_response(fenced_json)
    assert res_fenced == {"status": "alert", "leaf_drop": True, "summary": "Heavy drop"}

    # Invalid JSON
    assert parse_analysis_response("not valid json") is None
