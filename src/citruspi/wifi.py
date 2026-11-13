"""Wi-Fi signal strength, read from the kernel's /proc/net/wireless."""

from __future__ import annotations

from pathlib import Path
from typing import Any

_PROC_WIRELESS = Path("/proc/net/wireless")

# (minimum dBm, bars, label) — checked top to bottom.
_LEVELS = ((-55, 4, "Excellent"), (-65, 3, "Good"), (-75, 2, "Fair"), (-85, 1, "Weak"))


def read_signal(path: Path = _PROC_WIRELESS) -> dict[str, Any] | None:
    """Return the first wireless interface's signal, or None when unavailable.

    None covers wired/dev machines (no file, macOS) and an interface that is
    down or reports no level.
    """
    try:
        lines = path.read_text().splitlines()[2:]
    except OSError:
        return None
    for line in lines:
        iface, _, rest = line.partition(":")
        fields = rest.split()
        if len(fields) < 3:
            continue
        try:
            dbm = int(float(fields[2]))  # "-71." — trailing dot means dBm
        except ValueError:
            continue
        if dbm >= 0:  # 0 = no data; positive = unscaled driver value, not dBm
            continue
        bars, label = next(
            ((b, name) for floor, b, name in _LEVELS if dbm >= floor), (0, "Very weak"),
        )
        return {"interface": iface.strip(), "dbm": dbm, "bars": bars, "label": label}
    return None
