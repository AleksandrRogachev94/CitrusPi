"""Entry point — ``python -m citruspi`` starts everything."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import threading
import time


def main() -> None:
    parser = argparse.ArgumentParser(description="CitrusPi monitor")
    parser.add_argument("--config", help="Path to config.yaml")
    parser.add_argument("--port", type=int, help="Web server port")
    parser.add_argument("--mock", action="store_true", help="Force mock sensors")
    args = parser.parse_args()

    from citruspi.config import load_config

    config = load_config(args.config)
    if args.port:
        config.port = args.port
    if args.mock:
        config.mock_sensors = True

    logging.basicConfig(
        level=config.log_level.upper(),
        format="%(asctime)s  %(name)-24s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )
    logger = logging.getLogger("citruspi")

    from citruspi import camera
    from citruspi.sensors import SensorHub
    from citruspi.store import Store
    from citruspi.web import create_app

    store = Store(config.data_dir)
    sensors = SensorHub(config)
    app = create_app(config, store, sensors)

    # ── Background polling ────────────────────────────────────────────

    stop_event = threading.Event()

    def sensor_loop() -> None:
        """Poll sensors at the configured interval."""
        logger.info(
            "📊 Sensor polling started (every %ds, mock=%s)",
            config.poll_interval_sec,
            sensors.is_mock,
        )
        while not stop_event.is_set():
            try:
                reading = sensors.read()
                store.insert(reading)
                logger.info("📊 %s", reading)
            except Exception:
                logger.exception("Sensor poll error")
            stop_event.wait(config.poll_interval_sec)

    def capture_loop() -> None:
        """Capture photos at the configured interval."""
        logger.info("📸 Capture loop started (every %ds)", config.capture_interval_sec)
        while not stop_event.is_set():
            try:
                camera.capture_frame(config)
            except Exception:
                logger.exception("Capture error")
            stop_event.wait(config.capture_interval_sec)

    sensor_thread = threading.Thread(target=sensor_loop, daemon=True, name="sensors")
    capture_thread = threading.Thread(target=capture_loop, daemon=True, name="capture")
    sensor_thread.start()
    capture_thread.start()

    # ── Web server ────────────────────────────────────────────────────

    logger.info("")
    logger.info("🍊 CitrusPi starting at http://%s:%d", config.host, config.port)
    logger.info("")

    import uvicorn  # type: ignore[import-untyped]

    try:
        uvicorn.run(app, host=config.host, port=config.port, log_level=config.log_level)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        logger.info("🍊 CitrusPi stopped.")


if __name__ == "__main__":
    main()
