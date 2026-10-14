"""Entry point — ``python -m citruspi`` starts background monitoring & web dashboard."""

from __future__ import annotations

import argparse
import logging
import sys
import threading


def main() -> None:
    parser = argparse.ArgumentParser(description="CitrusPi monitor")
    parser.add_argument("--config", help="Path to config.yaml")
    parser.add_argument("--port", type=int, help="Web server port")
    parser.add_argument("--mock", action="store_true", help="Force mock sensors")
    parser.add_argument(
        "--test-alert",
        action="store_true",
        help="Send test ntfy alert and heartbeat ping, then exit",
    )
    parser.add_argument(
        "--analyze-now",
        action="store_true",
        help="Run AI analysis on latest timelapse capture, print JSON result, and exit",
    )
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
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logger = logging.getLogger("citruspi")

    if args.analyze_now:
        if not config.ai_enabled:
            print("AI analysis is disabled (set ai_enabled: true in config.yaml)", file=sys.stderr)
            sys.exit(1)
        if not config.ai_api_key:
            print(
                "AI API key missing (set ai_api_key in config.yaml or CITRUSPI_AI_API_KEY env var)",
                file=sys.stderr,
            )
            sys.exit(1)

        from citruspi import camera
        from citruspi.analyzer import run_daily_analysis
        from citruspi.store import Store

        latest = camera.latest_photo(config)
        if not latest:
            print("No photos found in captures directory", file=sys.stderr)
            sys.exit(1)

        photo_path = config.captures_dir / latest["filename"]
        store = Store(config.data_dir)
        result = run_daily_analysis(config, store, photo_path)
        if result is None:
            print("AI analysis failed (see logs for details)", file=sys.stderr)
            sys.exit(1)

        import json

        print(json.dumps(result, indent=2))
        sys.exit(0)

    from citruspi.alerts import AlertManager

    alerts = AlertManager(config)

    if args.test_alert:
        print("Testing CitrusPi alerts...")
        print(f"  ntfy URL:      {config.ntfy_url}")
        print(f"  ntfy topic:    {config.ntfy_topic or '(none)'}")
        print(f"  heartbeat URL: {config.heartbeat_url or '(none)'}")

        ntfy_ok = alerts.send_ntfy(
            title="CitrusPi Test Alert",
            message="This is a test notification from CitrusPi.",
            priority="urgent",
        )
        hb_ok = alerts.send_heartbeat()

        if ntfy_ok:
            print("✓ ntfy test alert sent successfully")
        else:
            print("✗ ntfy test alert failed (check topic / network)")

        if config.heartbeat_url:
            if hb_ok:
                print("✓ Heartbeat ping sent successfully")
            else:
                print("✗ Heartbeat ping failed (check URL / network)")
        else:
            print("ℹ No heartbeat_url configured")

        all_ok = ntfy_ok and (hb_ok if config.heartbeat_url else True)
        sys.exit(0 if all_ok else 1)
        return

    import socket

    if config.port < 1024:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((config.host, config.port))
        except PermissionError:
            logger.warning(
                "Port %d requires root/CAP_NET_BIND_SERVICE — "
                "falling back to port 8000 for local dev",
                config.port,
            )
            config.port = 8000
        except OSError:
            pass

    from citruspi.runner import run_daily_loop, run_sensor_loop
    from citruspi.sensors import SensorHub
    from citruspi.store import Store
    from citruspi.web import create_app

    store = Store(config.data_dir)
    sensors = SensorHub(config)
    app = create_app(config, store, sensors, alerts=alerts)

    # Startup notification
    try:
        hostname = socket.gethostname()
        alerts.send_ntfy(
            title="CitrusPi Online",
            message=(
                f"CitrusPi monitor is online on {hostname} "
                f"(http://{hostname}.local:{config.port})."
            ),
            priority="default",
            tags="lemon",
        )
    except Exception:
        logger.exception("Startup alert error")

    stop_event = threading.Event()

    sensor_thread = threading.Thread(
        target=run_sensor_loop,
        args=(config, sensors, store, alerts, stop_event),
        daemon=True,
        name="sensors",
    )
    photo_thread = threading.Thread(
        target=run_daily_loop,
        args=(config, store, stop_event),
        daemon=True,
        name="daily_photo",
    )
    sensor_thread.start()
    photo_thread.start()

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
