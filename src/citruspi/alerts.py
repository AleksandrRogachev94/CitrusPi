"""Alerting and heartbeat module for CitrusPi — thread-safe rule evaluation & ntfy dispatch."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

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
    last_sent: float | None = None
    is_active: bool = False


class AlertManager:
    """Evaluates telemetry readings against alert rules and dispatches ntfy & heartbeats."""

    def __init__(
        self,
        config: Config,
        clock: Callable[[], float] = time.time,
        notify: Callable[[str, str, str, str], bool] | None = None,
    ) -> None:
        self.config = config
        self.clock = clock
        self._notify = notify or self.send_ntfy
        self._lock = threading.Lock()
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
            logger.warning("Failed to send ntfy alert (%s)", type(e).__name__)
            return False

    def send_heartbeat(self) -> bool:
        """Send a GET request to heartbeat_url if configured. Returns True on success."""
        if not self.config.heartbeat_url:
            return False

        try:
            resp = httpx.get(self.config.heartbeat_url, timeout=3.0)
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.warning("Failed to send heartbeat (%s)", type(e).__name__)
            return False

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
        if rule.is_active and is_recovered:
            self._notify(
                f"Resolved: {rule.title}",
                recovery_msg,
                "default",
                "white_check_mark",
            )
            rule.is_active = False
            rule.breach_count = 0
            if rule.critical:
                rule.last_sent = None
            return

        if not is_breached:
            if not rule.is_active:
                rule.breach_count = 0
            return

        rule.breach_count += 1
        if rule.breach_count < self.config.alert_debounce_count:
            return

        now = self.clock()
        if rule.last_sent is not None and (now - rule.last_sent) < rule.cooldown_sec:
            return

        tag = "warning" if rule.critical else "lemon"
        if self._notify(rule.title, breach_msg, rule.priority, tag):
            rule.last_sent = now
            rule.is_active = True

    def evaluate(self, reading: Any) -> None:
        """Evaluate a reading against all rules."""
        with self._lock:
            temp_f = getattr(reading, "temp_f", None)
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

            # 2. temp_crit_low: breached at <= temp_min_crit_f;
            #    recovered at >= temp_min_crit_f + 2.0
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

            # 3. temp_crit_high: breached at >= temp_max_crit_f;
            #    recovered at <= temp_max_crit_f - 2.0
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
