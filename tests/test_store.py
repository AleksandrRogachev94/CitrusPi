"""Tests for SQLite store with WAL mode and migrations."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from citruspi.models import Reading
from citruspi.store import Store


def test_migrations_applied(tmp_path: Path) -> None:
    store = Store(tmp_path)
    migrations = store.get_applied_migrations()
    assert len(migrations) >= 4
    assert migrations[0]["version"] == 1
    assert migrations[1]["version"] == 2
    assert migrations[2]["version"] == 3
    assert migrations[3]["version"] == 4


def test_insert_and_latest(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.insert(Reading.create(temp_c=15.5, humidity=65.0, root_temp_c=13.0))

    latest = store.latest()
    assert latest is not None
    assert latest["temp_c"] == 15.5
    assert latest["temp_f"] == 59.9
    assert latest["root_temp_c"] == 13.0
    assert latest["root_temp_f"] == 55.4
    assert latest["vpd_kpa"] is not None



def test_insert_reading_model(tmp_path: Path) -> None:
    store = Store(tmp_path)
    reading = Reading.create(temp_c=18.0, humidity=55.0, root_temp_c=14.5, light_lux=450.0)
    store.insert(reading)

    latest = store.latest()
    assert latest is not None
    assert latest["temp_c"] == 18.0
    assert latest["root_temp_c"] == 14.5
    assert latest["root_temp_f"] == 58.1
    assert latest["light_lux"] == 450.0
    assert latest["vpd_kpa"] is not None


def test_history(tmp_path: Path) -> None:
    store = Store(tmp_path)
    for i in range(5):
        store.insert(Reading.create(temp_c=10.0 + i, humidity=60.0))

    history = store.history(hours=1)
    assert len(history) == 5
    assert history[0]["temp_c"] == 10.0
    assert history[4]["temp_c"] == 14.0


def test_history_downsampled(tmp_path: Path) -> None:
    store = Store(tmp_path)
    for i in range(10):
        store.insert(Reading.create(temp_c=10.0 + i, humidity=60.0))

    history = store.history(hours=72, max_points=3)
    assert 2 <= len(history) <= 3
    assert history[0]["temp_c"] == 10.0
    assert history[-1]["temp_c"] == 19.0


def test_history_preserves_extremes_gaps_and_endpoints(tmp_path: Path) -> None:
    store = Store(tmp_path)
    now = datetime.now(UTC)
    for i in range(2999):
        values = {"temp_f": 60.0, "root_temp_f": 58.0, "humidity": 65.0, "vpd_kpa": 0.5}
        for field, offset, low, high in (
            ("temp_f", 100, 30.0, 95.0),
            ("root_temp_f", 102, 35.0, 100.0),
            ("humidity", 104, 20.0, 90.0),
            ("vpd_kpa", 106, 0.1, 2.0),
        ):
            if i == offset:
                values[field] = low
            elif i == offset + 1:
                values[field] = high
        if i == 300:
            values = dict.fromkeys(values)
        store.insert(Reading.create(
            timestamp=(now - timedelta(seconds=2999 - i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            **values,
        ))

    for limit in (14, 28, 1000):
        history = store.history(hours=24, max_points=limit)
        assert len(history) <= limit
        assert history[0]["id"] == 1
        assert history[-1]["id"] == 2999
        assert any(row["temp_f"] is None for row in history)
        for field, low, high in (
            ("temp_f", 30.0, 95.0), ("root_temp_f", 35.0, 100.0),
            ("humidity", 20.0, 90.0), ("vpd_kpa", 0.1, 2.0),
        ):
            values = [row[field] for row in history if row[field] is not None]
            assert min(values) == low
            assert max(values) == high
        assert history == sorted(history, key=lambda row: (row["timestamp"], row["id"]))
        assert "_total" not in history[0]


def test_daily_summary(tmp_path: Path) -> None:
    store = Store(tmp_path)
    date_str = "2026-10-13"
    store.insert(
        Reading.create(
            temp_c=10.0,
            humidity=50.0,
            root_temp_c=12.0,
            timestamp=f"{date_str}T08:00:00Z",
        )
    )
    store.insert(
        Reading.create(
            temp_c=20.0,
            humidity=70.0,
            root_temp_c=15.0,
            timestamp=f"{date_str}T14:00:00Z",
        )
    )

    summary = store.daily_summary(date_str)
    assert summary["sample_count"] == 2
    assert summary["temp_min"] == 10.0
    assert summary["temp_max"] == 20.0
    assert summary["temp_avg"] == 15.0
    assert summary["temp_min_f"] == 50.0
    assert summary["temp_max_f"] == 68.0
    assert summary["root_temp_min_f"] == 53.6
    assert summary["root_temp_max_f"] == 59.0
    assert summary["hum_min"] == 50.0
    assert summary["hum_max"] == 70.0


def test_summary_last_hours(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.insert(
        Reading.create(
            temp_c=12.0,
            humidity=55.0,
            root_temp_c=14.0,
        )
    )
    store.insert(
        Reading.create(
            temp_c=22.0,
            humidity=65.0,
            root_temp_c=16.0,
        )
    )

    summary = store.summary_last_hours(hours=24)
    assert summary["sample_count"] == 2
    assert summary["temp_min"] == 12.0
    assert summary["temp_max"] == 22.0
    assert summary["temp_avg"] == 17.0
    assert summary["temp_min_f"] == 53.6
    assert summary["temp_max_f"] == 71.6


def test_save_and_latest_daily_report(tmp_path: Path) -> None:
    store = Store(tmp_path)

    assert store.latest_report() is None

    # Save initial report
    store.save_daily_report(
        date_str="2026-10-12",
        status="healthy",
        summary="All trees look fine.",
        leaf_drop=False,
    )

    latest = store.latest_report()
    assert latest is not None
    assert latest["date"] == "2026-10-12"
    assert latest["status"] == "healthy"
    assert latest["summary"] == "All trees look fine."
    assert latest["leaf_drop"] == 0

    # Save report for newer date
    store.save_daily_report(
        date_str="2026-10-13",
        status="watch",
        summary="Meyer leaf drop detected.",
        leaf_drop=True,
    )

    latest2 = store.latest_report()
    assert latest2 is not None
    assert latest2["date"] == "2026-10-13"
    assert latest2["status"] == "watch"
    assert latest2["leaf_drop"] == 1
    assert "Meyer leaf drop" in latest2["summary"]

    # Upsert test: overwrite report for same date
    store.save_daily_report(
        date_str="2026-10-13",
        status="alert",
        summary="Updated: severe defoliation.",
        leaf_drop=True,
    )

    latest_upsert = store.latest_report()
    assert latest_upsert is not None
    assert latest_upsert["date"] == "2026-10-13"
    assert latest_upsert["status"] == "alert"
    assert latest_upsert["summary"] == "Updated: severe defoliation."


def test_daily_reports_query_and_mapping(tmp_path: Path) -> None:
    store = Store(tmp_path)

    assert store.daily_reports() == []
    assert store.reports_by_date() == {}

    store.save_daily_report("2026-10-10", "healthy", "Looking green", leaf_drop=False)
    store.save_daily_report("2026-10-11", "watch", "Slight curl", leaf_drop=False)
    store.save_daily_report("2026-10-12", "alert", "Leaf drop", leaf_drop=True)

    reports = store.daily_reports(limit=2)
    assert len(reports) == 2
    # Ordered by date DESC
    assert reports[0]["date"] == "2026-10-12"
    assert reports[1]["date"] == "2026-10-11"

    mapping = store.reports_by_date()
    assert len(mapping) == 3
    assert mapping["2026-10-10"]["status"] == "healthy"
    assert mapping["2026-10-11"]["status"] == "watch"
    assert mapping["2026-10-12"]["status"] == "alert"
    assert mapping["2026-10-12"]["leaf_drop"] == 1

