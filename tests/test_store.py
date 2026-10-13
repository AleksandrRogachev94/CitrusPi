"""Tests for SQLite store with WAL mode and migrations."""

from pathlib import Path

from citruspi.models import Reading
from citruspi.store import Store


def test_migrations_applied(tmp_path: Path) -> None:
    store = Store(tmp_path)
    migrations = store.get_applied_migrations()
    assert len(migrations) >= 3
    assert migrations[0]["version"] == 1
    assert migrations[1]["version"] == 2
    assert migrations[2]["version"] == 3


def test_insert_dict_and_latest(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.insert({"temp_c": 15.5, "humidity": 65.0, "soil_moisture": 700})

    latest = store.latest()
    assert latest is not None
    assert latest["temp_c"] == 15.5
    assert latest["temp_f"] == 59.9
    assert latest["soil_moisture"] == 700


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

    # max_points=3 with 10 rows gives stride=3 (indices 0, 3, 6, 9 -> 4 items)
    history = store.history(hours=72, max_points=3)
    assert len(history) == 4
    assert history[0]["temp_c"] == 10.0
    assert history[1]["temp_c"] == 13.0


def test_daily_summary(tmp_path: Path) -> None:
    store = Store(tmp_path)
    date_str = "2026-10-13"
    store.insert(Reading.create(temp_c=10.0, humidity=50.0, timestamp=f"{date_str}T08:00:00Z"))
    store.insert(Reading.create(temp_c=20.0, humidity=70.0, timestamp=f"{date_str}T14:00:00Z"))

    summary = store.daily_summary(date_str)
    assert summary["sample_count"] == 2
    assert summary["temp_min"] == 10.0
    assert summary["temp_max"] == 20.0
    assert summary["temp_avg"] == 15.0
    assert summary["hum_min"] == 50.0
    assert summary["hum_max"] == 70.0
