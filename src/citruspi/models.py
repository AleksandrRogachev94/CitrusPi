"""Domain models and data schemas for CitrusPi."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
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
    id: int | None = None

    @classmethod
    def create(
        cls,
        *,
        temp_c: float | None = None,
        humidity: float | None = None,
        pressure: float | None = None,
        soil_moisture: int | None = None,
        root_temp_c: float | None = None,
        light_lux: float | None = None,
        timestamp: str | None = None,
    ) -> Reading:
        """Create a reading, auto-computing temp_f and UTC timestamp if omitted."""
        ts = timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        temp_f = round(temp_c * 9.0 / 5.0 + 32.0, 2) if temp_c is not None else None
        root_temp_f = (
            round(root_temp_c * 9.0 / 5.0 + 32.0, 2) if root_temp_c is not None else None
        )
        return cls(
            timestamp=ts,
            temp_c=round(temp_c, 2) if temp_c is not None else None,
            temp_f=temp_f,
            humidity=round(humidity, 2) if humidity is not None else None,
            pressure=round(pressure, 2) if pressure is not None else None,
            soil_moisture=soil_moisture,
            root_temp_c=round(root_temp_c, 2) if root_temp_c is not None else None,
            root_temp_f=root_temp_f,
            light_lux=round(light_lux, 2) if light_lux is not None else None,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert reading to dictionary, omitting null ID."""
        d = asdict(self)
        if d.get("id") is None:
            d.pop("id", None)
        return d
