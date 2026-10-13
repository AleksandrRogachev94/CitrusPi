"""Tests for SQLite store."""

from pathlib import Path

from citruspi.store import Store


def test_insert_and_latest(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.insert({"temp_c": 15.5, "temp_f": 59.9, "humidity": 65.0, "soil_moisture": 700})

    latest = store.latest()
    assert latest is not None
    assert latest["temp_c"] == 15.5
    assert latest["soil_moisture"] == 700


def test_history(tmp_path: Path) -> None:
    store = Store(tmp_path)
    for i in range(5):
        store.insert({"temp_c": 10.0 + i, "humidity": 60.0})

    history = store.history(hours=1)
    assert len(history) == 5
    assert history[0]["temp_c"] == 10.0
    assert history[4]["temp_c"] == 14.0
