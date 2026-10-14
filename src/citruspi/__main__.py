"""Entry point — ``python -m citruspi`` starts background monitoring & web dashboard."""

from __future__ import annotations

import argparse
import logging
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

    stop_event = threading.Event()

    # ── Background Sensor Polling (every minute by default) ───────────
    def sensor_loop() -> None:
        logger.info(
            "📊 Sensor polling active (every %ds, mock=%s)",
            config.poll_interval_sec,
            sensors.is_mock,
        )
        # Take initial reading immediately on startup
        try:
            initial = sensors.read()
            store.insert(initial)
            logger.info("📊 Startup reading: %s", initial)
        except Exception:
            logger.exception("Initial sensor reading failed")

        while not stop_event.is_set():
            stop_event.wait(config.poll_interval_sec)
            if stop_event.is_set():
                break
            try:
                reading = sensors.read()
                store.insert(reading)
                logger.debug("📊 Poll: %s", reading)
            except Exception:
                logger.exception("Sensor poll error")

    # ── Background Daily Photo Check ──────────────────────────────────
    def daily_photo_loop() -> None:
        logger.info(
            "📸 Daily timelapse photo scheduler active (scheduled for %02d:%02d)",
            config.daily_capture_hour,
            config.daily_capture_minute,
        )
        while not stop_event.is_set():
            try:
                camera.check_and_take_daily_capture(config)
            except Exception:
                logger.exception("Daily photo check error")
            # Check every 30 seconds
            stop_event.wait(30)

    sensor_thread = threading.Thread(target=sensor_loop, daemon=True, name="sensors")
    photo_thread = threading.Thread(target=daily_photo_loop, daemon=True, name="daily_photo")
    sensor_thread.start()
    photo_thread.start()

    # ── Web Server ────────────────────────────────────────────────────
    import socket

    if config.port < 1024:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((config.host, config.port))
        except PermissionError:
            logger.warning(
                "Port %d requires root/CAP_NET_BIND_SERVICE — falling back to port 8000 for local dev",
                config.port,
            )
            config.port = 8000
        except OSError:
            pass

    logger.info("")
    logger.info("🍊 CitrusPi running at http://%s:%d", config.host, config.port)
    logger.info("   Daily photo time: %02d:%02d", config.daily_capture_hour, config.daily_capture_minute)
    logger.info("   Sensor cadence:   every %ds", config.poll_interval_sec)
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
