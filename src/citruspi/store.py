"""SQLite storage with WAL mode, index optimization, and versioned migrations.

Database management:
- Automated versioned schema migrations tracked in ``schema_migrations``.
- WAL journal mode for concurrent thread reads and writes without blocking.
- Optimized B-tree index on ``timestamp`` for fast 24-hour range queries.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from citruspi.models import Reading

logger = logging.getLogger(__name__)

# ── Migration Definitions ─────────────────────────────────────────────────────

MIGRATIONS: list[tuple[int, str, list[str]]] = [
    (
        1,
        "initial_readings_table",
        [
            """
            CREATE TABLE IF NOT EXISTS readings (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp     TEXT    NOT NULL,
                temp_c        REAL,
                temp_f        REAL,
                humidity      REAL,
                pressure      REAL,
                soil_moisture INTEGER
            );
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_readings_timestamp
            ON readings (timestamp);
            """,
        ],
    ),
    (
        2,
        "add_root_temp_and_light_columns",
        [
            "ALTER TABLE readings ADD COLUMN root_temp_c REAL;",
            "ALTER TABLE readings ADD COLUMN root_temp_f REAL;",
            "ALTER TABLE readings ADD COLUMN light_lux REAL;",
        ],
    ),
    (
        3,
        "add_vpd_kpa_column",
        [
            "ALTER TABLE readings ADD COLUMN vpd_kpa REAL;",
        ],
    ),
]


class Store:
    """Thread-safe, WAL-enabled SQLite telemetry store with migrations."""

    def __init__(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = data_dir / "citruspi.db"
        self._local = threading.local()
        self._run_migrations()

    def _get_conn(self) -> sqlite3.Connection:
        """Create or reuse connection for the current thread with WAL pragmas."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(
                str(self._db_path),
                check_same_thread=False,
                timeout=5.0,
            )
            conn.row_factory = sqlite3.Row
            # Best practice pragmas for concurrent embedded applications:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA busy_timeout = 5000;")
            self._local.conn = conn
        return self._local.conn

    def _run_migrations(self) -> None:
        """Execute any pending schema migrations inside a single transaction."""
        conn = self._get_conn()
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version    INTEGER PRIMARY KEY,
                    name       TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                );
                """
            )

            # Get applied versions
            cursor = conn.execute("SELECT version FROM schema_migrations")
            applied = {row[0] for row in cursor.fetchall()}

            for version, name, statements in MIGRATIONS:
                if version in applied:
                    continue

                logger.info("Applying DB migration %d: %s", version, name)
                for sql in statements:
                    try:
                        conn.execute(sql)
                    except sqlite3.OperationalError as exc:
                        # Handle duplicate column on alter if previously partially run
                        if "duplicate column name" in str(exc).lower():
                            logger.debug("Column already exists, continuing: %s", exc)
                        else:
                            raise

                now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?);",
                    (version, name, now_utc),
                )
                logger.info("✓ Migration %d (%s) applied successfully", version, name)

    # ── Write Operations ──────────────────────────────────────────────────────

    def insert(self, reading: Reading | dict[str, Any]) -> None:
        """Insert a sensor reading."""
        if isinstance(reading, dict):
            # Accept dict for backward compatibility
            ts = reading.get("timestamp") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            temp_c = reading.get("temp_c")
            temp_f = reading.get("temp_f") or (round(temp_c * 9 / 5 + 32, 2) if temp_c is not None else None)
            humidity = reading.get("humidity")
            pressure = reading.get("pressure")
            soil = reading.get("soil_moisture")
            root_c = reading.get("root_temp_c")
            root_f = reading.get("root_temp_f")
            light = reading.get("light_lux")
            vpd = reading.get("vpd_kpa")
        else:
            ts = reading.timestamp
            temp_c = reading.temp_c
            temp_f = reading.temp_f
            humidity = reading.humidity
            pressure = reading.pressure
            soil = reading.soil_moisture
            root_c = reading.root_temp_c
            root_f = reading.root_temp_f
            light = reading.light_lux
            vpd = reading.vpd_kpa

        conn = self._get_conn()
        with conn:
            conn.execute(
                """
                INSERT INTO readings (
                    timestamp, temp_c, temp_f, humidity, pressure, soil_moisture,
                    root_temp_c, root_temp_f, light_lux, vpd_kpa
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (ts, temp_c, temp_f, humidity, pressure, soil, root_c, root_f, light, vpd),
            )

    # ── Read Operations ───────────────────────────────────────────────────────

    def latest(self) -> dict[str, Any] | None:
        """Return the most recent reading."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM readings ORDER BY id DESC LIMIT 1;"
        ).fetchone()
        return dict(row) if row else None

    def history(self, hours: int = 24, max_points: int = 1000) -> list[dict[str, Any]]:
        """Return readings from the last *hours* hours using timestamp index range scan.

        Automatically downsamples longer periods only if row count exceeds max_points,
        ensuring smooth rendering on Pi Zero 2W without starving small datasets.
        """
        conn = self._get_conn()
        cutoff = f"-{hours}"

        count_row = conn.execute(
            """
            SELECT COUNT(*) FROM readings
            WHERE timestamp >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ? || ' hours');
            """,
            (cutoff,),
        ).fetchone()
        total_rows = count_row[0] if count_row else 0

        if total_rows <= max_points:
            rows = conn.execute(
                """
                SELECT * FROM readings
                WHERE timestamp >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ? || ' hours')
                ORDER BY timestamp ASC;
                """,
                (cutoff,),
            ).fetchall()
        else:
            stride = max(2, total_rows // max_points)
            rows = conn.execute(
                """
                WITH numbered AS (
                    SELECT *, ROW_NUMBER() OVER (ORDER BY timestamp ASC) AS rn
                    FROM readings
                    WHERE timestamp >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ? || ' hours')
                )
                SELECT * FROM numbered
                WHERE rn % ? = 1
                ORDER BY timestamp ASC;
                """,
                (cutoff, stride),
            ).fetchall()
        return [dict(r) for r in rows]

    def daily_summary(self, date_str: str) -> dict[str, Any]:
        """Min/max/avg for a given YYYY-MM-DD date using index range bounds."""
        conn = self._get_conn()
        # Range bounds use index directly instead of full-table-scan date(timestamp)
        start_ts = f"{date_str}T00:00:00Z"
        end_ts = f"{date_str}T23:59:59Z"

        row = conn.execute(
            """
            SELECT
                MIN(temp_c) AS temp_min,
                MAX(temp_c) AS temp_max,
                ROUND(AVG(temp_c), 2) AS temp_avg,
                MIN(humidity) AS hum_min,
                MAX(humidity) AS hum_max,
                ROUND(AVG(humidity), 2) AS hum_avg,
                MIN(soil_moisture) AS soil_min,
                MAX(soil_moisture) AS soil_max,
                COUNT(*) AS sample_count
            FROM readings
            WHERE timestamp >= ? AND timestamp <= ?;
            """,
            (start_ts, end_ts),
        ).fetchone()
        return dict(row) if row else {}

    def get_applied_migrations(self) -> list[dict[str, Any]]:
        """Return list of applied migrations for inspection."""
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM schema_migrations ORDER BY version ASC;").fetchall()
        return [dict(r) for r in rows]
