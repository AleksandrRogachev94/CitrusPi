"""Time-of-use pricing follows local time and excludes missing observations."""

from datetime import UTC, datetime, timedelta

import pytest
import yaml
from fastapi.testclient import TestClient

from citruspi.config import Config, ElectricityConfig, load_config
from citruspi.electricity import interval_cost
from citruspi.models import PowerReading
from citruspi.sensors import SensorHub
from citruspi.store import Store
from citruspi.web import create_app


@pytest.mark.parametrize(("timestamp", "rate"), [
    ("2026-10-19T17:00:00Z", 0.1),  # 13:00 EDT, off-peak
    ("2026-10-19T18:00:00Z", 0.3),  # 14:00 EDT, peak
    ("2026-10-19T22:00:00Z", 0.1),  # 18:00 EDT, off-peak
    ("2026-10-20T04:00:00Z", 0.05),  # midnight EDT
    ("2026-10-20T10:00:00Z", 0.1),  # 06:00 EDT
    ("2026-10-18T18:00:00Z", 0.1),  # Sunday afternoon
    ("2026-11-26T19:00:00Z", 0.1),  # listed holiday, 14:00 EST
    ("2026-12-01T19:00:00Z", 0.3),  # winter peak, 14:00 EST
])
def test_periods_delivery_weekends_and_holidays(timestamp, rate):
    rates = ElectricityConfig(
        peak_rate=0.3, off_peak_rate=0.1, super_off_peak_rate=0.05,
        delivery_rate=0.02, holidays=["2026-11-26"],
    )
    start = datetime.fromisoformat(timestamp)
    assert interval_cost(start, start + timedelta(hours=1), 1000, 1000, rates) == (
        pytest.approx(rate + 0.02)
    )


def test_split_rate_boundary_with_changing_load():
    start = datetime(2026, 10, 19, 17, 59, tzinfo=UTC)
    rates = ElectricityConfig(peak_rate=0.3, off_peak_rate=0.1)
    # 0 -> 1000W before peak, 1000 -> 2000W after peak.
    assert interval_cost(start, start + timedelta(minutes=2), 0, 2000, rates) == (
        pytest.approx((500 * 0.1 + 1500 * 0.3) / 60000)
    )


@pytest.mark.parametrize(("start", "end", "overnight_hours"), [
    ("2026-03-08T05:00:00Z", "2026-03-08T10:00:00Z", 5),
    ("2026-11-01T04:00:00Z", "2026-11-01T11:00:00Z", 7),
])
def test_dst_uses_actual_elapsed_hours(start, end, overnight_hours):
    rates = ElectricityConfig(super_off_peak_rate=0.05)
    assert interval_cost(
        datetime.fromisoformat(start), datetime.fromisoformat(end), 1000, 1000, rates,
    ) == pytest.approx(overnight_hours * 0.05)


def test_store_window_clipping_cost_unknowns_and_gaps(tmp_path):
    store = Store(tmp_path)
    now = datetime(2026, 10, 19, 19, tzinfo=UTC)
    start = now - timedelta(hours=1)
    for seconds, watts in ((-60, 0), (60, 2000), (120, None), (180, 2000),
                           (600, 2000), (660, 2000)):
        store.insert_power(PowerReading(
            "heater", (start + timedelta(seconds=seconds)).isoformat().replace('+00:00', 'Z'),
            power_w=watts,
        ))
    rates = ElectricityConfig(peak_rate=0.3)
    summary = store.power_summary("heater", 60, now, hours=1, electricity=rates)
    assert summary["estimated_kwh"] == 0.0583  # clipped ramp + one valid minute
    assert summary["estimated_cost_usd"] == 0.0175
    assert summary["coverage_pct"] == 3.3
    assert store.power_summary("missing", 60, now, electricity=rates)[
        "estimated_cost_usd"
    ] is None
    assert store.power_summary("heater", 60, now)["estimated_cost_usd"] is None


def test_zero_load_is_zero_cost(tmp_path):
    store = Store(tmp_path)
    now = datetime.now(UTC).replace(microsecond=0)
    for timestamp in (now - timedelta(minutes=1), now):
        store.insert_power(PowerReading("heater", timestamp.isoformat(), power_w=0))
    assert store.power_summary("heater", 60, now, electricity=ElectricityConfig())[
        "estimated_cost_usd"
    ] == 0


def test_yaml_rates_and_optional_disable(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"electricity": {"peak_rate": 0.4}}))
    assert load_config(path).electricity.peak_rate == 0.4
    assert Config().electricity is None
    assert Config(electricity=None).electricity is None


@pytest.mark.parametrize("settings", [
    [], {"timezone": "bad/timezone"}, {"timezone": None}, {"peak_rate": -1},
    {"off_peak_rate": float("nan")}, {"delivery_rate": True},
    {"super_off_peak_rate": "0.05"}, {"peak_start_hour": 18},
    {"peak_end_hour": 25}, {"peak_start_hour": True},
    {"super_off_peak_end_hour": 16}, {"peak_weekdays": [7]},
    {"peak_weekdays": [True]}, {"peak_weekdays": None}, {"holidays": "2026-01-01"},
    {"holidays": ["2026-02-30"]}, {"holidays": [20260101]}, {"unknown": 1},
])
def test_invalid_electricity_settings(settings):
    with pytest.raises(ValueError):
        Config(electricity=settings)


def test_api_usage_and_cost_follow_selected_range(tmp_path):
    cfg = Config(
        mock_sensors=True, data_dir=tmp_path / "data", captures_dir=tmp_path / "captures",
        electricity={"peak_rate": 0.2, "off_peak_rate": 0.2, "super_off_peak_rate": 0.2},
        plugs=[{"id": "heater", "name": "Heater", "host": "192.0.2.1"}],
    )
    store = Store(cfg.data_dir)
    now = datetime.now(UTC).replace(microsecond=0)
    for age in (7200, 7140, 120, 60):
        store.insert_power(PowerReading(
            "heater", (now - timedelta(seconds=age)).strftime('%Y-%m-%dT%H:%M:%SZ'),
            power_w=1000,
        ))
    client = TestClient(create_app(cfg, store, SensorHub(cfg)))
    short = client.get('/api/plugs?hours=1').json()[0]["summary"]
    long = client.get('/api/plugs?hours=24').json()[0]["summary"]
    assert short["estimated_cost_usd"] == 0.0033
    assert long["estimated_cost_usd"] == 0.0067
    assert short["estimated_kwh"] < long["estimated_kwh"]
    page = client.get('/').text
    assert page.index('aria-label="Dashboard history"') < page.index('class="dashboard-grid"')
    assert 'id="electricity-cost"' in page
    assert 'Supply only; delivery charges excluded.' in page
