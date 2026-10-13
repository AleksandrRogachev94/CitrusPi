"""Tests for camera photoperiod logic."""

from datetime import datetime

import pytest

from citruspi.camera import is_daylight
from citruspi.config import Config


@pytest.fixture
def config() -> Config:
    return Config(lights_on_hour=7, lights_off_hour=17)


@pytest.mark.parametrize(
    "hour, expected",
    [
        (6, False),
        (7, True),
        (12, True),
        (16, True),
        (17, False),
        (23, False),
    ],
)
def test_is_daylight(config: Config, hour: int, expected: bool) -> None:
    now = datetime(2026, 1, 15, hour, 30)
    assert is_daylight(config, now=now) is expected
