"""Configuration for the Tymewear breathing tool.

The TrainingPeaks credential is shared with the other TrainingPeaks tools, so
the same ``TP_AUTH_COOKIE`` fallback chain applies.

The table's bucket size is ``TP_TYMEWEAR_TABLE_MINUTES``, not ``..._INTERVAL_MINUTES``:
the scheduler already reads ``<prefix>_INTERVAL_MINUTES`` as the tool's cadence.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from cronkit.core import clock, env
from cronkit.tools.trainingpeaks_tymewear.report import ReportOptions


@dataclass(frozen=True)
class TymewearConfig:
    """Everything this tool needs."""

    # The Production_tpAuth cookie, shared with the other TrainingPeaks tools.
    tp_auth_cookie: str

    # How many days back from today to consider. 1 is today only.
    lookback_days: int = 1

    # IANA zone that decides what "today" is; see cronkit.core.clock.
    timezone: str = ""

    # Bucket size for the downsampled table.
    table_minutes: float = 5.0

    # Write the summary only, leaving out the per-interval table.
    summary_only: bool = False

    @property
    def report_options(self) -> ReportOptions:
        return ReportOptions(
            interval=timedelta(minutes=self.table_minutes),
            include_series=not self.summary_only,
        )

    @classmethod
    def from_env(cls, **overrides: Any) -> "TymewearConfig":
        values: dict[str, Any] = {
            "tp_auth_cookie": env.require(
                "TP_TYMEWEAR_AUTH_COOKIE", "TP_CORE_AUTH_COOKIE", "TP_CALENDAR_AUTH_COOKIE", "TP_AUTH_COOKIE"
            ),
            "lookback_days": env.integer("TP_TYMEWEAR_LOOKBACK_DAYS", default=1),
            "timezone": clock.timezone_from_env("TP_TYMEWEAR_TIMEZONE"),
            "table_minutes": env.number("TP_TYMEWEAR_TABLE_MINUTES", default=5.0),
            "summary_only": env.flag("TP_TYMEWEAR_SUMMARY_ONLY", default=False),
        }
        values.update({key: value for key, value in overrides.items() if value is not None})
        return cls(**values)

    def status(self) -> dict[str, Any]:
        """Non-secret settings, safe to serve over the API."""
        return {
            "lookback_days": self.lookback_days,
            "timezone": self.timezone or "(host default)",
            "table_minutes": self.table_minutes,
            "summary_only": self.summary_only,
        }
