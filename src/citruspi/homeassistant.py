"""Optional MQTT Discovery publisher. No hardware reads, plug entities, or commands.

Producers signal the worker after committed changes. MQTT failure cannot stall collection,
capture, or analysis. Live telemetry uses QoS 0 without retention/queue replay;
dated artifacts and discovery use bounded, retained QoS 1 publications.
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import threading
from datetime import UTC, datetime
from typing import Any

from citruspi import camera
from citruspi.config import Config
from citruspi.store import Store

logger = logging.getLogger(__name__)

MEASUREMENTS = (
    ("temp_c", "Canopy temperature", "temperature", "°C"),
    ("root_temp_c", "Root temperature", "temperature", "°C"),
    ("humidity", "Humidity", "humidity", "%"),
    ("vpd_kpa", "VPD", None, "kPa"),
    ("pressure", "Atmospheric pressure", "atmospheric_pressure", "hPa"),
)


class HomeAssistantPublisher:
    def __init__(self, config: Config, store: Store, client: Any) -> None:
        if config.mqtt is None:
            raise ValueError("MQTT is not configured")
        self.config = config
        self.settings = config.mqtt
        self.store = store
        self.client = client
        self.root = f"citruspi/{self.settings.device_id}"
        self.refresh = threading.Event()  # Republish all retained state on the next tick.
        self.connected = threading.Event()

    def on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code != 0:
            self.connected.clear()
            logger.warning("MQTT connection rejected (check broker authentication/permissions)")
            return
        self.connected.set()
        self.refresh.set()
        self.store.updates.notify("connected")
        logger.info("Home Assistant MQTT connected")

    def on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        self.connected.clear()

    def _publish(self, topic: str, payload: Any, *, retain: bool = False) -> None:
        encoded = payload if isinstance(payload, str) else json.dumps(payload, allow_nan=False)
        result = self.client.publish(topic, encoded, qos=1 if retain else 0, retain=retain)
        if result.rc != 0:
            raise ConnectionError("MQTT publication not accepted")

    def discovery(self) -> list[tuple[str, dict[str, Any]]]:
        """Stable entity IDs; payloads contain no config secrets or plug duplicates."""
        device = {
            "identifiers": [f"citruspi_{self.settings.device_id}"],
            "name": "CitrusPi",
            "manufacturer": "CitrusPi",
            "model": "Grow tent monitor",
            "configuration_url": self.settings.base_url.rstrip("/"),
            "suggested_area": "Grow Tent",
        }
        service = {"topic": f"{self.root}/availability"}
        entities: list[tuple[str, dict[str, Any]]] = []

        def add(
            key: str, name: str, *, component: str = "sensor",
            availability: list[dict[str, str]] | None = None, **options: Any,
        ) -> None:
            payload = {
                "name": name,
                "unique_id": f"{self.settings.device_id}_{key}",
                "device": device,
                **options,
            }
            # The connection entity reports "offline" itself, so it must stay available.
            if key != "connection":
                payload["availability"] = availability or [service]
            topic = (
                f"{self.settings.discovery_prefix}/{component}/"
                f"{self.settings.device_id}/{key}/config"
            )
            entities.append((topic, payload))

        telemetry = f"{self.root}/readings"
        for key, name, device_class, unit in MEASUREMENTS:
            options: dict[str, Any] = {
                "state_topic": telemetry,
                "value_template": "{{ value_json." + key + " }}",
                "unit_of_measurement": unit,
                "state_class": "measurement",
                "expire_after": self.config.poll_interval_sec * 3,
                "availability_mode": "all",
                "availability": [
                    service,
                    {
                        "topic": telemetry,
                        "value_template": "{{ 'online' if value_json."
                        + key
                        + " is number else 'offline' }}",
                    },
                ],
            }
            if device_class:
                options["device_class"] = device_class
            if key == "pressure":
                options["enabled_by_default"] = False
            add(key, name, **options)
        add(
            "last_reading",
            "Last reading",
            state_topic=telemetry,
            value_template="{{ value_json.timestamp }}",
            device_class="timestamp",
            entity_category="diagnostic",
            expire_after=self.config.poll_interval_sec * 3,
        )
        add(
            "connection",
            "Connection",
            component="binary_sensor",
            state_topic=f"{self.root}/availability",
            payload_on="online",
            payload_off="offline",
            device_class="connectivity",
            entity_category="diagnostic",
        )
        add(
            "daily_photo",
            "Daily photo",
            component="image",
            url_topic=f"{self.root}/photo/url",
            availability_mode="all",
            availability=[
                service,
                {
                    "topic": f"{self.root}/photo/metadata",
                    "value_template": "{{ 'online' if value_json.url else 'offline' }}",
                },
            ],
        )
        add(
            "last_capture",
            "Last daily capture",
            state_topic=f"{self.root}/photo/metadata",
            value_template="{{ value_json.captured_at }}",
            device_class="timestamp",
        )
        add(
            "assessment",
            "Plant assessment",
            state_topic=f"{self.root}/analysis/report",
            value_template="{{ value_json.status }}",
            json_attributes_topic=f"{self.root}/analysis/report",
            device_class="enum",
            options=["healthy", "watch", "alert"],
        )
        add(
            "last_analysis",
            "Last successful analysis",
            state_topic=f"{self.root}/analysis/success",
            value_template="{{ value_json.timestamp }}",
            device_class="timestamp",
        )
        return entities

    def publish_metadata(self, *, changed: set[str] | None = None) -> None:
        # None means startup/recovery: restore artifacts once from persistent storage.
        changed = {"photo", "analysis_report"} if changed is None else changed
        if "photo" in changed:
            metadata: dict[str, Any] = {"date": None, "captured_at": None, "url": None}
            photo = camera.latest_photo(self.config)
            if photo:
                stat = (self.config.captures_dir / photo["filename"]).stat()
                if stat.st_size:
                    metadata = {
                        "date": photo["date"],
                        "captured_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                        "url": self.settings.base_url.rstrip("/") + photo["url"],
                    }
            self._publish(f"{self.root}/photo/metadata", metadata, retain=True)
            self._publish(f"{self.root}/photo/url", metadata["url"] or "", retain=True)
        if "analysis_report" in changed:
            report = self.store.latest_report()
            success = self.store.latest_successful_report()
            if report:
                report["photo_url"] = (
                    self.settings.base_url.rstrip("/") + f"/captures/{report['date']}.jpg"
                )
                report["report_id"] = f"{report['date']}:{report['created_at']}"
                # A skipped frame is not a plant assessment or evidence of no leaf drop.
                if report["analysis_outcome"] != "succeeded":
                    report.update(status=None, leaf_drop=None)
                else:
                    report["leaf_drop"] = (
                        bool(report["leaf_drop"]) if report["reference_dates"] else None
                    )
            self._publish(f"{self.root}/analysis/report", report or {"status": None}, retain=True)
            self._publish(
                f"{self.root}/analysis/success",
                {"timestamp": success["created_at"] if success else None},
                retain=True,
            )

    def tick(self) -> None:
        """Publish pending changes. Updates arriving while disconnected are dropped."""
        updates = self.store.updates.take()
        if not self.connected.is_set():
            return
        restore = self.refresh.is_set()
        self.refresh.clear()
        try:
            if restore:
                for topic, payload in self.discovery():
                    self._publish(topic, payload, retain=True)
                self._publish(f"{self.root}/availability", "online", retain=True)
            reading = updates.get("reading")
            if reading:
                payload = {"timestamp": reading["timestamp"]}
                for key, *_ in MEASUREMENTS:
                    value = reading.get(key)
                    payload[key] = (
                        value if isinstance(value, (int, float)) and math.isfinite(value) else None
                    )
                self._publish(f"{self.root}/readings", payload)
            changed = set(updates) & {"photo", "analysis_report"}
            if restore or changed:
                self.publish_metadata(changed=None if restore else changed)
        except Exception:
            # Reconcile everything on the next data or connection event.
            self.refresh.set()
            raise


def run_mqtt_loop(config: Config, store: Store, stop_event: threading.Event) -> None:
    """Own all MQTT lifecycle work outside the sensor/camera/web threads."""
    if config.mqtt is None or config.mock_sensors:
        return
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        logger.error('MQTT disabled: install support with pip install -e ".[mqtt]"')
        return
    settings = config.mqtt
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"citruspi_{settings.device_id}",
        clean_session=True,
    )
    publisher = HomeAssistantPublisher(config, store, client)
    client.on_connect = publisher.on_connect
    client.on_disconnect = publisher.on_disconnect
    client.max_queued_messages_set(64)
    client.reconnect_delay_set(min_delay=1, max_delay=60)
    client.connect_timeout = 5
    client.will_set(f"{publisher.root}/availability", "offline", qos=1, retain=True)
    if settings.username:
        client.username_pw_set(settings.username, settings.password)
    try:
        if settings.tls:
            client.tls_set(ca_certs=settings.ca_file)
        client.connect_async(settings.host, settings.port, keepalive=30)
        client.loop_start()
        while not stop_event.is_set():
            try:
                publisher.tick()
            except Exception as exc:
                # Never log connection strings, credentials, or report content.
                logger.warning("MQTT publisher update failed (%s)", type(exc).__name__)
            if not stop_event.is_set():
                store.updates.ready.wait()
    except Exception as exc:
        logger.error("MQTT setup failed (%s); local monitoring continues", type(exc).__name__)
    finally:
        if client.is_connected():
            with contextlib.suppress(Exception):
                client.publish(
                    f"{publisher.root}/availability", "offline", qos=1, retain=True
                ).wait_for_publish(timeout=2)
        client.disconnect()
        client.loop_stop()
