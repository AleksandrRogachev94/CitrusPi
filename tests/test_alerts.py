"""Tests for CitrusPi synchronous alerting and heartbeat module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from citruspi.alerts import AlertManager
from citruspi.config import Config
from citruspi.models import Reading


class MockClock:
    """Controllable clock for deterministic timer and cooldown tests."""

    def __init__(self, start_time: float = 1000.0) -> None:
        self.current = start_time

    def __call__(self) -> float:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += seconds


@pytest.fixture
def base_config() -> Config:
    return Config(
        ntfy_url="https://ntfy.sh",
        ntfy_topic="test-topic",
        heartbeat_url="https://heartbeat.example.com/ping",
        temp_min_crit_f=38.0,
        temp_min_warn_f=44.0,
        temp_max_crit_f=85.0,
        alert_debounce_count=3,
    )


# ── 1. Consecutive Debounce Reset ───────────────────────────────────────────


def test_consecutive_debounce_reset(base_config: Config) -> None:
    clock = MockClock(1000.0)
    alerts = AlertManager(base_config, clock=clock)

    with patch("httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)

        # 2 breached readings
        alerts.evaluate(Reading.create(temp_f=35.0))
        assert alerts.rules["temp_crit_low"].breach_count == 1
        alerts.evaluate(Reading.create(temp_f=35.0))
        assert alerts.rules["temp_crit_low"].breach_count == 2
        assert not alerts.rules["temp_crit_low"].is_active
        mock_post.assert_not_called()

        # Non-breached reading resets breach count to 0
        alerts.evaluate(Reading.create(temp_f=50.0))
        assert alerts.rules["temp_crit_low"].breach_count == 0
        assert not alerts.rules["temp_crit_low"].is_active
        mock_post.assert_not_called()

        # Needs 3 full consecutive breached readings to alert
        alerts.evaluate(Reading.create(temp_f=35.0))
        assert alerts.rules["temp_crit_low"].breach_count == 1
        alerts.evaluate(Reading.create(temp_f=35.0))
        assert alerts.rules["temp_crit_low"].breach_count == 2
        mock_post.assert_not_called()

        alerts.evaluate(Reading.create(temp_f=35.0))
        assert alerts.rules["temp_crit_low"].breach_count == 3
        assert alerts.rules["temp_crit_low"].is_active
        assert mock_post.call_count == 1


# ── 2. None Guard ───────────────────────────────────────────────────────────


def test_none_guard(base_config: Config) -> None:
    """None guard: offline fires, no TypeError, temperature alerts neither reset nor resolve."""
    clock = MockClock(1000.0)
    alerts = AlertManager(base_config, clock=clock)

    with patch("httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)

        # Setup temp_crit_low with 2 breach counts
        alerts.evaluate(Reading.create(temp_f=35.0))
        alerts.evaluate(Reading.create(temp_f=35.0))
        assert alerts.rules["temp_crit_low"].breach_count == 2

        # Feed temp_f = None (sensor disconnected / read error)
        # Must not raise TypeError: '<=' not supported between instances of 'NoneType' and 'float'
        reading_none = Reading.create(temp_f=None)
        alerts.evaluate(reading_none)

        # canopy_offline is evaluated and breached
        assert alerts.rules["canopy_offline"].breach_count == 1

        # Temperature alerts are skipped entirely: counter NOT reset and NOT resolved
        assert alerts.rules["temp_crit_low"].breach_count == 2
        assert not alerts.rules["temp_crit_low"].is_active

        # Second None reading
        alerts.evaluate(reading_none)
        assert alerts.rules["canopy_offline"].breach_count == 2
        assert alerts.rules["temp_crit_low"].breach_count == 2

        # Third None reading: canopy_offline reaches debounce threshold 3 and alerts
        alerts.evaluate(reading_none)
        assert alerts.rules["canopy_offline"].breach_count == 3
        assert alerts.rules["canopy_offline"].is_active
        assert alerts.rules["temp_crit_low"].breach_count == 2
        assert mock_post.call_count == 1
        # Priority should be high for canopy_offline
        headers = mock_post.call_args[1]["headers"]
        assert headers["Priority"] == "high"

        # Now sensor recovers with valid temp
        mock_post.reset_mock()
        alerts.evaluate(Reading.create(temp_f=50.0))

        # canopy_offline recovers and sends Resolved
        assert not alerts.rules["canopy_offline"].is_active
        assert alerts.rules["canopy_offline"].breach_count == 0
        assert mock_post.call_count == 1
        res_headers = mock_post.call_args[1]["headers"]
        assert res_headers["Priority"] == "default"
        assert res_headers["Tags"] == "white_check_mark"

        # temp_crit_low saw 50.0°F, so its breach count is now reset
        assert alerts.rules["temp_crit_low"].breach_count == 0


def test_none_guard_does_not_resolve_active_temp_alert(base_config: Config) -> None:
    """An active temperature alert must not be resolved by a None reading."""
    clock = MockClock(1000.0)
    alerts = AlertManager(base_config, clock=clock)

    with patch("httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)

        # Activate temp_crit_low
        for _ in range(3):
            alerts.evaluate(Reading.create(temp_f=35.0))
        assert alerts.rules["temp_crit_low"].is_active
        mock_post.reset_mock()

        # None reading occurs
        alerts.evaluate(Reading.create(temp_f=None))

        # temp_crit_low must remain active and not resolved
        assert alerts.rules["temp_crit_low"].is_active
        mock_post.assert_not_called()


# ── 3. Hysteresis Recovery ──────────────────────────────────────────────────


def test_hysteresis_recovery_crit_low(base_config: Config) -> None:
    """Breached <= 38.0; recovered >= 40.0; inside (38.0, 40.0) changes nothing."""
    clock = MockClock(1000.0)
    alerts = AlertManager(base_config, clock=clock)

    with patch("httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)

        # Trigger critical low alert
        for _ in range(3):
            alerts.evaluate(Reading.create(temp_f=37.0))
        assert alerts.rules["temp_crit_low"].is_active
        assert mock_post.call_count == 1
        mock_post.reset_mock()

        # Reading in hysteresis band (39.0°F): not breached, but not recovered
        alerts.evaluate(Reading.create(temp_f=39.0))
        assert alerts.rules["temp_crit_low"].is_active
        mock_post.assert_not_called()

        # Recovered at >= 40.0°F (38.0 + 2.0 = 40.0)
        alerts.evaluate(Reading.create(temp_f=41.0))
        assert not alerts.rules["temp_crit_low"].is_active
        assert alerts.rules["temp_crit_low"].breach_count == 0
        assert mock_post.call_count == 1
        headers = mock_post.call_args[1]["headers"]
        assert headers["Priority"] == "default"
        assert headers["Tags"] == "white_check_mark"
        assert "Resolved" in headers["Title"]


def test_hysteresis_recovery_crit_high(base_config: Config) -> None:
    """Breached >= 85.0; recovered <= 83.0; inside (83.0, 85.0) changes nothing."""
    clock = MockClock(1000.0)
    alerts = AlertManager(base_config, clock=clock)

    with patch("httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)

        # Trigger critical high alert
        for _ in range(3):
            alerts.evaluate(Reading.create(temp_f=86.0))
        assert alerts.rules["temp_crit_high"].is_active
        assert mock_post.call_count == 1
        mock_post.reset_mock()

        # Inside hysteresis band (84.0°F)
        alerts.evaluate(Reading.create(temp_f=84.0))
        assert alerts.rules["temp_crit_high"].is_active
        mock_post.assert_not_called()

        # Recovered at <= 83.0°F (85.0 - 2.0)
        alerts.evaluate(Reading.create(temp_f=82.5))
        assert not alerts.rules["temp_crit_high"].is_active
        assert mock_post.call_count == 1
        headers = mock_post.call_args[1]["headers"]
        assert headers["Tags"] == "white_check_mark"


def test_hysteresis_recovery_warn_low(base_config: Config) -> None:
    """Breached when 38 < temp <= 44; recovered >= 46.0; inside (44, 46) changes nothing."""
    clock = MockClock(1000.0)
    alerts = AlertManager(base_config, clock=clock)

    with patch("httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)

        # Trigger warning low alert
        for _ in range(3):
            alerts.evaluate(Reading.create(temp_f=42.0))
        assert alerts.rules["temp_warn_low"].is_active
        assert mock_post.call_count == 1
        headers = mock_post.call_args[1]["headers"]
        assert headers["Priority"] == "high"
        mock_post.reset_mock()

        # Inside hysteresis band (45.0°F)
        alerts.evaluate(Reading.create(temp_f=45.0))
        assert alerts.rules["temp_warn_low"].is_active
        mock_post.assert_not_called()

        # Recovered at >= 46.0°F (44.0 + 2.0)
        alerts.evaluate(Reading.create(temp_f=46.5))
        assert not alerts.rules["temp_warn_low"].is_active
        assert mock_post.call_count == 1
        res_headers = mock_post.call_args[1]["headers"]
        assert res_headers["Tags"] == "white_check_mark"


# ── 4. Critical Cooldown Cleared on Resolution ──────────────────────────────


def test_critical_cooldown_cleared_on_resolution(base_config: Config) -> None:
    """Critical rules clear _last_sent on resolution so a relapse pages immediately."""
    clock = MockClock(1000.0)
    alerts = AlertManager(base_config, clock=clock)

    with patch("httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)

        # Trigger critical alert at t=1000
        for _ in range(3):
            alerts.evaluate(Reading.create(temp_f=35.0))
        assert alerts.rules["temp_crit_low"].is_active
        assert alerts.rules["temp_crit_low"]._last_sent == 1000.0

        # Recover at t=1100
        clock.advance(100)
        alerts.evaluate(Reading.create(temp_f=50.0))
        assert not alerts.rules["temp_crit_low"].is_active
        # Critical rule must clear _last_sent
        assert alerts.rules["temp_crit_low"]._last_sent is None

        # Relapse occurs at t=1200 (only 200s after initial alert; cooldown is 30m / 1800s)
        clock.advance(100)
        mock_post.reset_mock()

        alerts.evaluate(Reading.create(temp_f=35.0))
        alerts.evaluate(Reading.create(temp_f=35.0))
        assert mock_post.call_count == 0

        # On the 3rd consecutive reading, pages immediately without waiting 30m
        alerts.evaluate(Reading.create(temp_f=35.0))
        assert alerts.rules["temp_crit_low"].is_active
        assert mock_post.call_count == 1
        assert alerts.rules["temp_crit_low"]._last_sent == 1200.0


def test_non_critical_cooldown_retained_on_resolution(base_config: Config) -> None:
    """Non-critical rules (e.g. temp_warn_low) retain _last_sent so flapping does not spam."""
    clock = MockClock(1000.0)
    alerts = AlertManager(base_config, clock=clock)

    with patch("httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)

        # Trigger warning alert at t=1000
        for _ in range(3):
            alerts.evaluate(Reading.create(temp_f=42.0))
        assert alerts.rules["temp_warn_low"].is_active
        assert alerts.rules["temp_warn_low"]._last_sent == 1000.0

        # Recover at t=1100
        clock.advance(100)
        alerts.evaluate(Reading.create(temp_f=50.0))
        assert not alerts.rules["temp_warn_low"].is_active
        # Non-critical rule does NOT clear _last_sent
        assert alerts.rules["temp_warn_low"]._last_sent == 1000.0

        # Relapse at t=1200 (within 4h cooldown = 14400s)
        clock.advance(100)
        mock_post.reset_mock()
        for _ in range(3):
            alerts.evaluate(Reading.create(temp_f=42.0))

        # Cooldown has NOT elapsed, so no duplicate alert sent
        assert not alerts.rules["temp_warn_low"].is_active
        mock_post.assert_not_called()


# ── 5. Failed Send Retried Next Poll ─────────────────────────────────────────


def test_failed_send_retried_next_poll(base_config: Config) -> None:
    """A failed send does not mark active or record _last_sent, and retries on next poll."""
    clock = MockClock(1000.0)
    alerts = AlertManager(base_config, clock=clock)

    with patch("httpx.post") as mock_post:
        # First 2 polls
        alerts.evaluate(Reading.create(temp_f=35.0))
        alerts.evaluate(Reading.create(temp_f=35.0))

        # 3rd poll: network failure
        mock_post.side_effect = Exception("network unreachable")
        alerts.evaluate(Reading.create(temp_f=35.0))

        assert alerts.rules["temp_crit_low"].breach_count == 3
        # Failed send must not record _last_sent or mark active
        assert alerts.rules["temp_crit_low"]._last_sent is None
        assert not alerts.rules["temp_crit_low"].is_active

        # 4th poll: network recovers
        mock_post.side_effect = None
        mock_post.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)
        clock.advance(60)

        alerts.evaluate(Reading.create(temp_f=35.0))
        assert alerts.rules["temp_crit_low"].breach_count == 4
        # Alert successfully sent on retry
        assert alerts.rules["temp_crit_low"].is_active
        assert alerts.rules["temp_crit_low"]._last_sent == 1060.0
        assert mock_post.call_count == 2


# ── 6. Swallowing Network Errors ────────────────────────────────────────────


def test_send_ntfy_swallows_network_errors(base_config: Config) -> None:
    alerts = AlertManager(base_config)

    with patch("httpx.post", side_effect=Exception("Connection refused")):
        ok = alerts.send_ntfy(title="Test", message="Fail me")
        assert ok is False


def test_send_ntfy_no_topic_is_noop() -> None:
    cfg = Config(ntfy_topic="")
    alerts = AlertManager(cfg)

    with patch("httpx.post") as mock_post:
        ok = alerts.send_ntfy(title="Test", message="No topic")
        assert ok is False
        mock_post.assert_not_called()


def test_send_heartbeat_swallows_network_errors(base_config: Config) -> None:
    alerts = AlertManager(base_config)

    with patch("httpx.get", side_effect=Exception("503 Service Unavailable")):
        # Must not raise
        alerts.send_heartbeat()


def test_send_heartbeat_noop_when_empty() -> None:
    cfg = Config(heartbeat_url="")
    alerts = AlertManager(cfg)

    with patch("httpx.get") as mock_get:
        alerts.send_heartbeat()
        mock_get.assert_not_called()


def test_send_heartbeat_success(base_config: Config) -> None:
    alerts = AlertManager(base_config)

    with patch("httpx.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200, raise_for_status=lambda: None)
        alerts.send_heartbeat()
        mock_get.assert_called_once_with("https://heartbeat.example.com/ping", timeout=3.0)


# ── 7. CLI --test-alert Tests ───────────────────────────────────────────────


def test_cli_test_alert_success() -> None:
    from citruspi.__main__ import main

    with (
        patch("sys.argv", ["citruspi", "--test-alert"]),
        patch("citruspi.alerts.AlertManager.send_ntfy", return_value=True) as mock_ntfy,
        patch("citruspi.alerts.AlertManager.send_heartbeat") as mock_hb,
        patch("sys.exit") as mock_exit,
    ):
        main()
        mock_ntfy.assert_called_once()
        assert mock_ntfy.call_args[1]["priority"] == "urgent"
        mock_hb.assert_called_once()
        mock_exit.assert_called_once_with(0)


def test_cli_test_alert_failure() -> None:
    from citruspi.__main__ import main

    with (
        patch("sys.argv", ["citruspi", "--test-alert"]),
        patch("citruspi.alerts.AlertManager.send_ntfy", return_value=False) as mock_ntfy,
        patch("citruspi.alerts.AlertManager.send_heartbeat") as mock_hb,
        patch("sys.exit") as mock_exit,
    ):
        main()
        mock_ntfy.assert_called_once()
        mock_hb.assert_called_once()
        mock_exit.assert_called_once_with(1)

