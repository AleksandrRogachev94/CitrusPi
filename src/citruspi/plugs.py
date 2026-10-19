"""Optional, read-only LAN monitoring. All Kasa IO stays on one event loop."""

from __future__ import annotations

import asyncio
import logging
import math
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from citruspi.config import Config, PlugConfig
from citruspi.models import PowerReading
from citruspi.store import Store

if TYPE_CHECKING:
    from kasa import Device

logger = logging.getLogger(__name__)
_TIMEOUT = 10


def _nonnegative(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and (
        math.isfinite(value) and value >= 0
    ):
        return float(value)
    return None


@dataclass
class Activity:
    """Hysteresis plus two observations to confirm a change after initial detection."""

    state: bool | None = None
    pending: bool | None = None

    def update(self, watts: float | None, plug: PlugConfig) -> bool | None:
        if watts is None:
            self.state = self.pending = None
            return None
        candidate = True if watts >= plug.active_watts else (
            False if watts <= plug.idle_watts else self.state
        )
        if self.state is None or candidate == self.state or self.pending == candidate:
            self.state, self.pending = candidate, None
        else:
            self.pending = candidate
        return self.state


class PlugMonitor:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.devices: dict[str, Device] = {}
        self.activity = {p.id: Activity() for p in config.plugs}
        self.status: dict[str, str] = {}

    async def _disconnect(self, plug_id: str) -> None:
        device = self.devices.pop(plug_id, None)
        if device is not None:
            try:
                async with asyncio.timeout(2):
                    await device.disconnect()
            except Exception:
                pass  # Best effort cleanup; the next poll reconnects.

    async def poll(self, plug: PlugConfig) -> PowerReading:
        from kasa import AuthenticationError, Discover, Module, UnsupportedDeviceError

        relay = power = energy_today = None
        status = "ok"
        try:
            async with asyncio.timeout(_TIMEOUT):
                device = self.devices.get(plug.id)
                if device is None:
                    device = await Discover.discover_single(
                        plug.host,
                        username=self.config.kasa_username or None,
                        password=self.config.kasa_password or None,
                        discovery_timeout=3,
                        timeout=3,
                    )
                    if device is None:
                        raise TimeoutError
                    self.devices[plug.id] = device
                await device.update()
                relay = device.is_on
                meter = device.modules.get(Module.Energy)
                if meter is not None:
                    power = _nonnegative(meter.current_consumption)
                    energy_today = _nonnegative(meter.consumption_today)
                if power is None:
                    status = "energy_unavailable"
        except AuthenticationError:
            status = "auth_error"
        except UnsupportedDeviceError:
            status = "unsupported"
        except Exception:
            status = "unreachable"

        if status not in ("ok", "energy_unavailable"):
            relay = power = energy_today = None
            await self._disconnect(plug.id)
        if self.status.get(plug.id) != status:
            # Exception messages may contain device payloads or credentials: omit them.
            logger.log(logging.INFO if status == "ok" else logging.WARNING,
                       "Plug %s: %s", plug.id, status)
            self.status[plug.id] = status
        return PowerReading(
            plug_id=plug.id,
            timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            relay_on=relay,
            power_w=power,
            energy_today_kwh=energy_today,
            active=self.activity[plug.id].update(power, plug),
            status=status,
        )

    async def close(self) -> None:
        for plug_id in list(self.devices):
            await self._disconnect(plug_id)


async def check_plugs(config: Config) -> list[dict]:
    """One read per configured plug, without writes or device-setting changes."""
    monitor = PlugMonitor(config)
    try:
        results = []
        for plug in config.plugs:
            reading = await monitor.poll(plug)
            result = asdict(reading)
            device = monitor.devices.get(plug.id)
            if device is not None:
                result.update(model=device.model, firmware=device.hw_info.get("sw_ver"))
            results.append(result)
        return results
    finally:
        await monitor.close()


async def _run(config: Config, store: Store, stop: threading.Event) -> None:
    monitor = PlugMonitor(config)
    try:
        while not stop.is_set():
            started = asyncio.get_running_loop().time()
            for plug in config.plugs:
                if stop.is_set():
                    break
                try:
                    store.insert_power(await monitor.poll(plug))
                except Exception:
                    logger.exception("Could not record plug %s", plug.id)
            remaining = config.plug_poll_interval_sec - (
                asyncio.get_running_loop().time() - started
            )
            # Event.wait wakes immediately on shutdown; this thread owns the event loop.
            stop.wait(max(0, remaining))
    finally:
        await monitor.close()


def run_plug_loop(config: Config, store: Store, stop: threading.Event) -> None:
    try:
        import kasa  # noqa: F401
    except ImportError:
        logger.error('Plug monitoring needs: pip install -e ".[plugs]"')
        return
    asyncio.run(_run(config, store, stop))
