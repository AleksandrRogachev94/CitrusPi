"""SQLite storage with WAL mode, index optimization, and versioned migrations.

Database management:
- Automated versioned schema migrations tracked in ``schema_migrations``.
- WAL journal mode for concurrent thread reads and writes without blocking.
- Optimized B-tree index on ``timestamp`` for fast 24-hour range queries.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from itertools import chain, islice
from math import ceil
from pathlib import Path
from typing import Any

from citruspi.config import ElectricityConfig
from citruspi.electricity import interval_cost
from citruspi.models import PowerReading, Reading

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
    (
        4,
        "add_daily_reports_table",
        [
            """
            CREATE TABLE IF NOT EXISTS daily_reports (
                date        TEXT PRIMARY KEY,
                created_at  TEXT NOT NULL,
                status      TEXT NOT NULL,
                summary     TEXT NOT NULL,
                leaf_drop   INTEGER NOT NULL DEFAULT 0
            );
            """,
        ],
    ),
    (
        5,
        "add_structured_tree_observations",
        [
            "ALTER TABLE daily_reports ADD COLUMN trees TEXT NOT NULL DEFAULT '[]';",
            "ALTER TABLE daily_reports ADD COLUMN reference_dates TEXT;",
        ],
    ),
    (
        6,
        "add_power_readings",
        [
            """CREATE TABLE IF NOT EXISTS power_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plug_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                relay_on INTEGER,
                power_w REAL,
                active INTEGER,
                energy_today_kwh REAL,
                status TEXT NOT NULL
            );""",
            """CREATE INDEX IF NOT EXISTS idx_power_plug_timestamp
                ON power_readings (plug_id, timestamp);""",
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
        """Execute any pending schema migrations."""
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

                now_utc = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?);",
                    (version, name, now_utc),
                )
                logger.info("✓ Migration %d (%s) applied successfully", version, name)

    # ── Write Operations ──────────────────────────────────────────────────────

    def insert(self, reading: Reading) -> None:
        """Insert a sensor reading."""
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
                (
                    reading.timestamp,
                    reading.temp_c,
                    reading.temp_f,
                    reading.humidity,
                    reading.pressure,
                    reading.soil_moisture,
                    reading.root_temp_c,
                    reading.root_temp_f,
                    reading.light_lux,
                    reading.vpd_kpa,
                ),
            )

    # ── Read Operations ───────────────────────────────────────────────────────

    def insert_power(self, reading: PowerReading) -> None:
        conn = self._get_conn()
        with conn:
            conn.execute(
                """INSERT INTO power_readings
                   (plug_id, timestamp, relay_on, power_w, active, energy_today_kwh, status)
                   VALUES (:plug_id, :timestamp, :relay_on, :power_w, :active,
                           :energy_today_kwh, :status)""",
                asdict(reading),
            )

    @staticmethod
    def _decode_power(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        for key in ("relay_on", "active"):
            if result[key] is not None:
                result[key] = bool(result[key])
        return result

    def latest_power(self, plug_id: str) -> dict[str, Any] | None:
        row = self._get_conn().execute(
            """SELECT * FROM power_readings WHERE plug_id = ?
               ORDER BY timestamp DESC, id DESC LIMIT 1""", (plug_id,),
        ).fetchone()
        return self._decode_power(row) if row else None

    def power_history(
        self, plug_id: str, hours: int = 24, max_points: int = 1000,
        max_gap_sec: int = 120,
    ) -> list[dict[str, Any]]:
        """Bound chart payloads while retaining endpoints, power extrema and missing data."""
        if max_points < 5:
            raise ValueError("max_points must be at least 5")
        cursor = self._get_conn().execute(
            """SELECT *, COUNT(*) OVER () AS _total FROM power_readings
               WHERE plug_id = ?
                 AND timestamp >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)
               ORDER BY timestamp, id""", (plug_id, f"-{hours} hours"),
        )
        first = cursor.fetchone()
        if first is None:
            return []
        # Insert gap markers before sampling, so sparse chart points are not mistaken
        # for collection outages. At most 2N rows including these markers.
        bucket_size = ceil(first["_total"] * 2 / max(1, max_points // 5))

        def with_gaps():
            previous = None
            for raw in chain([first], cursor):
                row = self._decode_power(raw)
                row.pop("_total")
                timestamp = datetime.fromisoformat(row["timestamp"])
                if previous is not None and (timestamp - previous).total_seconds() > max_gap_sec:
                    yield {**row, "timestamp": (previous + timedelta(seconds=1)).strftime(
                        '%Y-%m-%dT%H:%M:%SZ'), "power_w": None, "active": None}
                yield row
                previous = timestamp

        rows = with_gaps()
        sampled = []
        while bucket := list(islice(rows, bucket_size)):
            indices = {0, len(bucket) - 1}
            valid = [i for i, row in enumerate(bucket) if row["power_w"] is not None]
            if valid:
                indices.update((min(valid, key=lambda i: bucket[i]["power_w"]),
                                max(valid, key=lambda i: bucket[i]["power_w"])))
            missing = next((i for i, row in enumerate(bucket) if row["power_w"] is None), None)
            if missing is not None:
                indices.add(missing)
            for index in sorted(indices):
                sampled.append(bucket[index])
        return sampled

    def power_activity(
        self, plug_id: str, hours: int, max_gap_sec: int, now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Merge observed states from raw readings, independently of chart downsampling.

        Like runtime estimates, an interval uses the preceding state and requires
        known endpoints. Do not bridge outages or extend past the last observation.
        """
        end = now or datetime.now(UTC)
        start = end - timedelta(hours=hours)
        rows = self._get_conn().execute(
            """SELECT timestamp, active FROM power_readings
               WHERE plug_id = ? AND timestamp >= ? AND timestamp <= ?
               ORDER BY timestamp, id""",
            (plug_id, (start - timedelta(seconds=max_gap_sec)).strftime('%Y-%m-%dT%H:%M:%SZ'),
             end.strftime('%Y-%m-%dT%H:%M:%SZ')),
        )
        intervals: list[dict[str, Any]] = []
        previous = None
        for row in rows:
            timestamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
            if previous is not None:
                prev_time, state = previous
                gap = (timestamp - prev_time).total_seconds()
                if (0 < gap <= max_gap_sec and state is not None and row["active"] is not None
                        and timestamp > start):
                    left = max(start, prev_time).strftime('%Y-%m-%dT%H:%M:%SZ')
                    right = timestamp.strftime('%Y-%m-%dT%H:%M:%SZ')
                    if intervals and intervals[-1]["end"] == left and (
                        intervals[-1]["active"] == bool(state)
                    ):
                        intervals[-1]["end"] = right
                    else:
                        intervals.append({"start": left, "end": right, "active": bool(state)})
            previous = timestamp, row["active"]
        return intervals

    def first_power_timestamp(self, plug_id: str) -> str | None:
        row = self._get_conn().execute(
            "SELECT MIN(timestamp) FROM power_readings WHERE plug_id = ?", (plug_id,),
        ).fetchone()
        return row[0] if row else None

    def power_summary(
        self, plug_id: str, poll_interval: int, now: datetime | None = None,
        *, electricity: ElectricityConfig | None = None, hours: int = 24,
    ) -> dict[str, Any]:
        """Estimate energy/runtime/cost from raw samples, excluding gaps.

        Device 'today' counters are stored separately: never sum them, since they
        reset at the device's local midnight. Do not extrapolate after the last sample.
        The selected window is priced using the current configured rates.
        """
        end = now or datetime.now(UTC)
        start = end - timedelta(hours=hours)
        max_gap = poll_interval * 2
        cursor = self._get_conn().execute(
            """SELECT timestamp, power_w, active FROM power_readings
               WHERE plug_id = ? AND timestamp >= ? AND timestamp <= ?
               ORDER BY timestamp, id""",
            (plug_id, (start - timedelta(seconds=max_gap)).strftime('%Y-%m-%dT%H:%M:%SZ'),
             end.strftime('%Y-%m-%dT%H:%M:%SZ')),
        )
        energy = observed = runtime = activity_observed = cost = 0.0
        previous = None
        for row in cursor:
            time = datetime.fromisoformat(row["timestamp"])
            if previous is not None:
                prev_time, prev = previous
                gap = (time - prev_time).total_seconds()
                left = max(prev_time, start)
                seconds = (time - left).total_seconds()
                if 0 < gap <= max_gap and seconds > 0:
                    if prev["power_w"] is not None and row["power_w"] is not None:
                        left_watts = prev["power_w"] + (
                            row["power_w"] - prev["power_w"]
                        ) * (left - prev_time).total_seconds() / gap
                        energy += (left_watts + row["power_w"]) / 2 * seconds / 3_600_000
                        if electricity is not None:
                            cost += interval_cost(
                                left, time, left_watts, row["power_w"], electricity,
                            )
                        observed += seconds
                    if prev["active"] is not None and row["active"] is not None:
                        activity_observed += seconds
                        runtime += seconds if prev["active"] else 0
            previous = time, row
        span = (end - start).total_seconds()
        return {
            "estimated_kwh": round(energy, 4) if observed else None,
            "estimated_cost_usd": round(cost, 4) if observed and electricity else None,
            "runtime_hours": round(runtime / 3600, 2) if activity_observed else None,
            "duty_cycle_pct": round(runtime / activity_observed * 100, 1)
                if activity_observed else None,
            "coverage_pct": round(observed / span * 100, 1) if span > 0 else 0,
        }

    def latest(self) -> dict[str, Any] | None:
        """Return the most recent reading."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM readings ORDER BY id DESC LIMIT 1;"
        ).fetchone()
        return dict(row) if row else None

    def history(self, hours: int = 24, max_points: int = 1000) -> list[dict[str, Any]]:
        """Return readings from the last *hours* hours using timestamp index range scan.

        Keep bucket endpoints, extrema for each charted metric, and a missing-value
        sample per metric. Stream the query so a month of data needn't fit in Python memory.
        max_points must be at least 2 so the first and last samples can be retained.
        """
        conn = self._get_conn()
        if max_points < 2:
            raise ValueError("max_points must be at least 2")
        cursor = conn.execute(
            """
            SELECT *, COUNT(*) OVER () AS _total FROM readings
            WHERE timestamp >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ? || ' hours')
            ORDER BY timestamp ASC, id ASC;
            """,
            (f"-{hours}",),
        )
        first = cursor.fetchone()
        if first is None:
            return []
        columns = [key for key in dict(first) if key != "_total"]
        rows = ({key: row[key] for key in columns} for row in chain([first], cursor))
        if first["_total"] <= max_points:
            return list(rows)

        # At most 14 distinct samples per bucket: 2 endpoints + 3 per metric.
        bucket_size = ceil(first["_total"] / max(1, max_points // 14))
        sampled: list[dict[str, Any]] = []
        while bucket := list(islice(rows, bucket_size)):
            indices = {0, len(bucket) - 1}
            for field in ("temp_f", "root_temp_f", "humidity", "vpd_kpa"):
                valid = [i for i, row in enumerate(bucket) if row[field] is not None]
                candidates: list[int] = []
                if valid:
                    candidates.extend((
                        min(valid, key=lambda i: bucket[i][field]),
                        max(valid, key=lambda i: bucket[i][field]),
                    ))
                missing = next((i for i, row in enumerate(bucket) if row[field] is None), None)
                if missing is not None:
                    candidates.append(missing)
                for index in candidates:
                    if len(indices) < max_points:
                        indices.add(index)
            sampled.extend(bucket[i] for i in sorted(indices))
        return sampled

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
                MIN(temp_f) AS temp_min_f,
                MAX(temp_f) AS temp_max_f,
                MIN(root_temp_f) AS root_temp_min_f,
                MAX(root_temp_f) AS root_temp_max_f,
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

    def summary_last_hours(
        self, hours: int = 24, before_ts: str | None = None
    ) -> dict[str, Any]:
        """Min/max/avg for the last *hours* hours before *before_ts* (or now)."""
        conn = self._get_conn()
        cutoff = f"-{hours}"
        if before_ts:
            query = """
                SELECT
                    MIN(temp_c) AS temp_min,
                    MAX(temp_c) AS temp_max,
                    ROUND(AVG(temp_c), 2) AS temp_avg,
                    MIN(temp_f) AS temp_min_f,
                    MAX(temp_f) AS temp_max_f,
                    MIN(root_temp_f) AS root_temp_min_f,
                    MAX(root_temp_f) AS root_temp_max_f,
                    MIN(humidity) AS hum_min,
                    MAX(humidity) AS hum_max,
                    ROUND(AVG(humidity), 2) AS hum_avg,
                    MIN(soil_moisture) AS soil_min,
                    MAX(soil_moisture) AS soil_max,
                    COUNT(*) AS sample_count
                FROM readings
                WHERE timestamp >= strftime('%Y-%m-%dT%H:%M:%SZ', ?, ? || ' hours')
                  AND timestamp <= ?;
            """
            row = conn.execute(query, (before_ts, cutoff, before_ts)).fetchone()
        else:
            query = """
                SELECT
                    MIN(temp_c) AS temp_min,
                    MAX(temp_c) AS temp_max,
                    ROUND(AVG(temp_c), 2) AS temp_avg,
                    MIN(temp_f) AS temp_min_f,
                    MAX(temp_f) AS temp_max_f,
                    MIN(root_temp_f) AS root_temp_min_f,
                    MAX(root_temp_f) AS root_temp_max_f,
                    MIN(humidity) AS hum_min,
                    MAX(humidity) AS hum_max,
                    ROUND(AVG(humidity), 2) AS hum_avg,
                    MIN(soil_moisture) AS soil_min,
                    MAX(soil_moisture) AS soil_max,
                    COUNT(*) AS sample_count
                FROM readings
                WHERE timestamp >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ? || ' hours');
            """
            row = conn.execute(query, (cutoff,)).fetchone()
        return dict(row) if row else {}

    # ── Daily AI Analysis Reports ─────────────────────────────────────────────

    def save_daily_report(
        self,
        date_str: str,
        status: str,
        summary: str,
        leaf_drop: bool | int = 0,
        *,
        trees: list[dict[str, str]] | None = None,
        reference_dates: list[str] | None = None,
    ) -> None:
        """Save or update daily AI analysis report (upsert)."""
        conn = self._get_conn()
        now_utc = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        leaf_drop_int = 1 if leaf_drop else 0
        with conn:
            conn.execute(
                """
                INSERT INTO daily_reports
                    (date, created_at, status, summary, leaf_drop, trees, reference_dates)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    created_at = excluded.created_at,
                    status = excluded.status,
                    summary = excluded.summary,
                    leaf_drop = excluded.leaf_drop,
                    trees = excluded.trees,
                    reference_dates = excluded.reference_dates;
                """,
                (
                    date_str, now_utc, status, summary, leaf_drop_int,
                    json.dumps(trees or []),
                    json.dumps(reference_dates) if reference_dates is not None else None,
                ),
            )

    @staticmethod
    def _decode_report(row: sqlite3.Row) -> dict[str, Any]:
        report = dict(row)
        report["trees"] = json.loads(report["trees"])
        # NULL means legacy/unanalyzed; [] means an analyzed photo without references.
        if report["reference_dates"] is not None:
            report["reference_dates"] = json.loads(report["reference_dates"])
        return report

    def latest_report(self) -> dict[str, Any] | None:
        """Return the most recent daily AI analysis report."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM daily_reports ORDER BY date DESC LIMIT 1;"
        ).fetchone()
        return self._decode_report(row) if row else None

    def daily_reports(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return daily AI analysis reports ordered by date DESC."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM daily_reports ORDER BY date DESC LIMIT ?;",
            (limit,),
        ).fetchall()
        return [self._decode_report(r) for r in rows]

    def reports_by_date(self, limit: int = 365) -> dict[str, dict[str, Any]]:
        """Return mapping of date_str -> report dict for fast lookups."""
        reports = self.daily_reports(limit=limit)
        return {str(r["date"]): r for r in reports}

    def get_applied_migrations(self) -> list[dict[str, Any]]:
        """Return list of applied migrations for inspection."""
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM schema_migrations ORDER BY version ASC;").fetchall()
        return [dict(r) for r in rows]
