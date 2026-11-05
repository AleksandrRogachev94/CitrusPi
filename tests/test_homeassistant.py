"""MQTT contract and failure cases, without connecting to real devices/brokers."""

from __future__ import annotations

import json
import sys
import threading
from datetime import UTC, datetime
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from jinja2 import Template

from citruspi.config import Config, MQTTConfig, load_config
from citruspi.homeassistant import HomeAssistantPublisher, run_mqtt_loop
from citruspi.models import Reading
from citruspi.store import Store


class FakeClient:
    def __init__(self):
        self.messages = []
        self.rc = 0

    def publish(self, topic, payload, qos, retain):
        self.messages.append((topic, payload, qos, retain))
        return SimpleNamespace(rc=self.rc)

    def latest(self, suffix):
        return next(m for m in reversed(self.messages) if m[0].endswith("/" + suffix))

    def payload(self, suffix):
        return json.loads(self.latest(suffix)[1])


@pytest.fixture
def bridge(tmp_path):
    cfg = Config(data_dir=tmp_path / "data", captures_dir=tmp_path / "captures",
                 mqtt=MQTTConfig(host="broker.local", username="private-user",
                                 password="private-password"))
    cfg.captures_dir.mkdir()
    store = Store(cfg.data_dir)
    return HomeAssistantPublisher(cfg, store, FakeClient())


def connect(bridge):
    bridge.on_connect(bridge.client, None, None, 0, None)


def test_discovery_contract_and_credentials(bridge):
    discovery = bridge.discovery()
    assert len(discovery) == 11
    assert len({p["unique_id"] for _, p in discovery}) == len(discovery)
    serialized = json.dumps(discovery)
    assert "private-user" not in serialized
    assert "private-password" not in serialized
    assert "heater" not in serialized
    assert "switch/" not in serialized
    assert "soil_moisture" not in serialized
    assert "light_lux" not in serialized
    assert "command_topic" not in serialized
    entities = {topic.split("/")[-2]: payload for topic, payload in discovery}
    assert "photo_status" not in entities
    assert "analysis_status" not in entities
    assert "expire_after" not in entities["last_capture"]
    assert entities["pressure"]["enabled_by_default"] is False
    assert entities["temp_c"]["expire_after"] == 180
    assert entities["root_temp_c"]["availability_mode"] == "all"
    template = Template(entities["root_temp_c"]["availability"][1]["value_template"])
    assert template.render(value_json={"root_temp_c": None}) == "offline"
    assert template.render(value_json={"root_temp_c": 0}) == "online"
    assert entities["daily_photo"]["url_topic"].endswith("/photo/url")
    assert entities["assessment"]["options"] == ["healthy", "watch", "alert"]
    assert "state_class" not in entities["assessment"]


def test_live_samples_once_unretained_with_nulls(bridge):
    connect(bridge)
    now = datetime.now(UTC)
    bridge.store.insert(Reading.create(timestamp=now.isoformat(), temp_c=0, humidity=50))
    bridge.tick()
    topic, payload, qos, retain = bridge.client.latest("readings")
    assert qos == 0 and retain is False
    assert json.loads(payload)["temp_c"] == 0
    assert json.loads(payload)["root_temp_c"] is None
    assert json.loads(payload)["timestamp"] == now.isoformat()
    bridge.tick()
    assert sum(m[0] == topic for m in bridge.client.messages) == 1


def test_reconnect_republishes_discovery_not_stale_samples(bridge):
    now = datetime.now(UTC)
    bridge.store.insert(Reading.create(timestamp=now.isoformat(), temp_c=10))
    bridge.tick()  # offline data stays in SQLite
    assert bridge.client.messages == []
    connect(bridge)
    bridge.tick()
    assert bridge.client.latest("availability")[1] == "online"
    assert not any(m[0].endswith("/readings") for m in bridge.client.messages)
    count = len([m for m in bridge.client.messages if m[0].endswith("/config")])
    bridge.on_disconnect(None, None, None, 1, None)
    connect(bridge)
    bridge.tick()
    assert len([m for m in bridge.client.messages if m[0].endswith("/config")]) == count * 2
    assert not any(m[0].endswith("/readings") for m in bridge.client.messages)
    bridge.on_disconnect(None, None, None, 1, None)
    assert not bridge.connected.is_set()


def test_failed_discovery_is_retried_and_does_not_touch_history(bridge):
    connect(bridge)
    bridge.client.rc = 4
    with pytest.raises(ConnectionError):
        bridge.tick()
    assert bridge.refresh.is_set()
    bridge.client.rc = 0
    bridge.tick()
    assert not bridge.refresh.is_set()
    assert bridge.store.latest() is None


def test_auth_rejection_does_not_claim_online(bridge):
    bridge.on_connect(bridge.client, None, None, 135, None)
    bridge.tick()
    assert not bridge.connected.is_set()
    assert bridge.client.messages == []


def test_photo_url_is_dated_and_retained(bridge):
    path = bridge.config.captures_dir / "2026-11-03.jpg"
    path.write_bytes(b"image")
    bridge.publish_metadata()
    assert bridge.client.payload("photo/metadata")["date"] == "2026-11-03"
    assert bridge.client.latest("photo/url")[1].startswith(
        "http://citruspi.local/captures/2026-11-03.jpg?v="
    )
    assert bridge.client.latest("photo/url")[2:] == (1, True)


def test_dark_report_does_not_assert_health_or_no_leaf_drop(bridge):
    (bridge.config.captures_dir / "2026-11-03.jpg").write_bytes(b"dark")
    bridge.store.save_daily_report("2026-11-02", "healthy", "Fine", reference_dates=["2026-11-01"])
    success = bridge.store.latest_successful_report()
    bridge.store.save_daily_report("2026-11-03", "watch", "Dark", analysis_outcome="skipped")
    bridge.publish_metadata()
    report = bridge.client.payload("analysis/report")
    assert report["status"] is None
    assert report["leaf_drop"] is None
    assert report["analysis_outcome"] == "skipped"
    assert bridge.client.payload("analysis/success")["timestamp"] == success["created_at"]


def test_first_report_has_no_leaf_drop_evidence_and_keeps_date(bridge):
    bridge.store.save_daily_report("2026-11-03", "healthy", "Baseline", reference_dates=[])
    bridge.publish_metadata()
    report = bridge.client.payload("analysis/report")
    assert report["status"] == "healthy"
    assert report["date"] == "2026-11-03"
    assert report["leaf_drop"] is None
    assert report["report_id"].startswith("2026-11-03:")


def test_mock_or_disabled_mqtt_never_imports_client(bridge):
    bridge.config.mock_sensors = True
    with patch("builtins.__import__", side_effect=AssertionError("unexpected import")):
        run_mqtt_loop(bridge.config, bridge.store, threading.Event())
    bridge.config.mock_sensors = False
    bridge.config.mqtt = None
    with patch("builtins.__import__", side_effect=AssertionError("unexpected import")):
        run_mqtt_loop(bridge.config, bridge.store, threading.Event())


@pytest.mark.parametrize("publish_rc", [0, 4])
def test_transport_lifecycle_and_publish_failure_isolation(bridge, monkeypatch, publish_rc):
    """Exercise the worker with a transport double; no network or installed Paho needed."""
    paho = ModuleType("paho")
    package = ModuleType("paho.mqtt")
    module = ModuleType("paho.mqtt.client")
    paho.mqtt = package
    package.client = module
    client = Mock()
    client.publish.return_value.rc = publish_rc
    client.is_connected.return_value = True
    module.CallbackAPIVersion = SimpleNamespace(VERSION2=2)
    module.Client = Mock(return_value=client)
    for name, value in (("paho", paho), ("paho.mqtt", package), ("paho.mqtt.client", module)):
        monkeypatch.setitem(sys.modules, name, value)
    client.loop_start.side_effect = lambda: client.on_connect(client, None, None, 0, None)
    stop = Mock()
    stop.is_set.side_effect = [False, False, True]
    with patch.object(bridge.store.updates.ready, "wait") as wait:
        run_mqtt_loop(bridge.config, bridge.store, stop)
        wait.assert_called_once_with()
    client.will_set.assert_called_once_with(
        "citruspi/citruspi/availability", "offline", qos=1, retain=True,
    )
    client.connect_async.assert_called_once_with("broker.local", 1883, keepalive=30)
    client.max_queued_messages_set.assert_called_once_with(64)
    client.username_pw_set.assert_called_once_with("private-user", "private-password")
    client.disconnect.assert_called_once()
    client.loop_stop.assert_called_once()
    stop.wait.assert_not_called()
    assert bridge.store.latest() is None


def test_idle_publisher_does_no_work(bridge):
    connect(bridge)
    bridge.tick()
    bridge.client.messages.clear()
    with (
        patch.object(bridge.store, "latest", side_effect=AssertionError("SQL polling")),
        patch.object(bridge.store, "latest_report", side_effect=AssertionError("SQL polling")),
        patch.object(bridge.store, "latest_successful_report",
                     side_effect=AssertionError("SQL polling")),
        patch("citruspi.homeassistant.camera.latest_photo",
              side_effect=AssertionError("archive polling")),
    ):
        bridge.tick()
        assert bridge.client.messages == []
        bridge.tick()
    assert bridge.client.messages == []


def test_saved_reading_handoff_needs_no_database_read(bridge):
    connect(bridge)
    bridge.tick()
    now = datetime.now(UTC)
    bridge.store.insert(Reading.create(timestamp=now.isoformat(), temp_c=12))
    assert bridge.store.updates.ready.is_set()
    with patch.object(bridge.store, "latest", side_effect=AssertionError("reading SQL")):
        bridge.tick()
    assert bridge.client.payload("readings")["temp_c"] == 12
    assert not bridge.store.updates.ready.is_set()


def test_reading_producer_wakes_sleeping_publisher(bridge):
    connect(bridge)
    bridge.tick()
    waiting = threading.Event()
    published = threading.Event()

    def consume():
        waiting.set()
        if bridge.store.updates.ready.wait(timeout=2):
            bridge.tick()
            published.set()

    worker = threading.Thread(target=consume)
    worker.start()
    try:
        assert waiting.wait(timeout=1)
        bridge.store.insert(Reading.create(temp_c=16, timestamp=datetime.now(UTC).isoformat()))
        assert published.wait(timeout=1)
        assert bridge.client.payload("readings")["temp_c"] == 16
    finally:
        worker.join(timeout=3)
    assert not worker.is_alive()


def test_saved_report_wakes_publisher_without_archive_scan(bridge):
    connect(bridge)
    bridge.tick()
    bridge.store.save_daily_report("2026-11-03", "healthy", "Fine")
    assert bridge.store.updates.ready.is_set()
    with patch("citruspi.homeassistant.camera.latest_photo",
               side_effect=AssertionError("unrelated archive scan")):
        bridge.tick()
    assert bridge.client.payload("analysis/report")["summary"] == "Fine"


def test_photo_notification_publishes_without_waiting_for_timer(bridge):
    connect(bridge)
    bridge.tick()
    (bridge.config.captures_dir / "2026-11-03.jpg").write_bytes(b"image")
    bridge.store.updates.notify("photo")
    bridge.tick()
    assert bridge.client.payload("photo/metadata")["date"] == "2026-11-03"
    assert "2026-11-03.jpg" in bridge.client.latest("photo/url")[1]


def test_pending_updates_are_bounded_and_new_signals_survive_take(bridge):
    for number in range(100):
        bridge.store.updates.notify("reading", {"sequence": number})
    bridge.store.updates.notify("photo")
    assert bridge.store.updates.take() == {"reading": {"sequence": 99}, "photo": None}
    assert not bridge.store.updates.ready.is_set()
    bridge.store.updates.notify("analysis_report")
    assert bridge.store.updates.ready.wait(timeout=0)
    assert bridge.store.updates.take() == {"analysis_report": None}


@pytest.mark.parametrize("settings", [
    {"host": ""}, {"host": "mqtt://broker"}, {"host": "broker", "port": True},
    {"host": "broker", "port": 65536},
    {"host": "broker", "device_id": "bad/id"},
    {"host": "broker", "base_url": "file:///etc/passwd"},
])
def test_invalid_mqtt_config(settings):
    with pytest.raises(ValueError):
        MQTTConfig(**settings)


@pytest.mark.parametrize("section", [
    "mqtt: true", "mqtt: {}", "mqtt: {host: broker, user: typo}", "mqtt: {host: 'mqtt://x'}",
])
def test_bad_mqtt_settings_disable_mqtt_without_stopping_service(tmp_path, section):
    path = tmp_path / "config.yaml"
    path.write_text(section + "\n")
    assert load_config(path).mqtt is None


def test_environment_overrides_and_secrets(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text('mqtt:\n  host: "192.168.1.241"\n  password: yaml-secret\n')
    monkeypatch.setenv("CITRUSPI_MQTT_HOST", "broker.local")
    monkeypatch.setenv("CITRUSPI_MQTT_PASSWORD", "env-secret")
    cfg = load_config(path)
    assert cfg.mqtt.host == "broker.local"
    assert cfg.mqtt.password == "env-secret"
    assert "secret" not in repr(cfg)
    path.write_text("mqtt: null\n")
    assert load_config(path).mqtt is None
