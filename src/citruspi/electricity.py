"""Simple time-of-use pricing for measured power intervals."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from citruspi.config import ElectricityConfig


def interval_cost(
    start: datetime, end: datetime, start_watts: float, end_watts: float,
    rates: ElectricityConfig,
) -> float:
    """Integrate linearly interpolated watts, splitting at local hourly boundaries.

    Step along UTC hours so both repeated and skipped DST hours retain their
    actual elapsed duration. PECO's Eastern timezone has whole-hour offsets.
    """
    timezone = ZoneInfo(rates.timezone)
    duration = (end - start).total_seconds()
    cost = 0.0
    left = start
    while left < end:
        local = left.astimezone(timezone)
        hour = local.hour
        if rates.super_off_peak_start_hour <= hour < rates.super_off_peak_end_hour:
            rate = rates.super_off_peak_rate
        elif (rates.peak_start_hour <= hour < rates.peak_end_hour
              and local.weekday() in rates.peak_weekdays
              and local.date().isoformat() not in rates.holidays):
            rate = rates.peak_rate
        else:
            rate = rates.off_peak_rate
        # Next local clock-hour boundary, expressed on the UTC timeline.
        right = min(end, left + timedelta(
            seconds=3600 - local.minute * 60 - local.second - local.microsecond / 1e6,
        ))
        watts_left = start_watts + (end_watts - start_watts) * (
            (left - start).total_seconds() / duration
        )
        watts_right = start_watts + (end_watts - start_watts) * (
            (right - start).total_seconds() / duration
        )
        kwh = (watts_left + watts_right) / 2 * (right - left).total_seconds() / 3_600_000
        cost += kwh * (rate + rates.delivery_rate)
        left = right.astimezone(UTC)
    return cost
