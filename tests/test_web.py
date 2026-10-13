"""Tests for the web API endpoints."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from citruspi.config import Config
from citruspi.sensors import SensorHub
from citruspi.store import Store
from citruspi.web import create_app


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


def test_api_current(client: TestClient) -> None:
    r = client.get("/api/current")
    assert r.status_code == 200
    data = r.json()
    assert "temp_c" in data


def test_api_history(client: TestClient) -> None:
    r = client.get("/api/history?hours=1")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert len(r.json()) >= 1


def test_api_poll(client: TestClient) -> None:
    r = client.post("/api/poll")
    assert r.status_code == 200
    assert "temp_c" in r.json()


def test_gallery_loads(client: TestClient) -> None:
    r = client.get("/gallery")
    assert r.status_code == 200
    assert "Gallery" in r.text
