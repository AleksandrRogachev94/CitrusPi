"""Sensor hub — reads BME280 + capacitive soil moisture.

Auto-falls back to realistic mock data when not running on a Pi,
so the dashboard works beautifully during development on any machine.
"""

from __future__ import annotations

import logging
import math
import random
import threading
import time
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

        # Simulate: warmer during lights-on, cooler at night
        base_c = 15.0 + 3.0 * math.sin(hour_angle)  # 12–18 °C
        temp_c = round(base_c + random.gauss(0, 0.3), 2)
        temp_f = round(temp_c * 9 / 5 + 32, 2)

        # Humidity inversely correlated with temperature
        humidity = round(65.0 - 8.0 * math.sin(hour_angle) + random.gauss(0, 1.5), 2)
        humidity = max(30.0, min(95.0, humidity))

        # Pressure — slow barometric drift
        pressure = round(1013.25 + 2.0 * math.sin(elapsed / 7200) + random.gauss(0, 0.2), 2)

        # Soil moisture — slow dry-down with occasional "watering" resets
        cycle = (elapsed % 86400) / 86400  # daily cycle
        soil = int(800 - 200 * cycle + random.gauss(0, 10))
        soil = max(300, min(900, soil))

        return {
            "temp_c": temp_c,
            "temp_f": temp_f,
            "humidity": humidity,
            "pressure": pressure,
            "soil_moisture": soil,
        }


# ── Real hardware drivers ────────────────────────────────────────────────────


class _BME280:
    """Minimal BME280 driver (smbus2)."""

    _REG_ID = 0xD0
    _REG_CTRL_HUM = 0xF2
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
        self._load_calibration()
        self._configure()

    def _rb(self, reg: int, n: int) -> list[int]:
        return self._bus.read_i2c_block_data(self._addr, reg, n)

    def _wb(self, reg: int, val: int) -> None:
        self._bus.write_byte_data(self._addr, reg, val)

    def _load_calibration(self) -> None:
        tp = self._rb(self._REG_CALIB_T_P, 26)
        h1 = self._rb(self._REG_DIG_H1, 1)
        h2 = self._rb(self._REG_CALIB_H, 7)

        def u16(b: list[int], i: int) -> int:
            return (b[i + 1] << 8) | b[i]

        def s16(b: list[int], i: int) -> int:
            v = u16(b, i)
            return v - 65536 if v >= 32768 else v

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
        c["H4"] = (h2[3] << 4) | (h2[4] & 0x0F)
        c["H5"] = (h2[5] << 4) | ((h2[4] >> 4) & 0x0F)
        c["H6"] = h2[6] if h2[6] < 128 else h2[6] - 256

    def _configure(self) -> None:
        self._wb(self._REG_CTRL_HUM, 0x01)
        self._wb(self._REG_CONFIG, 0x00)
        self._wb(self._REG_CTRL_MEAS, 0x25)

    def read(self) -> dict[str, float]:
        self._wb(self._REG_CTRL_MEAS, 0x25)
        time.sleep(0.05)
        raw = self._rb(self._REG_DATA, 8)
        raw_p = (raw[0] << 12) | (raw[1] << 4) | (raw[2] >> 4)
        raw_t = (raw[3] << 12) | (raw[4] << 4) | (raw[5] >> 4)
        raw_h = (raw[6] << 8) | raw[7]

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
            humidity = (raw_h - (c["H4"] * 64.0 + c["H5"] / 16384.0 * h)) * (
                c["H2"] / 65536.0 * (1.0 + c["H6"] / 67108864.0 * h * (1.0 + c["H3"] / 67108864.0 * h))
            )
            humidity *= 1.0 - c["H1"] * humidity / 524288.0
            humidity = max(0.0, min(humidity, 100.0))

        return {
            "temp_c": round(temp_c, 2),
            "pressure": round(pressure, 2),
            "humidity": round(humidity, 2),
        }


class _SoilMoisture:
    """Adafruit STEMMA / Seesaw capacitive soil sensor."""

    _TOUCH_BASE = 0x0F
    _TOUCH_OFFSET = 0x10

    def __init__(self, bus: Any, address: int) -> None:
        self._bus = bus
        self._addr = address

    def read_raw(self) -> int:
        """Read capacitive touch value taking median of 3 samples for glitch filtering."""
        samples: list[int] = []
        for _ in range(3):
            self._bus.write_i2c_block_data(self._addr, self._TOUCH_BASE, [self._TOUCH_OFFSET])
            time.sleep(0.005)
            d = self._bus.read_i2c_block_data(self._addr, self._TOUCH_BASE, 2)
            samples.append((d[0] << 8) | d[1])
        samples.sort()
        return samples[1]  # median of 3


# ── Public interface ─────────────────────────────────────────────────────────


class SensorHub:
    """Unified interface — real hardware on Pi, mock data everywhere else."""

    def __init__(self, config: Config) -> None:
        self._mock: _MockSensors | None = None
        self._bme: _BME280 | None = None
        self._soil: _SoilMoisture | None = None
        self._bus: Any = None
        self._lock = threading.Lock()

        if config.mock_sensors:
            logger.info("Mock sensors enabled via config")
            self._mock = _MockSensors()
            return

        try:
            import smbus2  # type: ignore[import-untyped]

            self._bus = smbus2.SMBus(1)
        except Exception as exc:
            logger.warning("I2C bus 1 unavailable (%s) — falling back to mock", exc)
            self._mock = _MockSensors()
            return

        try:
            self._bme = _BME280(self._bus, config.bme280_address)
            logger.info("BME280 initialised at 0x%02X", config.bme280_address)
        except Exception as exc:
            logger.warning("BME280 unavailable (%s)", exc)

        try:
            self._soil = _SoilMoisture(self._bus, config.soil_moisture_address)
            logger.info("Soil sensor initialised at 0x%02X", config.soil_moisture_address)
        except Exception as exc:
            logger.warning("Soil sensor unavailable (%s)", exc)

        # If nothing real is available on the I2C bus, fall back to mock
        if self._bme is None and self._soil is None:
            logger.info("No sensors detected on I2C bus — using mock sensor data")
            self._mock = _MockSensors()

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
                soil_moisture=raw.get("soil_moisture"),
            )

        with self._lock:
            temp_c: float | None = None
            humidity: float | None = None
            pressure: float | None = None
            soil_moisture: int | None = None

            if self._bme:
                try:
                    bme_data = self._bme.read()
                    temp_c = bme_data.get("temp_c")
                    humidity = bme_data.get("humidity")
                    pressure = bme_data.get("pressure")
                except Exception:
                    logger.exception("BME280 read error")

            if self._soil:
                try:
                    soil_moisture = self._soil.read_raw()
                except Exception:
                    logger.exception("Soil moisture read error")

            return Reading.create(
                temp_c=temp_c,
                humidity=humidity,
                pressure=pressure,
                soil_moisture=soil_moisture,
            )
