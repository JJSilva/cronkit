"""Configuration for the CORE body-temperature tool.

The TrainingPeaks credential is shared with the calendar tool, so the same
``TP_AUTH_COOKIE`` fallback chain applies: one cookie configures both tools, and
neither needs its own copy.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from cronkit.core import env
from cronkit.tools.trainingpeaks_core_temp.report import ReportOptions


@dataclass(frozen=True)
class CoreTempConfig:
    """Everything this tool needs."""

    # The Production_tpAuth cookie, shared with the calendar tool.
    tp_auth_cookie: str

    # How many days back from today to consider. 1 is today only; raise it so a
    # late-night session that uploads after midnight is still picked up.
    lookback_days: int = 1

    # Report the readings in Fahrenheit rather than the sensor's native Celsius.
    fahrenheit: bool = False

    # Core temperature at or above which time is counted as heat strain.
    threshold_c: float = 38.0

    # Bucket size for the downsampled table.
    interval_minutes: float = 5.0

    # Write the summary only, leaving out the per-interval table.
    summary_only: bool = False

    @property
    def report_options(self) -> ReportOptions:
        return ReportOptions(
            fahrenheit=self.fahrenheit,
            threshold_c=self.threshold_c,
            interval=timedelta(minutes=self.interval_minutes),
            include_series=not self.summary_only,
        )

    @classmethod
    def from_env(cls, **overrides: Any) -> "CoreTempConfig":
        values: dict[str, Any] = {
            "tp_auth_cookie": env.require("TP_CORE_AUTH_COOKIE", "TP_CALENDAR_AUTH_COOKIE", "TP_AUTH_COOKIE"),
            "lookback_days": env.integer("TP_CORE_LOOKBACK_DAYS", default=1),
            "fahrenheit": env.optional("TP_CORE_UNITS", default="C").upper().startswith("F"),
            "threshold_c": env.number("TP_CORE_THRESHOLD_C", default=38.0),
            "interval_minutes": env.number("TP_CORE_INTERVAL_MINUTES", default=5.0),
            "summary_only": env.flag("TP_CORE_SUMMARY_ONLY", default=False),
        }
        values.update({key: value for key, value in overrides.items() if value is not None})
        return cls(**values)

    def status(self) -> dict[str, Any]:
        """Non-secret settings, safe to serve over the API."""
        return {
            "lookback_days": self.lookback_days,
            "units": "F" if self.fahrenheit else "C",
            "threshold_c": self.threshold_c,
            "interval_minutes": self.interval_minutes,
            "summary_only": self.summary_only,
        }
