"""Tests for CitrusPi daily AI photo analyzer module."""

from __future__ import annotations

import json
from datetime import date, datetime
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


# Valid 20x20 mid-gray JPEG image decodable by OpenCV and other image tools
_VALID_TEST_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xdb\x00C\x00\x02\x01\x01"
    b"\x01\x01\x01\x02\x01\x01\x01\x02\x02\x02\x02\x02\x04\x03\x02\x02\x02\x02\x05\x04\x04\x03\x04\x06"
    b"\x05\x06\x06\x06\x05\x06\x06\x06\x07\t\x08\x06\x07\t\x07\x06\x06\x08\x0b\x08\t\n\n\n\n\n\x06\x08"
    b"\x0b\x0c\x0b\n\x0c\t\n\n\n\xff\xdb\x00C\x01\x02\x02\x02\x02\x02\x02\x05\x03\x03\x05\n\x07\x06\x07"
    b"\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n"
    b"\n\n\xff\xc0\x00\x11\x08\x00\x14\x00\x14\x03\x01\"\x00\x02\x11\x01\x03\x11\x01\xff\xc4\x00\x1f\x00"
    b"\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07"
    b"\x08\t\n\x0b\xff\xc4\x00\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04\x04\x00\x00\x01}\x01"
    b"\x02\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa\x07\"q\x142\x81\x91\xa1\x08#B\xb1\xc1\x15R\xd1\xf0$3br"
    b"\x82\t\n\x16\x17\x18\x19\x1a%&\'()*456789:CDEFGHIJSTUVWXYZcdefghijstuvwxyz\x83\x84\x85\x86\x87\x88"
    b"\x89\x8a\x92\x93\x94\x95\x96\x97\x98\x99\x9a\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xb2\xb3\xb4\xb5"
    b"\xb6\xb7\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7\xc8\xc9\xca\xd2\xd3\xd4\xd5\xd6\xd7\xd8\xd9\xda\xe1"
    b"\xe2\xe3\xe4\xe5\xe6\xe7\xe8\xe9\xea\xf1\xf2\xf3\xf4\xf5\xf6\xf7\xf8\xf9\xfa\xff\xc4\x00\x1f\x01"
    b"\x00\x03\x01\x01\x01\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07"
    b"\x08\t\n\x0b\xff\xc4\x00\xb5\x11\x00\x02\x01\x02\x04\x04\x03\x04\x07\x05\x04\x04\x00\x01\x02w\x00"
    b"\x01\x02\x03\x11\x04\x05!1\x06\x12AQ\x07aq\x13\"2\x81\x08\x14B\x91\xa1\xb1\xc1\t#3R\xf0\x15br"
    b"\xd1\n\x16$4\xe1%\xf1\x17\x18\x19\x1a&\'()*56789:CDEFGHIJSTUVWXYZcdefghijstuvwxyz\x82\x83\x84\x85"
    b"\x86\x87\x88\x89\x8a\x92\x93\x94\x95\x96\x97\x98\x99\x9a\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xb2"
    b"\xb3\xb4\xb5\xb6\xb7\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7\xc8\xc9\xca\xd2\xd3\xd4\xd5\xd6\xd7\xd8"
    b"\xd9\xda\xe2\xe3\xe4\xe5\xe6\xe7\xe8\xe9\xea\xf2\xf3\xf4\xf5\xf6\xf7\xf8\xf9\xfa\xff\xda\x00\x0c"
    b"\x03\x01\x00\x02\x11\x03\x11\x00?\x00(\xa2\x8a\x00(\xa2\x8a\x00(\xa2\x8a\x00(\xa2\x8a\x00\xff\xd9"
)


def _make_dummy_photo(path: Path) -> Path:
    """Create a minimal valid file for photo testing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_VALID_TEST_JPEG)
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
    # Deterministic tie-breaking picks the older photo (2026-10-05) to ensure a wider baseline.
    match = find_historical_photo(
        captures, today_date=today_date, days_ago=7, tolerance_days=2
    )
    assert match is not None
    assert match.name == "2026-10-05.jpg"

    # Target is T-7 (2026-10-06) with tolerance 0. Neither matches.
    match_strict = find_historical_photo(
        captures, today_date=today_date, days_ago=7, tolerance_days=0
    )
    assert match_strict is None


def test_find_historical_photo_skips_zero_byte_files(tmp_path: Path) -> None:
    captures = tmp_path / "captures"
    captures.mkdir(parents=True, exist_ok=True)
    empty_file = captures / "2026-10-12.jpg"
    empty_file.write_bytes(b"")  # 0-byte file
    valid_file = _make_dummy_photo(captures / "2026-10-11.jpg")
    today_date = date(2026, 10, 13)

    match = find_historical_photo(
        captures, today_date=today_date, days_ago=1, tolerance_days=1
    )
    assert match is not None
    assert match.name == valid_file.name


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
        assert gen_config["responseSchema"]["propertyOrdering"] == [
            "summary",
            "trees",
            "leaf_drop",
            "status",
        ]
        assert "systemInstruction" in call_args.kwargs["json"]


# ── 3. Malformed and Fenced JSON Handling ─────────────────────────────────────


def test_fenced_json_response_parsed_and_saved(
    test_config: Config, store: Store
) -> None:
    today_photo = _make_dummy_photo(test_config.captures_dir / "2026-10-13.jpg")
    _make_dummy_photo(test_config.captures_dir / "2026-10-12.jpg")

    fenced_content = """```json
{
  "summary": "Sumo tree has 2 fallen leaves on soil surface. No yellowing.",
  "leaf_drop": true,
  "status": "watch"
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


def test_invalid_status_rejected_returns_none(
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
        assert result is None
        assert store.latest_report() is None


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

    # String boolean rejected (strict bool required)
    invalid_bool = '{"status": "healthy", "leaf_drop": "false", "summary": "ok"}'
    assert parse_analysis_response(invalid_bool) is None

    # Empty / whitespace summary rejected
    empty_summary = '{"status": "healthy", "leaf_drop": false, "summary": "   "}'
    assert parse_analysis_response(empty_summary) is None

    # Unknown status enum rejected
    unknown_status = '{"status": "unknown", "leaf_drop": false, "summary": "ok"}'
    assert parse_analysis_response(unknown_status) is None


@pytest.mark.parametrize("trees", [
    None,
    "Right: yellowing",
    ["Right"],
    [{"position": "Right", "observation": "Yellowing"}],
    [{"position": " ", "observation": "Yellowing", "change": ""}],
    [{"position": "Right", "observation": 42, "change": ""}],
])
def test_invalid_structured_tree_observations_rejected(trees: object) -> None:
    from citruspi.analyzer import parse_analysis_response

    raw = json.dumps({
        "status": "watch", "leaf_drop": False, "summary": "Yellowing on right tree.",
        "trees": trees,
    })
    assert parse_analysis_response(raw) is None


@pytest.mark.parametrize("reference_days", [[], ["2026-10-06", "2026-10-12"]])
def test_structured_report_roundtrip_and_baseline_handling(
    test_config: Config, store: Store, reference_days: list[str],
) -> None:
    today = _make_dummy_photo(test_config.captures_dir / "2026-10-13.jpg")
    for day in reference_days:
        _make_dummy_photo(test_config.captures_dir / f"{day}.jpg")
    # Include an unsupported change in the baseline response to check app enforcement.
    raw = json.dumps({
        "status": "watch", "leaf_drop": True, "summary": "Yellowing on right tree.",
        "trees": [{
            "position": " Right ", "observation": " Sparse canopy. ",
            "change": " More fallen leaves than on 2026-10-12. ",
        }],
    })
    with patch("citruspi.analyzer.call_gemini_api", return_value=raw) as call:
        result = run_daily_analysis(test_config, store, today)
    assert result is not None
    assert result["reference_dates"] == reference_days
    assert result["leaf_drop"] is bool(reference_days)
    assert result["trees"] == [{
        "position": "Right", "observation": "Sparse canopy.",
        "change": "More fallen leaves than on 2026-10-12." if reference_days else "",
    }]
    saved = store.latest_report()
    assert saved is not None
    assert saved["trees"] == result["trees"]
    assert saved["reference_dates"] == reference_days
    # Metadata must reflect the same photos actually supplied to the model.
    parts = call.call_args.args[2]
    labels = [part["text"] for part in parts if part.get("text", "").startswith("Photo ")]
    assert labels == [
        f"Photo {day} ({(date(2026, 10, 13) - date.fromisoformat(day)).days}d ago):"
        for day in reference_days
    ] + ["Photo 2026-10-13 (today):"]


def test_call_gemini_api_ignores_thought_parts() -> None:
    from citruspi.analyzer import call_gemini_api

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {
                    "parts": [
                        {"thought": True, "text": "Thinking about citrus trees..."},
                        {"text": '{"status": "healthy", '},
                        {"text": '"leaf_drop": false, "summary": "Looks good"}'},
                    ]
                },
            }
        ]
    }

    with patch("httpx.post", return_value=mock_resp):
        res = call_gemini_api("gemini-3.8-flash", "test-key", [{"text": "prompt"}])
        assert res == '{"status": "healthy", "leaf_drop": false, "summary": "Looks good"}'


def test_call_gemini_api_max_tokens_returns_none() -> None:
    from citruspi.analyzer import call_gemini_api

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "candidates": [
            {
                "finishReason": "MAX_TOKENS",
                "content": {"parts": [{"text": '{"status": "health'}]},
            }
        ]
    }

    with patch("httpx.post", return_value=mock_resp):
        res = call_gemini_api("gemini-3.8-flash", "test-key", [{"text": "prompt"}])
        assert res is None


def test_get_system_instruction_formats_layout(test_config: Config) -> None:
    from citruspi.analyzer import get_system_instruction

    instruction = get_system_instruction(test_config)
    assert test_config.plant_layout in instruction
    assert "<<PLANT_LAYOUT>>" not in instruction


@pytest.mark.parametrize("layout", ["", "   "])
def test_empty_layout_uses_camera_positions(test_config: Config, layout: str) -> None:
    from citruspi.analyzer import get_system_instruction

    test_config.plant_layout = layout
    instruction = get_system_instruction(test_config)
    assert "Not specified (evaluate visible trees from left to right)." in instruction
    assert "claim no change" not in instruction


def test_build_analysis_request_signature_and_structure(test_config: Config) -> None:
    from citruspi.analyzer import build_analysis_request

    today_photo = _make_dummy_photo(test_config.captures_dir / "2026-10-13.jpg")
    parts = build_analysis_request(test_config, today_photo, "2026-10-13")
    assert len(parts) >= 2
    assert any("Today is 2026-10-13" in p.get("text", "") for p in parts)
    assert any("inlineData" in p for p in parts)


def test_check_frame_brightness_empty_and_corrupt() -> None:
    from citruspi.analyzer import check_frame_brightness

    # 0 bytes
    assert check_frame_brightness(b"") is False

    # Corrupted / invalid image bytes
    assert check_frame_brightness(b"corrupted binary data not a jpeg") is False

    # Dark frame (20x20 all zeros -> mean brightness 0.0 < 15.0)
    dark_jpeg = (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xdb\x00C\x00\x02\x01\x01"
        b"\x01\x01\x01\x02\x01\x01\x01\x02\x02\x02\x02\x02\x04\x03\x02\x02\x02\x02\x05\x04\x04\x03\x04\x06"
        b"\x05\x06\x06\x06\x05\x06\x06\x06\x07\t\x08\x06\x07\t\x07\x06\x06\x08\x0b\x08\t\n\n\n\n\n\x06\x08"
        b"\x0b\x0c\x0b\n\x0c\t\n\n\n\xff\xdb\x00C\x01\x02\x02\x02\x02\x02\x02\x05\x03\x03\x05\n\x07\x06\x07"
        b"\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n"
        b"\n\n\xff\xc0\x00\x11\x08\x00\x14\x00\x14\x03\x01\"\x00\x02\x11\x01\x03\x11\x01\xff\xc4\x00\x1f\x00"
        b"\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07"
        b"\x08\t\n\x0b\xff\xc4\x00\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04\x04\x00\x00\x01}\x01"
        b"\x02\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa\x07\"q\x142\x81\x91\xa1\x08#B\xb1\xc1\x15R\xd1\xf0$3br"
        b"\x82\t\n\x16\x17\x18\x19\x1a%&\'()*456789:CDEFGHIJSTUVWXYZcdefghijstuvwxyz\x83\x84\x85\x86\x87\x88"
        b"\x89\x8a\x92\x93\x94\x95\x96\x97\x98\x99\x9a\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xb2\xb3\xb4\xb5"
        b"\xb6\xb7\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7\xc8\xc9\xca\xd2\xd3\xd4\xd5\xd6\xd7\xd8\xd9\xda\xe1"
        b"\xe2\xe3\xe4\xe5\xe6\xe7\xe8\xe9\xea\xf1\xf2\xf3\xf4\xf5\xf6\xf7\xf8\xf9\xfa\xff\xc4\x00\x1f\x01"
        b"\x00\x03\x01\x01\x01\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07"
        b"\x08\t\n\x0b\xff\xc4\x00\xb5\x11\x00\x02\x01\x02\x04\x04\x03\x04\x07\x05\x04\x04\x00\x01\x02w\x00"
        b"\x01\x02\x03\x11\x04\x05!1\x06\x12AQ\x07aq\x13\"2\x81\x08\x14B\x91\xa1\xb1\xc1\t#3R\xf0\x15br"
        b"\xd1\n\x16$4\xe1%\xf1\x17\x18\x19\x1a&\'()*56789:CDEFGHIJSTUVWXYZcdefghijstuvwxyz\x82\x83\x84\x85"
        b"\x86\x87\x88\x89\x8a\x92\x93\x94\x95\x96\x97\x98\x99\x9a\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xb2"
        b"\xb3\xb4\xb5\xb6\xb7\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7\xc8\xc9\xca\xd2\xd3\xd4\xd5\xd6\xd7\xd8"
        b"\xd9\xda\xe2\xe3\xe4\xe5\xe6\xe7\xe8\xe9\xea\xf2\xf3\xf4\xf5\xf6\xf7\xf8\xf9\xfa\xff\xda\x00\x0c"
        b"\x03\x01\x00\x02\x11\x03\x11\x00?\x00\xfe\x7f\xe8\xa2\x8a\x00(\xa2\x8a\x00(\xa2\x8a\x00(\xa2\x8a\x00\xff\xd9"
    )
    assert check_frame_brightness(dark_jpeg) is False

    # Valid illuminated 20x20 image (mean brightness ~128.0 >= 15.0)
    assert check_frame_brightness(_VALID_TEST_JPEG) is True


def test_run_daily_analysis_handles_corrupt_photo_without_gemini_call(
    test_config: Config, store: Store
) -> None:
    corrupt_photo = test_config.captures_dir / "2026-10-13.jpg"
    corrupt_photo.parent.mkdir(parents=True, exist_ok=True)
    corrupt_photo.write_bytes(b"not an image file at all")

    with patch("httpx.post") as mock_post:
        result = run_daily_analysis(test_config, store, corrupt_photo)

        # Gemini API must NOT be called when photo cannot be decoded
        mock_post.assert_not_called()
        assert result is not None
        assert result["status"] == "watch"
        assert "invalid" in result["summary"] or "dark" in result["summary"]


def test_run_daily_analysis_respects_injected_now(
    test_config: Config, store: Store
) -> None:
    # Photo with non-date name
    photo = _make_dummy_photo(test_config.captures_dir / "tent_test.jpg")
    injected_now = datetime(2026, 11, 20, 14, 30)

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
                                    "summary": "Trees look great.",
                                }
                            )
                        }
                    ]
                }
            }
        ]
    }

    with patch("httpx.post", return_value=mock_resp):
        result = run_daily_analysis(test_config, store, photo, now=injected_now)
        assert result is not None
        assert result["date"] == "2026-11-20"
        report = store.latest_report()
        assert report is not None
        assert report["date"] == "2026-11-20"
