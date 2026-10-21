"""Sensor hub — reads BME280 + DS18B20 root-zone temperature probe.

When mock_sensors is enabled in Config, realistic mock data is generated
for local UI development. Real mode strictly queries physical hardware.
"""

from __future__ import annotations

import contextlib
import logging
import math
import random
import threading
import time
from pathlib import Path
from typing import Any

from citruspi.config import Config
from citruspi.models import Reading

logger = logging.getLogger(__name__)


# ── Mock data generator ──────────────────────────────────────────────────────

class _MockSensors:
    """Generates realistic grow-tent data for UI development."""

    _start = time.time()

    def read(self) -> dict[str, Any]:
        elapsed = time.time() - self._start
        hour_angle = (elapsed / 3600) * (2 * math.pi / 24)

        # Simulate canopy: warmer during lights-on, cooler at night (12-18 °C)
        base_c = 15.0 + 3.0 * math.sin(hour_angle)
        temp_c = round(base_c + random.gauss(0, 0.3), 2)
        temp_f = round(temp_c * 9 / 5 + 32, 2)

        # Humidity inversely correlated with temperature
        humidity = round(65.0 - 8.0 * math.sin(hour_angle) + random.gauss(0, 1.5), 2)
        humidity = max(30.0, min(95.0, humidity))

        # Pressure — slow barometric drift
        pressure = round(1013.25 + 2.0 * math.sin(elapsed / 7200) + random.gauss(0, 0.2), 2)

        # Root zone temperature — lagged and damped by substrate thermal mass
        # Damped amplitude (1.2°C vs 3.0°C) and ~3 hour thermal phase lag (3 * pi / 12)
        root_hour_angle = hour_angle - (3.0 * math.pi / 12)
        root_base_c = 14.5 + 1.2 * math.sin(root_hour_angle)
        root_temp_c = round(root_base_c + random.gauss(0, 0.1), 2)
        root_temp_f = round(root_temp_c * 9 / 5 + 32, 2)

        return {
            "temp_c": temp_c,
            "temp_f": temp_f,
            "humidity": humidity,
            "pressure": pressure,
            "root_temp_c": root_temp_c,
            "root_temp_f": root_temp_f,
        }


# ── Real hardware drivers ────────────────────────────────────────────────────


class _BME280:
    """Minimal BME280 driver (smbus2)."""

    _REG_ID = 0xD0
    _REG_RESET = 0xE0
    _REG_CTRL_HUM = 0xF2
    _REG_STATUS = 0xF3
    _REG_CTRL_MEAS = 0xF4
    _REG_CONFIG = 0xF5
    _REG_DATA = 0xF7
    _REG_CALIB_T_P = 0x88
    _REG_CALIB_H = 0xE1
    _REG_DIG_H1 = 0xA1

    def __init__(self, bus: Any, address: int) -> None:
        self._bus = bus
        self._addr = address
        self._cal: dict[str, int] = {}
        chip_id = self._rb(self._REG_ID, 1)[0]
        if chip_id != 0x60:
            raise ValueError(f"Expected BME280 chip ID 0x60, got 0x{chip_id:02X}")
        # Clear settings left by a previous process and wait for calibration NVM.
        self._wb(self._REG_RESET, 0xB6)
        time.sleep(0.003)
        self._wait_ready()
        self._load_calibration()
        self._configure()

    def _rb(self, reg: int, n: int) -> list[int]:
        return self._bus.read_i2c_block_data(self._addr, reg, n)

    def _wb(self, reg: int, val: int) -> None:
        self._bus.write_byte_data(self._addr, reg, val)

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + 0.25
        # Bit 3: conversion running; bit 0: calibration NVM copy running.
        while self._rb(self._REG_STATUS, 1)[0] & 0x09:
            if time.monotonic() >= deadline:
                raise TimeoutError("BME280 did not finish conversion or calibration copy")
            time.sleep(0.002)

    def _load_calibration(self) -> None:
        tp = self._rb(self._REG_CALIB_T_P, 26)
        h1 = self._rb(self._REG_DIG_H1, 1)
        h2 = self._rb(self._REG_CALIB_H, 7)

        def u16(b: list[int], i: int) -> int:
            return (b[i + 1] << 8) | b[i]

        def s16(b: list[int], i: int) -> int:
            v = u16(b, i)
            return v - 65536 if v >= 32768 else v

        def s12(v: int) -> int:
            return v - 4096 if v >= 2048 else v

        c = self._cal
        c["T1"] = u16(tp, 0)
        c["T2"] = s16(tp, 2)
        c["T3"] = s16(tp, 4)
        c["P1"] = u16(tp, 6)
        for i in range(2, 10):
            c[f"P{i}"] = s16(tp, 6 + (i - 1) * 2)
        c["H1"] = h1[0]
        c["H2"] = s16(h2, 0)
        c["H3"] = h2[2]
        c["H4"] = s12((h2[3] << 4) | (h2[4] & 0x0F))
        c["H5"] = s12((h2[5] << 4) | ((h2[4] >> 4) & 0x0F))
        c["H6"] = h2[6] if h2[6] < 128 else h2[6] - 256

    def _configure(self) -> None:
        self._wb(self._REG_CTRL_HUM, 0x01)
        self._wb(self._REG_CONFIG, 0x00)
        # Latch humidity settings with temperature/pressure x1, staying asleep.
        # Only read() starts a conversion.
        self._wb(self._REG_CTRL_MEAS, 0x24)

    def read(self) -> dict[str, float]:
        self._wb(self._REG_CTRL_MEAS, 0x25)
        # Allow conversion to start and exceed the x1 conversion time before
        # polling; an immediate status read can still report the previous idle state.
        time.sleep(0.05)
        self._wait_ready()
        raw = self._rb(self._REG_DATA, 8)
        raw_p = (raw[0] << 12) | (raw[1] << 4) | (raw[2] >> 4)
        raw_t = (raw[3] << 12) | (raw[4] << 4) | (raw[5] >> 4)
        raw_h = (raw[6] << 8) | raw[7]
        if raw_t == 0x80000 or raw_p == 0x80000 or raw_h == 0x8000:
            raise ValueError(
                "BME280 returned reset/skipped measurement data "
                f"(temperature=0x{raw_t:05X}, pressure=0x{raw_p:05X}, "
                f"humidity=0x{raw_h:04X})"
            )

        c = self._cal
        v1 = (raw_t / 16384.0 - c["T1"] / 1024.0) * c["T2"]
        v2 = ((raw_t / 131072.0 - c["T1"] / 8192.0) ** 2) * c["T3"]
        t_fine = v1 + v2
        temp_c = t_fine / 5120.0

        v1 = t_fine / 2.0 - 64000.0
        v2 = v1 * v1 * c["P6"] / 32768.0 + v1 * c["P5"] * 2.0
        v2 = v2 / 4.0 + c["P4"] * 65536.0
        v1 = (c["P3"] * v1 * v1 / 524288.0 + c["P2"] * v1) / 524288.0
        v1 = (1.0 + v1 / 32768.0) * c["P1"]
        pressure = 0.0
        if v1 != 0:
            pressure = 1048576.0 - raw_p
            pressure = ((pressure - v2 / 4096.0) * 6250.0) / v1
            v1 = c["P9"] * pressure * pressure / 2147483648.0
            v2 = pressure * c["P8"] / 32768.0
            pressure = (pressure + (v1 + v2 + c["P7"]) / 16.0) / 100.0

        h = t_fine - 76800.0
        humidity = 0.0
        if h != 0:
            h_scale = 1.0 + (c["H3"] / 67108864.0) * h
            h_intermediate = 1.0 + (c["H6"] / 67108864.0) * h * h_scale
            humidity = (raw_h - (c["H4"] * 64.0 + c["H5"] / 16384.0 * h)) * (
                c["H2"] / 65536.0 * h_intermediate
            )
            humidity *= 1.0 - c["H1"] * humidity / 524288.0
            humidity = max(0.0, min(humidity, 100.0))

        return {
            "temp_c": round(temp_c, 2),
            "pressure": round(pressure, 2),
            "humidity": round(humidity, 2),
        }


class _DS18B20:
    """DS18B20 1-Wire temperature probe driver via Linux w1 sysfs."""

    def __init__(self, w1_dir: Path | str = Path("/sys/bus/w1/devices")) -> None:
        self._w1_dir = Path(w1_dir)
        self._device_file: Path | None = None

    def _find_device(self) -> Path | None:
        try:
            if not self._w1_dir.is_dir():
                return None
            devices = sorted(self._w1_dir.glob("28-*/w1_slave"))
            if devices:
                return devices[0]
        except OSError:
            return None
        return None

    def read(self) -> float | None:
        """Read temperature in Celsius from 1-Wire sysfs interface.

        Returns float Celsius, or None on missing probe, CRC error,
        85000 sentinel, or parse/read failure.
        """
        try:
            if self._device_file is None:
                self._device_file = self._find_device()
                if self._device_file is None:
                    return None

            content = self._device_file.read_text()
            lines = content.strip().splitlines()
            if len(lines) < 2:
                raise ValueError("Malformed w1_slave: less than 2 lines")

            # Line 1 must end with YES
            if not lines[0].strip().endswith("YES"):
                if lines[0].strip().endswith("NO"):
                    # CRC error on valid w1 device
                    return None
                raise ValueError("Malformed w1_slave: line 1 does not end with YES or NO")

            # Parse t=<millidegrees> from line 2
            if "t=" not in lines[1]:
                raise ValueError("Malformed w1_slave: missing 't=' token")

            t_str = lines[1].split("t=")[-1].strip()
            t_raw = int(t_str)

            # 85000 is power-on sentinel (conversion incomplete)
            if t_raw == 85000:
                return None

            return round(t_raw / 1000.0, 2)
        except (OSError, ValueError):
            self._device_file = None
            return None


# ── Public interface ─────────────────────────────────────────────────────────


class SensorHub:
    """Unified interface — real hardware on Pi, mock data only when explicitly enabled."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._mock: _MockSensors | None = None
        self._bme: _BME280 | None = None
        self._ds18b20: _DS18B20 | None = None
        self._bus: Any = None
        self._last_bme_init_attempt: float = 0.0
        self._lock = threading.Lock()

        if config.mock_sensors:
            logger.info("Mock sensors enabled via config")
            self._mock = _MockSensors()
            return

        # STRICT MOCK RULE: When config.mock_sensors is False, NEVER fall back to mock data.
        self._try_init_bme()

        # Initialize DS18B20 1-Wire temperature probe
        try:
            self._ds18b20 = _DS18B20(config.w1_devices_dir)
            logger.info("DS18B20 probe configured at %s", config.w1_devices_dir)
        except Exception as exc:
            logger.warning("DS18B20 unavailable (%s)", exc)

    def _try_init_bme(self) -> None:
        """Attempt to initialize or re-initialize BME280 on I2C bus."""
        now = time.time()
        if self._last_bme_init_attempt > 0 and (now - self._last_bme_init_attempt) < 30.0:
            return
        self._last_bme_init_attempt = now

        if self._bus is None:
            try:
                import smbus2  # type: ignore[import-untyped]

                self._bus = smbus2.SMBus(1)
            except Exception as exc:
                logger.warning("I2C bus 1 unavailable (%s)", exc)
                self._bus = None
                return

        try:
            self._bme = _BME280(self._bus, self.config.bme280_address)
            logger.info("BME280 initialised at 0x%02X", self.config.bme280_address)
        except Exception as exc:
            logger.warning("BME280 unavailable (%s)", exc)
            self._bme = None

    @property
    def is_mock(self) -> bool:
        return self._mock is not None

    def read(self) -> Reading:
        """Read all active sensors and return a validated Reading model."""
        if self._mock:
            raw = self._mock.read()
            return Reading.create(
                temp_c=raw.get("temp_c"),
                humidity=raw.get("humidity"),
                pressure=raw.get("pressure"),
                root_temp_c=raw.get("root_temp_c"),
            )

        with self._lock:
            temp_c: float | None = None
            humidity: float | None = None
            pressure: float | None = None
            root_temp_c: float | None = None

            # Self-healing retry if BME280 was not ready at boot or had a bus glitch
            if self._bme is None:
                self._try_init_bme()

            if self._bme:
                try:
                    bme_data = self._bme.read()
                    temp_c = bme_data.get("temp_c")
                    humidity = bme_data.get("humidity")
                    pressure = bme_data.get("pressure")
                except Exception:
                    logger.exception("BME280 read error")
                    self._bme = None
                    if self._bus is not None:
                        with contextlib.suppress(Exception):
                            self._bus.close()
                        self._bus = None

            if self._ds18b20:
                try:
                    root_temp_c = self._ds18b20.read()
                except Exception:
                    logger.exception("DS18B20 read error")

            return Reading.create(
                temp_c=temp_c,
                humidity=humidity,
                pressure=pressure,
                root_temp_c=root_temp_c,
            )
