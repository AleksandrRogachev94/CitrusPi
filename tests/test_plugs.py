"""Read-only adapter, reconnects and failure isolation without physical devices."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from kasa import AuthenticationError, Module, UnsupportedDeviceError

from citruspi.config import Config, PlugConfig
from citruspi.plugs import Activity, PlugMonitor, _run, check_plugs
from citruspi.store import Store


@pytest.mark.parametrize(("status", "exit_code"), [("ok", 0), ("auth_error", 1)])
def test_diagnostic_cli_exit_code_and_no_sensor_startup(tmp_path, status, exit_code, capsys):
    from citruspi.__main__ import main

    path = tmp_path / "config.yaml"
    path.write_text("plugs:\n  - {id: heater, name: Heater, host: 192.0.2.1}\n")
    with (
        patch("sys.argv", ["citruspi", "--config", str(path), "--check-plugs"]),
        patch("citruspi.plugs.check_plugs", AsyncMock(return_value=[{"status": status}])),
        patch("citruspi.sensors.SensorHub") as sensors,
        pytest.raises(SystemExit) as exc,
    ):
        main()
    assert exc.value.code == exit_code
    assert f'"status": "{status}"' in capsys.readouterr().out
    sensors.assert_not_called()


def device(watts=200, today=0.75):
    return SimpleNamespace(
        is_on=True, model="KP125M", hw_info={"sw_ver": "1.2.3"},
        modules={Module.Energy: SimpleNamespace(
            current_consumption=watts, consumption_today=today,
        )}, update=AsyncMock(), disconnect=AsyncMock(),
    )


def test_activity_hysteresis_debounce_and_reset():
    plug = PlugConfig("heater", "Heater", "192.0.2.1", active_watts=10, idle_watts=3)
    activity = Activity()
    assert activity.update(5, plug) is None  # Ambiguous first observation.
    assert activity.update(0, plug) is False
    assert activity.update(11, plug) is False  # One spike cannot change state.
    assert activity.update(5, plug) is False
    assert activity.update(11, plug) is False
    assert activity.update(12, plug) is True
    assert activity.update(5, plug) is True
    assert activity.update(0, plug) is True
    assert activity.update(0, plug) is False
    assert activity.update(None, plug) is None
    assert activity.update(5, plug) is None


@pytest.mark.asyncio
async def test_reads_and_reuses_connection_without_writes():
    cfg = Config(plugs=[PlugConfig("heater", "Heater", "192.0.2.1")],
                 kasa_username="user", kasa_password="secret")
    dev = device()
    monitor = PlugMonitor(cfg)
    with patch("kasa.Discover.discover_single", AsyncMock(return_value=dev)) as discover:
        first = await monitor.poll(cfg.plugs[0])
        second = await monitor.poll(cfg.plugs[0])
    discover.assert_awaited_once_with(
        "192.0.2.1", username="user", password="secret", discovery_timeout=3, timeout=3,
    )
    assert first.power_w == second.power_w == 200
    assert first.energy_today_kwh == 0.75
    assert first.relay_on is True
    assert first.active is True
    assert first.status == "ok"
    assert dev.update.await_count == 2
    await monitor.close()
    dev.disconnect.assert_awaited_once()


@pytest.mark.parametrize(("error", "status"), [
    (AuthenticationError("password=secret"), "auth_error"),
    (UnsupportedDeviceError("secret payload"), "unsupported"),
    (TimeoutError("secret"), "unreachable"),
])
@pytest.mark.asyncio
async def test_failed_update_clears_values_and_reconnects(error, status, caplog):
    cfg = Config(plugs=[PlugConfig("light", "Light", "192.0.2.2")])
    failed = device()
    failed.update.side_effect = error
    recovered = device(50)
    monitor = PlugMonitor(cfg)
    with patch("kasa.Discover.discover_single", AsyncMock(side_effect=[failed, recovered])):
        reading = await monitor.poll(cfg.plugs[0])
        assert reading.status == status
        assert reading.power_w is reading.active is reading.relay_on is None
        assert reading.energy_today_kwh is None
        failed.disconnect.assert_awaited_once()
        assert "secret" not in caplog.text
        assert (await monitor.poll(cfg.plugs[0])).status == "ok"
    await monitor.close()


@pytest.mark.parametrize("watts", [None, float("nan"), float("inf"), -1, True])
@pytest.mark.asyncio
async def test_missing_or_invalid_power_is_unknown(watts):
    cfg = Config(plugs=[PlugConfig("light", "Light", "192.0.2.2")])
    dev = device(watts)
    with patch("kasa.Discover.discover_single", AsyncMock(return_value=dev)):
        monitor = PlugMonitor(cfg)
        reading = await monitor.poll(cfg.plugs[0])
        assert reading.status == "energy_unavailable"
        assert reading.power_w is reading.active is None
        assert reading.relay_on is True
        await monitor.close()


@pytest.mark.asyncio
async def test_zero_watts_and_missing_energy_module():
    cfg = Config(plugs=[PlugConfig("light", "Light", "192.0.2.2")])
    dev = device(0)
    monitor = PlugMonitor(cfg)
    with patch("kasa.Discover.discover_single", AsyncMock(return_value=dev)):
        zero = await monitor.poll(cfg.plugs[0])
        assert zero.power_w == 0 and zero.active is False and zero.status == "ok"
        dev.modules = {}
        assert (await monitor.poll(cfg.plugs[0])).status == "energy_unavailable"
    await monitor.close()


@pytest.mark.asyncio
async def test_timeout_is_bounded(monkeypatch):
    cfg = Config(plugs=[PlugConfig("light", "Light", "192.0.2.2")])
    monkeypatch.setattr("citruspi.plugs._TIMEOUT", 0.01)

    async def blocked(*args, **kwargs):
        await asyncio.sleep(60)

    monitor = PlugMonitor(cfg)
    with patch("kasa.Discover.discover_single", blocked):
        reading = await monitor.poll(cfg.plugs[0])
    assert reading.status == "unreachable"


@pytest.mark.asyncio
async def test_diagnostic_closes_connection_and_does_not_create_storage(tmp_path):
    cfg = Config(data_dir=tmp_path / "not-created",
                 plugs=[PlugConfig("light", "Light", "192.0.2.2")])
    dev = device()
    with patch("kasa.Discover.discover_single", AsyncMock(return_value=dev)):
        result = await check_plugs(cfg)
    assert result[0]["model"] == "KP125M"
    assert result[0]["firmware"] == "1.2.3"
    assert not cfg.data_dir.exists()
    dev.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_one_plug_failure_does_not_stop_other_plug_collection(tmp_path):
    cfg = Config(plugs=[PlugConfig("heater", "Heater", "192.0.2.1"),
                        PlugConfig("light", "Light", "192.0.2.2")])
    store = Store(tmp_path)
    stop = threading.Event()
    dev = device()

    async def update():
        stop.set()

    dev.update.side_effect = update
    with patch("kasa.Discover.discover_single", AsyncMock(side_effect=[TimeoutError(), dev])):
        await _run(cfg, store, stop)
    assert store.latest_power("heater")["status"] == "unreachable"
    assert store.latest_power("light")["power_w"] == 200
    dev.disconnect.assert_awaited_once()
