"""Configuration for the CORE body-temperature tool.

The TrainingPeaks credential is shared with the calendar tool, so the same
``TP_AUTH_COOKIE`` fallback chain applies: one cookie configures both tools, and
neither needs its own copy.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from cronkit.core import env
from cronkit.tools.trainingpeaks_core_temp.fit import from_fahrenheit, to_fahrenheit
from cronkit.tools.trainingpeaks_core_temp.report import ReportOptions


@dataclass(frozen=True)
class CoreTempConfig:
    """Everything this tool needs."""

    # The Production_tpAuth cookie, shared with the calendar tool.
    tp_auth_cookie: str

    # How many days back from today to consider. 1 is today only; raise it so a
    # late-night session that uploads after midnight is still picked up.
    lookback_days: int = 1

    # Report the readings in Fahrenheit. The sensor records Celsius; set
    # TP_CORE_UNITS=C to see it unconverted.
    fahrenheit: bool = True

    # Core temperature at or above which time is counted as heat strain, held in
    # Celsius because that is what the sensor produces. It is *configured* in
    # whatever unit `fahrenheit` selects — see from_env.
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
        # The threshold is configured in the display unit, so that a Fahrenheit
        # deployment never has to think in Celsius. TP_CORE_THRESHOLD_C remains
        # available for anyone who would rather be explicit.
        fahrenheit = env.optional("TP_CORE_UNITS", default="F").upper().startswith("F")
        default_threshold = 100.4 if fahrenheit else 38.0
        threshold = env.number("TP_CORE_THRESHOLD", default=default_threshold)
        threshold_c = from_fahrenheit(threshold) if fahrenheit else threshold
        threshold_c = env.number("TP_CORE_THRESHOLD_C", default=threshold_c)

        values: dict[str, Any] = {
            "tp_auth_cookie": env.require("TP_CORE_AUTH_COOKIE", "TP_CALENDAR_AUTH_COOKIE", "TP_AUTH_COOKIE"),
            "lookback_days": env.integer("TP_CORE_LOOKBACK_DAYS", default=1),
            "fahrenheit": fahrenheit,
            "threshold_c": threshold_c,
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
            "threshold": round(to_fahrenheit(self.threshold_c), 1) if self.fahrenheit else self.threshold_c,
            "interval_minutes": self.interval_minutes,
            "summary_only": self.summary_only,
        }
