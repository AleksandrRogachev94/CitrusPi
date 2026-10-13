"""SQLite storage for sensor readings.

One lightweight file, fully queryable, zero config.  The DB lives
inside ``data/citruspi.db`` by default.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class Store:
    """Thread-safe SQLite wrapper for telemetry data."""

    def __init__(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = data_dir / "citruspi.db"
        self._local = threading.local()
        self._init_tables()

    def _conn(self) -> sqlite3.Connection:
        """One connection per thread (SQLite requirement)."""
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(
                str(self._db_path), check_same_thread=False
            )
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_tables(self) -> None:
        self._conn().execute(
            """
            CREATE TABLE IF NOT EXISTS readings (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp  TEXT    NOT NULL,
                temp_c     REAL,
                temp_f     REAL,
                humidity   REAL,
                pressure   REAL,
                soil_moisture INTEGER
            )
            """
        )
        self._conn().execute(
            """
            CREATE INDEX IF NOT EXISTS idx_readings_ts
            ON readings (timestamp)
            """
        )
        self._conn().commit()

    # ── Writes ────────────────────────────────────────────────────────

    def insert(self, reading: dict[str, Any]) -> None:
        """Insert a sensor reading dict."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn().execute(
            """
            INSERT INTO readings (timestamp, temp_c, temp_f, humidity, pressure, soil_moisture)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                reading.get("temp_c"),
                reading.get("temp_f"),
                reading.get("humidity"),
                reading.get("pressure"),
                reading.get("soil_moisture"),
            ),
        )
        self._conn().commit()

    # ── Reads ─────────────────────────────────────────────────────────

    def latest(self) -> dict[str, Any] | None:
        """Most recent reading."""
        row = self._conn().execute(
            "SELECT * FROM readings ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def history(self, hours: int = 24) -> list[dict[str, Any]]:
        """All readings from the last *hours* hours."""
        rows = self._conn().execute(
            """
            SELECT * FROM readings
            WHERE timestamp >= datetime('now', ? || ' hours')
            ORDER BY timestamp ASC
            """,
            (f"-{hours}",),
        ).fetchall()
        return [dict(r) for r in rows]

    def daily_summary(self, date: str) -> dict[str, Any]:
        """Min/max/avg for a given YYYY-MM-DD date."""
        row = self._conn().execute(
            """
            SELECT
                MIN(temp_c) as temp_min,
                MAX(temp_c) as temp_max,
                AVG(temp_c) as temp_avg,
                MIN(humidity) as hum_min,
                MAX(humidity) as hum_max,
                AVG(humidity) as hum_avg,
                COUNT(*) as sample_count
            FROM readings
            WHERE date(timestamp) = ?
            """,
            (date,),
        ).fetchone()
        return dict(row) if row else {}
