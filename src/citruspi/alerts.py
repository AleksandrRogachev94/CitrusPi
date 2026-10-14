"""Alerting and heartbeat module for CitrusPi — synchronous, simple, no threads/locks."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

import httpx

if TYPE_CHECKING:
    from citruspi.config import Config

logger = logging.getLogger(__name__)


@dataclass
class AlertRule:
    """State and thresholds for an alert rule."""

    name: str
    title: str
    priority: str
    cooldown_sec: float
    critical: bool = False
    breach_count: int = 0
    _last_sent: float | None = None
    is_active: bool = False

    @property
    def last_sent(self) -> float | None:
        return self._last_sent

    @last_sent.setter
    def last_sent(self, value: float | None) -> None:
        self._last_sent = value

    @property
    def active(self) -> bool:
        return self.is_active

    @active.setter
    def active(self, value: bool) -> None:
        self.is_active = value


class AlertManager:
    """Evaluates telemetry readings against alert rules and dispatches ntfy & heartbeats."""

    def __init__(self, config: Config, clock: Callable[[], float] = time.time) -> None:
        self.config = config
        self.clock = clock
        self.rules: dict[str, AlertRule] = {
            "canopy_offline": AlertRule(
                name="canopy_offline",
                title="Canopy Sensor Offline",
                priority="high",
                cooldown_sec=3600.0,
                critical=False,
            ),
            "temp_crit_low": AlertRule(
                name="temp_crit_low",
                title="CRITICAL: Low Temperature",
                priority="urgent",
                cooldown_sec=1800.0,
                critical=True,
            ),
            "temp_crit_high": AlertRule(
                name="temp_crit_high",
                title="CRITICAL: High Temperature",
                priority="urgent",
                cooldown_sec=1800.0,
                critical=True,
            ),
            "temp_warn_low": AlertRule(
                name="temp_warn_low",
                title="WARNING: Low Temperature",
                priority="high",
                cooldown_sec=14400.0,
                critical=False,
            ),
        }

    def send_ntfy(
        self,
        title: str,
        message: str,
        priority: str = "default",
        tags: str = "lemon",
    ) -> bool:
        """Send an ntfy notification. Returns True on success, False on failure or if disabled."""
        if not self.config.ntfy_topic:
            return False

        url = f"{self.config.ntfy_url.rstrip('/')}/{self.config.ntfy_topic}"
        headers = {
            "Title": title,
            "Priority": priority,
        }
        if tags:
            headers["Tags"] = tags

        try:
            resp = httpx.post(
                url,
                content=message.encode("utf-8"),
                headers=headers,
                timeout=3.0,
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.warning("Failed to send ntfy alert: %s", e)
            return False

    def send_heartbeat(self) -> None:
        """Send a GET request to heartbeat_url if configured. Swallows exceptions."""
        if not self.config.heartbeat_url:
            return

        try:
            resp = httpx.get(self.config.heartbeat_url, timeout=3.0)
            resp.raise_for_status()
        except Exception as e:
            logger.warning("Failed to send heartbeat: %s", e)

    def _evaluate_rule(
        self,
        rule: AlertRule,
        *,
        is_breached: bool,
        is_recovered: bool,
        breach_msg: str,
        recovery_msg: str,
    ) -> None:
        """Update consecutive debouncing, active state, and dispatch notifications."""
        if rule.is_active:
            if is_recovered:
                self.send_ntfy(
                    title=f"Resolved: {rule.title}",
                    message=recovery_msg,
                    priority="default",
                    tags="white_check_mark",
                )
                rule.is_active = False
                rule.breach_count = 0
                if rule.critical:
                    rule._last_sent = None
            elif is_breached:
                rule.breach_count += 1
                now = self.clock()
                cooldown_elapsed = (rule._last_sent is None) or (
                    (now - rule._last_sent) >= rule.cooldown_sec
                )
                if rule.breach_count >= self.config.alert_debounce_count and cooldown_elapsed:
                    sent = self.send_ntfy(
                        title=rule.title,
                        message=breach_msg,
                        priority=rule.priority,
                        tags="lemon",
                    )
                    if sent:
                        rule._last_sent = now
            else:
                # Inside hysteresis band: while active and not recovered, changes nothing
                pass
        else:
            if is_breached:
                rule.breach_count += 1
                now = self.clock()
                cooldown_elapsed = (rule._last_sent is None) or (
                    (now - rule._last_sent) >= rule.cooldown_sec
                )
                if rule.breach_count >= self.config.alert_debounce_count and cooldown_elapsed:
                    sent = self.send_ntfy(
                        title=rule.title,
                        message=breach_msg,
                        priority=rule.priority,
                        tags="lemon",
                    )
                    if sent:
                        rule._last_sent = now
                        rule.is_active = True
            else:
                # Strictly consecutive debounce: non-breached reading resets counter
                rule.breach_count = 0

    def evaluate(self, reading: Any) -> None:
        """Evaluate a reading against all rules."""
        temp_f = getattr(reading, "temp_f", None) if not isinstance(reading, dict) else reading.get("temp_f")
        if temp_f is not None:
            try:
                temp_f = float(temp_f)
            except (ValueError, TypeError):
                temp_f = None

        if temp_f is None:
            # If temp_f is None, run only canopy_offline. Skip rules 2-4 entirely.
            self._evaluate_rule(
                self.rules["canopy_offline"],
                is_breached=True,
                is_recovered=False,
                breach_msg="Canopy temperature sensor is offline (None reading).",
                recovery_msg="",
            )
            return

        # temp_f is not None: evaluate canopy_offline recovery
        self._evaluate_rule(
            self.rules["canopy_offline"],
            is_breached=False,
            is_recovered=True,
            breach_msg="",
            recovery_msg=f"Canopy sensor back online ({temp_f:.1f}°F).",
        )

        # 2. temp_crit_low: breached at <= temp_min_crit_f; recovered at >= temp_min_crit_f + 2.0
        self._evaluate_rule(
            self.rules["temp_crit_low"],
            is_breached=(temp_f <= self.config.temp_min_crit_f),
            is_recovered=(temp_f >= self.config.temp_min_crit_f + 2.0),
            breach_msg=(
                f"Canopy temperature dropped to {temp_f:.1f}°F "
                f"(critical minimum: {self.config.temp_min_crit_f:.1f}°F)."
            ),
            recovery_msg=f"Canopy temperature recovered to {temp_f:.1f}°F.",
        )

        # 3. temp_crit_high: breached at >= temp_max_crit_f; recovered at <= temp_max_crit_f - 2.0
        self._evaluate_rule(
            self.rules["temp_crit_high"],
            is_breached=(temp_f >= self.config.temp_max_crit_f),
            is_recovered=(temp_f <= self.config.temp_max_crit_f - 2.0),
            breach_msg=(
                f"Canopy temperature rose to {temp_f:.1f}°F "
                f"(critical maximum: {self.config.temp_max_crit_f:.1f}°F)."
            ),
            recovery_msg=f"Canopy temperature recovered to {temp_f:.1f}°F.",
        )

        # 4. temp_warn_low: breached when temp_min_crit_f < temp_f <= temp_min_warn_f;
        #    recovered at >= temp_min_warn_f + 2.0
        self._evaluate_rule(
            self.rules["temp_warn_low"],
            is_breached=(self.config.temp_min_crit_f < temp_f <= self.config.temp_min_warn_f),
            is_recovered=(temp_f >= self.config.temp_min_warn_f + 2.0),
            breach_msg=(
                f"Canopy temperature dropped to {temp_f:.1f}°F "
                f"(warning threshold: {self.config.temp_min_warn_f:.1f}°F)."
            ),
            recovery_msg=f"Canopy temperature recovered to {temp_f:.1f}°F.",
        )
