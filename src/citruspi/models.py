"""Domain models and data schemas for CitrusPi."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class Reading:
    """A single sensor reading measurement."""

    timestamp: str  # ISO8601 UTC string: YYYY-MM-DDTHH:MM:SSZ
    temp_c: float | None = None
    temp_f: float | None = None
    humidity: float | None = None
    pressure: float | None = None
    soil_moisture: int | None = None
    root_temp_c: float | None = None  # Available if DS18B20 probe attached
    root_temp_f: float | None = None
    light_lux: float | None = None    # Available if BH1750 attached
    vpd_kpa: float | None = None      # Vapor Pressure Deficit in kPa
    id: int | None = None

    @classmethod
    def create(
        cls,
        *,
        temp_c: float | None = None,
        temp_f: float | None = None,
        humidity: float | None = None,
        pressure: float | None = None,
        soil_moisture: int | None = None,
        root_temp_c: float | None = None,
        root_temp_f: float | None = None,
        light_lux: float | None = None,
        vpd_kpa: float | None = None,
        timestamp: str | None = None,
    ) -> Reading:
        """Create a reading, auto-computing temp_f, root_temp_f, VPD, and UTC timestamp."""
        ts = timestamp or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        calc_temp_f = temp_f
        if calc_temp_f is None and temp_c is not None:
            calc_temp_f = round(temp_c * 9.0 / 5.0 + 32.0, 2)
        calc_root_f = root_temp_f
        if calc_root_f is None and root_temp_c is not None:
            calc_root_f = round(root_temp_c * 9.0 / 5.0 + 32.0, 2)

        # Compute Vapor Pressure Deficit (VPD in kPa) using Magnus-Tetens formula:
        # e_s = 0.61078 * exp((17.27 * T_c) / (T_c + 237.3))
        # VPD = e_s * (1 - RH / 100)
        calc_vpd = vpd_kpa
        if calc_vpd is None and temp_c is not None and humidity is not None:
            try:
                es = 0.61078 * math.exp((17.27 * temp_c) / (temp_c + 237.3))
                calc_vpd = round(es * (1.0 - (humidity / 100.0)), 2)
            except Exception:
                calc_vpd = None

        return cls(
            timestamp=ts,
            temp_c=round(temp_c, 2) if temp_c is not None else None,
            temp_f=calc_temp_f,
            humidity=round(humidity, 2) if humidity is not None else None,
            pressure=round(pressure, 2) if pressure is not None else None,
            soil_moisture=soil_moisture,
            root_temp_c=round(root_temp_c, 2) if root_temp_c is not None else None,
            root_temp_f=calc_root_f,
            light_lux=round(light_lux, 2) if light_lux is not None else None,
            vpd_kpa=calc_vpd,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert reading to dictionary, omitting null ID."""
        d = asdict(self)
        if d.get("id") is None:
            d.pop("id", None)
        return d
