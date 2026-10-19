"""Tests for the web API endpoints."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from citruspi.config import Config
from citruspi.models import Reading
from citruspi.sensors import SensorHub
from citruspi.store import Store
from citruspi.web import create_app


def test_power_api_staleness_unknowns_escaping_and_credentials(tmp_path):
    from citruspi.models import PowerReading

    cfg = Config(
        mock_sensors=True, data_dir=tmp_path / "data", captures_dir=tmp_path / "captures",
        kasa_username="private-email", kasa_password="private-password",
        plugs=[{"id": "heater", "name": "Heater <script>", "host": "192.0.2.1"},
               {"id": "light", "name": "Light", "host": "192.0.2.2"}],
    )
    store = Store(cfg.data_dir)
    timestamp = (datetime.now(UTC) - timedelta(minutes=10)).strftime('%Y-%m-%dT%H:%M:%SZ')
    store.insert_power(PowerReading("heater", timestamp, True, 200, True, 1.25))
    client = TestClient(create_app(cfg, store, SensorHub(cfg)))
    current = client.get("/api/plugs").json()
    assert current[0]["stale"] is True
    assert current[0]["latest"]["relay_on"] is True
    assert current[0]["stale_after_sec"] == 180
    assert current[1]["latest"] is None
    assert current[1]["summary"]["estimated_kwh"] is None
    assert current[0]["monitoring"] is False
    for url in ("/", "/api/plugs", "/api/plugs/history?hours=24"):
        response = client.get(url)
        assert response.status_code == 200
        assert "private-email" not in response.text
        assert "private-password" not in response.text
    assert 'id="powerChart"' in client.get("/").text
    assert 'id="activityChart"' in client.get("/").text
    plug_hist = client.get("/api/plugs/history").json()[0]
    assert plug_hist["readings"][0]["power_w"] == 200
    assert "activity" in plug_hist
    assert plug_hist["recorded_since"] == timestamp
    with patch.object(store, "power_history", return_value=[]) as history:
        client.get('/api/plugs/history?hours=999999')
        assert history.call_args.args[1] == 720


def test_plug_endpoints_disabled_by_default(client):
    assert client.get("/api/plugs").json() == []
    assert client.get("/api/plugs/history").json() == []
    assert 'id="powerChart"' not in client.get("/").text


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    cfg = Config(
        mock_sensors=True,
        data_dir=tmp_path / "data",
        captures_dir=tmp_path / "captures",
    )
    store = Store(cfg.data_dir)
    sensors = SensorHub(cfg)
    # Seed one reading
    reading = sensors.read()
    store.insert(reading)
    app = create_app(cfg, store, sensors)
    return TestClient(app)


def test_dashboard_loads(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "CitrusPi" in r.text
    assert "Thermal Balance" in r.text


def test_api_current(client: TestClient) -> None:
    r = client.get("/api/current")
    assert r.status_code == 200
    data = r.json()
    assert "temp_c" in data
    assert "root_temp_c" in data


def test_api_history(client: TestClient) -> None:
    r = client.get("/api/history?hours=1")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert len(r.json()) >= 1


def test_gallery_loads(client: TestClient) -> None:
    r = client.get("/gallery")
    assert r.status_code == 200
    assert "Gallery" in r.text


def test_fragments_readings_loads(client: TestClient) -> None:
    r = client.get("/fragments/readings")
    assert r.status_code == 200
    assert "Temperature" in r.text
    assert "Humidity" in r.text
    assert "Root Zone Temp" in r.text


@pytest.mark.parametrize(("reading", "status"), [
    (None, "No readings yet"),
    (Reading.create(), "Canopy sensor offline"),
    (Reading.create(temp_c=15, humidity=65), "Root probe offline"),
    (Reading.create(temp_c=15, humidity=65, root_temp_c=14), "Telemetry Feed"),
])
def test_sensor_status_in_dashboard_and_fragment(
    tmp_path: Path, reading: Reading | None, status: str,
) -> None:
    cfg = Config(mock_sensors=True, data_dir=tmp_path / "data",
                 captures_dir=tmp_path / "captures", poll_interval_sec=300)
    store = Store(cfg.data_dir)
    if reading is not None:
        store.insert(reading)
    client = TestClient(create_app(cfg, store, SensorHub(cfg)))
    for url in ("/", "/fragments/readings"):
        html = client.get(url).text
        assert f'data-status="{status}">{status}</span>' in html
        dot = "live-dot" if status == "Telemetry Feed" else "stale-dot"
        assert f'class="{dot}" id="telemetry-live-dot"' in html
    assert 'data-stale-after="900"' in client.get("/").text


def test_dashboard_with_ai_report(tmp_path: Path) -> None:
    cfg = Config(
        mock_sensors=True,
        data_dir=tmp_path / "data",
        captures_dir=tmp_path / "captures",
    )
    store = Store(cfg.data_dir)
    store.save_daily_report(
        date_str="2026-10-13",
        status="healthy",
        summary="All trees in good health. No leaf drop.",
        leaf_drop=False,
    )
    sensors = SensorHub(cfg)
    app = create_app(cfg, store, sensors)
    tc = TestClient(app)

    r = tc.get("/")
    assert r.status_code == 200
    assert "AI Canopy Health" in r.text
    assert "HEALTHY" in r.text
    assert "All trees in good health" in r.text
    assert "View complete plant health history" in r.text


def test_gallery_with_ai_reports_and_photos(tmp_path: Path) -> None:
    cfg = Config(
        mock_sensors=True,
        data_dir=tmp_path / "data",
        captures_dir=tmp_path / "captures",
    )
    cfg.captures_dir.mkdir(parents=True, exist_ok=True)
    (cfg.captures_dir / "2026-10-14.jpg").write_bytes(b"dummy")

    store = Store(cfg.data_dir)
    store.save_daily_report(
        date_str="2026-10-14",
        status="watch",
        summary="Meyer leaf drop detected.",
        leaf_drop=True,
    )

    sensors = SensorHub(cfg)
    app = create_app(cfg, store, sensors)
    tc = TestClient(app)

    r = tc.get("/gallery")
    assert r.status_code == 200
    assert "Health Log" in r.text
    assert "WATCH" in r.text
    assert "Leaf Drop" in r.text
    assert "Meyer leaf drop detected." in r.text


def test_api_reports(tmp_path: Path) -> None:
    cfg = Config(
        mock_sensors=True,
        data_dir=tmp_path / "data",
        captures_dir=tmp_path / "captures",
    )
    store = Store(cfg.data_dir)
    store.save_daily_report(
        date_str="2026-10-14",
        status="alert",
        summary="Severe wilt.",
        leaf_drop=True,
    )
    sensors = SensorHub(cfg)
    app = create_app(cfg, store, sensors)
    tc = TestClient(app)

    r = tc.get("/api/reports")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["date"] == "2026-10-14"
    assert data[0]["status"] == "alert"
    assert data[0]["leaf_drop"] == 1


@pytest.mark.parametrize("reference_dates", [[], ["2026-10-06", "2026-10-12"]])
def test_structured_report_display_and_escaping(
    tmp_path: Path, reference_dates: list[str],
) -> None:
    cfg = Config(mock_sensors=True, data_dir=tmp_path / "data", captures_dir=tmp_path / "captures")
    store = Store(cfg.data_dir)
    trees = [{
        "position": "Right <tree>",
        "observation": "Sparse canopy with yellowing leaves. <script>alert(1)</script>",
        "change": "Yellowing persists since 2026-10-06.",
    }]
    store.save_daily_report(
        "2026-10-13", "alert", "Right tree needs attention.", trees=trees,
        reference_dates=reference_dates,
    )
    tc = TestClient(create_app(cfg, store, SensorHub(cfg)))
    for url in ("/", "/gallery"):
        html = tc.get(url).text
        assert "Right tree needs attention." in html
        assert '<dl class="ai-tree-list">' in html
        assert "Right &lt;tree&gt;" in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
        assert "<script>alert(1)</script>" not in html
        if reference_dates:
            assert "Compared with photos from 2026-10-06 and 2026-10-12." in html
            assert "Yellowing persists since 2026-10-06." in html
            assert "First photo" not in html
        else:
            assert html.count("First photo · Changes cannot be assessed yet.") == 1
            assert "Yellowing persists" not in html
    report = tc.get("/api/reports").json()[0]
    assert report["trees"] == trees
    assert report["reference_dates"] == reference_dates


def test_cache_busting_version_in_html(tmp_path: Path) -> None:
    cfg = Config(mock_sensors=True, data_dir=tmp_path / "data")
    store = Store(cfg.data_dir)
    sensors = SensorHub(cfg)
    app = create_app(cfg, store, sensors)
    tc = TestClient(app)

    r = tc.get("/")
    assert r.status_code == 200
    assert 'href="/static/style.css?v=' in r.text


def test_temperature_cards_show_only_last_24_hours_range(tmp_path: Path) -> None:
    cfg = Config(mock_sensors=True, data_dir=tmp_path / "data", captures_dir=tmp_path / "captures")
    store = Store(cfg.data_dir)
    now = datetime.now(UTC)
    for age, canopy, root in ((25, -20, -10), (2, 10, 12), (1, 20, 16)):
        store.insert(Reading.create(
            timestamp=(now - timedelta(hours=age)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            temp_c=canopy, root_temp_c=root,
        ))
    client = TestClient(create_app(cfg, store, SensorHub(cfg)))
    for url in ("/", "/fragments/readings"):
        html = client.get(url).text
        assert "24h · Low 50.0°F · High 68.0°F" in html
        assert "24h · Low 53.6°F · High 60.8°F" in html
        assert "24h · Low -4.0°F" not in html


def test_temperature_range_hidden_when_there_are_no_measurements(tmp_path: Path) -> None:
    cfg = Config(mock_sensors=True, data_dir=tmp_path / "data", captures_dir=tmp_path / "captures")
    store = Store(cfg.data_dir)
    store.insert(Reading.create())
    client = TestClient(create_app(cfg, store, SensorHub(cfg)))
    for url in ("/", "/fragments/readings"):
        assert "24h · Low" not in client.get(url).text


