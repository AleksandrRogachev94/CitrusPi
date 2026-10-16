"""Tests for background runner ticks and loops."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from citruspi.alerts import AlertManager
from citruspi.config import Config
from citruspi.runner import daily_tick, sensor_tick
from citruspi.sensors import SensorHub
from citruspi.store import Store


def test_sensor_tick_records_reading_and_evaluates_alerts(tmp_path: Path) -> None:
    cfg = Config(
        mock_sensors=True,
        data_dir=tmp_path / "data",
        captures_dir=tmp_path / "captures",
        ntfy_topic="test-topic",
    )
    store = Store(cfg.data_dir)
    sensors = SensorHub(cfg)
    alerts = AlertManager(cfg)

    with (
        patch.object(alerts, "evaluate") as mock_eval,
        patch.object(alerts, "send_heartbeat") as mock_hb,
    ):
        reading = sensor_tick(sensors, store, alerts)
        assert reading is not None
        assert reading.temp_f is not None

        # Verify reading stored in SQLite
        latest = store.latest()
        assert latest is not None
        assert latest["temp_f"] == reading.temp_f

        # Verify alert evaluation and heartbeat were dispatched
        mock_eval.assert_called_once_with(reading)
        mock_hb.assert_called_once()


def test_daily_tick_runs_scheduled_capture_and_analysis(tmp_path: Path) -> None:
    cfg = Config(
        mock_sensors=True,
        data_dir=tmp_path / "data",
        captures_dir=tmp_path / "captures",
        ai_enabled=True,
        ai_api_key="fake-key",
        daily_capture_hour=22,
        daily_capture_minute=0,
    )
    store = Store(cfg.data_dir)

    target_time = datetime(2026, 10, 14, 22, 1)

    with patch("citruspi.runner.run_daily_analysis") as mock_analysis:
        mock_analysis.return_value = {"status": "healthy", "summary": "Looking great"}
        photo = daily_tick(cfg, store, now=target_time)

        assert photo is not None
        assert photo.name == "2026-10-14.jpg"
        mock_analysis.assert_called_once_with(cfg, store, photo)


def test_daily_tick_retries_analysis_when_photo_already_exists(tmp_path: Path) -> None:
    cfg = Config(
        mock_sensors=True,
        data_dir=tmp_path / "data",
        captures_dir=tmp_path / "captures",
        ai_enabled=True,
        ai_api_key="fake-key",
        daily_capture_hour=22,
        daily_capture_minute=0,
    )
    cfg.captures_dir.mkdir(parents=True, exist_ok=True)
    existing_photo = cfg.captures_dir / "2026-10-14.jpg"
    existing_photo.write_bytes(b"fake photo data")

    store = Store(cfg.data_dir)
    target_time = datetime(2026, 10, 14, 22, 5)

    with patch("citruspi.runner.run_daily_analysis") as mock_analysis:
        mock_analysis.return_value = {"status": "healthy", "summary": "Looking great"}
        photo = daily_tick(cfg, store, now=target_time)

        # photo_path returned is None because capture was already taken
        assert photo is None
        # But analysis was still executed using the existing photo
        mock_analysis.assert_called_once_with(cfg, store, existing_photo)


def test_daily_tick_skips_analysis_if_already_analyzed_today(tmp_path: Path) -> None:
    cfg = Config(
        mock_sensors=True,
        data_dir=tmp_path / "data",
        captures_dir=tmp_path / "captures",
        ai_enabled=True,
        ai_api_key="fake-key",
        daily_capture_hour=22,
        daily_capture_minute=0,
    )
    cfg.captures_dir.mkdir(parents=True, exist_ok=True)
    existing_photo = cfg.captures_dir / "2026-10-14.jpg"
    existing_photo.write_bytes(b"fake photo data")

    store = Store(cfg.data_dir)
    # Save a report for today
    store.save_daily_report("2026-10-14", "healthy", "All good", leaf_drop=False)

    target_time = datetime(2026, 10, 14, 22, 10)

    with patch("citruspi.runner.run_daily_analysis") as mock_analysis:
        photo = daily_tick(cfg, store, now=target_time)
        assert photo is None
        mock_analysis.assert_not_called()


def test_daily_tick_alerts_on_dark_frame(tmp_path: Path) -> None:
    cfg = Config(
        mock_sensors=True,
        data_dir=tmp_path / "data",
        captures_dir=tmp_path / "captures",
        ntfy_topic="test-topic",
        daily_capture_hour=22,
        daily_capture_minute=0,
    )
    cfg.captures_dir.mkdir(parents=True, exist_ok=True)
    existing_photo = cfg.captures_dir / "2026-10-14.jpg"
    existing_photo.write_bytes(b"dummy photo content")

    store = Store(cfg.data_dir)
    alerts = AlertManager(cfg)
    target_time = datetime(2026, 10, 14, 22, 5)

    with patch("citruspi.runner.check_frame_brightness", return_value=False), patch.object(
        alerts, "send_ntfy"
    ) as mock_ntfy:
        daily_tick(cfg, store, now=target_time, alerts=alerts)
        mock_ntfy.assert_called_once()
        assert "Grow Light Failure" in mock_ntfy.call_args[1]["title"]

        # Second check on same day must not re-alert (rate limited to 1 alert/day)
        daily_tick(cfg, store, now=target_time, alerts=alerts)
        assert mock_ntfy.call_count == 1


def test_daily_tick_runs_brightness_check_once_per_day_for_bright_photo(
    tmp_path: Path,
) -> None:
    cfg = Config(
        mock_sensors=True,
        data_dir=tmp_path / "data",
        captures_dir=tmp_path / "captures",
        ntfy_topic="test-topic",
        daily_capture_hour=22,
        daily_capture_minute=0,
    )
    cfg.captures_dir.mkdir(parents=True, exist_ok=True)
    today_photo = cfg.captures_dir / "2026-10-14.jpg"
    today_photo.write_bytes(b"dummy photo content")

    store = Store(cfg.data_dir)
    alerts = AlertManager(cfg)
    target_time = datetime(2026, 10, 14, 22, 5)

    with (
        patch("citruspi.runner.check_frame_brightness", return_value=True) as mock_check,
        patch.object(alerts, "send_ntfy") as mock_ntfy,
    ):
        # First tick of the day: checks brightness
        daily_tick(cfg, store, now=target_time, alerts=alerts)
        mock_check.assert_called_once()
        mock_ntfy.assert_not_called()

        # Subsequent ticks on the same day: brightness check must NOT run again
        daily_tick(cfg, store, now=target_time, alerts=alerts)
        daily_tick(cfg, store, now=target_time, alerts=alerts)
        assert mock_check.call_count == 1

        # Next day rollover: new capture taken and checked once
        tomorrow_time = datetime(2026, 10, 15, 22, 5)
        tomorrow_photo = cfg.captures_dir / "2026-10-15.jpg"
        tomorrow_photo.write_bytes(b"tomorrow photo content")

        daily_tick(cfg, store, now=tomorrow_time, alerts=alerts)
        assert mock_check.call_count == 2


