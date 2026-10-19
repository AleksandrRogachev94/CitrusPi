"""Power history retains outages; aggregates never extrapolate over missing data."""

from datetime import UTC, datetime, timedelta

import pytest

from citruspi.models import PowerReading
from citruspi.store import Store


def insert(store, timestamp, watts, *, plug="heater", active=True, today=0):
    store.insert_power(PowerReading(
        plug, timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        power_w=watts, active=active, energy_today_kwh=today,
    ))


def test_energy_runtime_and_midnight_counter_reset(tmp_path):
    store = Store(tmp_path)
    now = datetime(2026, 10, 19, 0, 1, tzinfo=UTC)
    for age, counter in ((120, 10), (60, 0), (0, 0.01)):
        insert(store, now - timedelta(seconds=age), 600, today=counter)
    summary = store.power_summary("heater", 60, now)
    assert summary["estimated_kwh"] == 0.02
    assert summary["runtime_hours"] == 0.03
    assert summary["duty_cycle_pct"] == 100
    assert summary["coverage_pct"] == 0.1
    assert store.latest_power("heater")["energy_today_kwh"] == 0.01
    assert store.latest_power("heater")["active"] is True


def test_failures_and_long_gaps_are_excluded(tmp_path):
    store = Store(tmp_path)
    now = datetime.now(UTC).replace(microsecond=0)
    for minutes, watts, active in ((0, 600, True), (1, 600, True), (2, None, None),
                                    (3, 600, True), (20, 600, True), (21, 600, True)):
        insert(store, now - timedelta(minutes=21 - minutes), watts, active=active)
    summary = store.power_summary("heater", 60, now)
    assert summary["estimated_kwh"] == 0.02
    assert summary["runtime_hours"] == 0.03
    assert summary["coverage_pct"] == 0.1
    assert summary["duty_cycle_pct"] == 100


def test_window_clipping_and_no_extrapolation(tmp_path):
    store = Store(tmp_path)
    now = datetime.now(UTC).replace(microsecond=0)
    start = now - timedelta(hours=24)
    insert(store, start - timedelta(seconds=30), 600)
    insert(store, start + timedelta(seconds=30), 600)
    summary = store.power_summary("heater", 60, now)
    assert summary["estimated_kwh"] == 0.005
    assert summary["runtime_hours"] == 0.01
    assert summary["coverage_pct"] == 0


def test_no_measurements_are_unknown_not_zero(tmp_path):
    store = Store(tmp_path)
    assert store.latest_power("missing") is None
    summary = store.power_summary("missing", 60)
    assert summary == {"estimated_kwh": None, "runtime_hours": None,
                       "duty_cycle_pct": None, "coverage_pct": 0}
    assert store.power_history("missing") == []


def test_history_preserves_peaks_missing_values_and_is_bounded(tmp_path):
    store = Store(tmp_path)
    now = datetime.now(UTC).replace(microsecond=0)
    for index in range(4000):
        watts = 900 if index == 127 else None if index == 130 else 100
        insert(store, now - timedelta(seconds=4000-index), watts)
    rows = store.power_history("heater", max_points=100)
    assert len(rows) <= 100
    assert rows[0]["timestamp"] == (now - timedelta(seconds=4000)).strftime('%Y-%m-%dT%H:%M:%SZ')
    assert rows[-1]["timestamp"] == (now - timedelta(seconds=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
    assert any(r["power_w"] == 900 for r in rows)
    assert any(r["power_w"] is None for r in rows)
    assert store.power_history("other") == []


def test_history_inserts_null_for_unrecorded_outage(tmp_path):
    store = Store(tmp_path)
    now = datetime.now(UTC).replace(microsecond=0)
    insert(store, now - timedelta(minutes=15), 100)
    insert(store, now, 100)
    history = store.power_history("heater")
    assert [row["power_w"] for row in history] == [100, None, 100]


def test_old_samples_are_excluded_and_sampling_limit_validated(tmp_path):
    store = Store(tmp_path)
    insert(store, datetime.now(UTC) - timedelta(hours=25), 100)
    assert store.power_history("heater", hours=24) == []
    with pytest.raises(ValueError):
        store.power_history("heater", max_points=4)


def test_migration_is_repeatable_and_preserves_sensor_history(tmp_path):
    from citruspi.models import Reading

    store = Store(tmp_path)
    store.insert(Reading.create(temp_c=20))
    insert(store, datetime.now(UTC), 100)
    reopened = Store(tmp_path)
    assert reopened.latest()["temp_c"] == 20
    assert reopened.latest_power("heater")["power_w"] == 100
    assert [m["version"] for m in reopened.get_applied_migrations()] == list(range(1, 7))


def test_power_activity_intervals_merging_and_outages(tmp_path):
    store = Store(tmp_path)
    now = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)

    # Plug with no readings returns empty activity and None timestamp
    assert store.power_activity("heater", hours=24, max_gap_sec=120, now=now) == []
    assert store.first_power_timestamp("heater") is None

    # T0 to T2 active, T3 to T4 idle, gap to T10 (outage), T10 to T11 active
    t0 = now - timedelta(minutes=60)
    t1 = now - timedelta(minutes=58)
    t2 = now - timedelta(minutes=56)
    t3 = now - timedelta(minutes=54)
    t4 = now - timedelta(minutes=52)
    t10 = now - timedelta(minutes=10)
    t11 = now - timedelta(minutes=8)

    insert(store, t0, 100, active=True)
    insert(store, t1, 100, active=True)
    insert(store, t2, 100, active=True)
    insert(store, t3, 0, active=False)
    insert(store, t4, 0, active=False)
    insert(store, t10, 100, active=True)
    insert(store, t11, 100, active=True)

    assert store.first_power_timestamp("heater") == t0.strftime('%Y-%m-%dT%H:%M:%SZ')

    # max_gap_sec = 180s (3 minutes)
    intervals = store.power_activity("heater", hours=2, max_gap_sec=180, now=now)
    # Merged active interval t0 to t2
    # Transition to idle t2 to t4
    # Outage gap between t4 and t10 is excluded
    # Active interval t10 to t11
    assert len(intervals) == 3
    assert intervals[0] == {
        "start": t0.strftime('%Y-%m-%dT%H:%M:%SZ'),
        "end": t3.strftime('%Y-%m-%dT%H:%M:%SZ'),
        "active": True,
    }
    assert intervals[1] == {
        "start": t3.strftime('%Y-%m-%dT%H:%M:%SZ'),
        "end": t4.strftime('%Y-%m-%dT%H:%M:%SZ'),
        "active": False,
    }
    assert intervals[2] == {
        "start": t10.strftime('%Y-%m-%dT%H:%M:%SZ'),
        "end": t11.strftime('%Y-%m-%dT%H:%M:%SZ'),
        "active": True,
    }

