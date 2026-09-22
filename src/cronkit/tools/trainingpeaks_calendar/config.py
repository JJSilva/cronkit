"""Configuration for the TrainingPeaks -> Google Calendar tool.

Variables are namespaced under ``TP_CALENDAR_`` so that tools added later cannot
collide with them. The original un-namespaced names are still accepted as
fallbacks, so the running Railway deployment keeps working across this refactor
without touching its variables.

The cadence is not here — that is :class:`cronkit.core.schedule.Schedule`,
resolved from ``TP_CALENDAR_INTERVAL_*`` (or the legacy ``SYNC_INTERVAL_*``).
"""

from dataclasses import dataclass
from typing import Any

from cronkit.core import env


@dataclass(frozen=True)
class CalendarSyncConfig:
    """Everything this tool needs to talk to TrainingPeaks and Google."""

    # TrainingPeaks: the Production_tpAuth cookie value, exchanged for a bearer
    # token on each run. This is the same credential the trainingpeaks-mcp
    # deployment uses.
    tp_auth_cookie: str

    # Google OAuth (installed-app style refresh token for the target account).
    google_client_id: str
    google_client_secret: str
    google_refresh_token: str

    # Calendar to write to, e.g. "you@gmail.com".
    calendar_id: str = "primary"

    # Rolling sync window: [today, today + sync_days].
    sync_days: int = 21

    # IANA zone for interpreting TrainingPeaks' naive planned start times.
    # When empty, the target calendar's own timezone is used.
    timezone: str = ""

    # Remove previously-synced events whose workout no longer has a planned time
    # (or no longer exists) in TrainingPeaks.
    prune: bool = True

    @classmethod
    def from_env(cls, **overrides: Any) -> "CalendarSyncConfig":
        """Read the configuration, letting explicit ``overrides`` win.

        Overrides come from CLI flags such as ``--days``; a ``None`` is treated
        as "not given" so the caller can pass flags through unconditionally.
        """
        values: dict[str, Any] = {
            "tp_auth_cookie": env.require("TP_CALENDAR_AUTH_COOKIE", "TP_AUTH_COOKIE"),
            "google_client_id": env.require("TP_CALENDAR_GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_ID"),
            "google_client_secret": env.require("TP_CALENDAR_GOOGLE_CLIENT_SECRET", "GOOGLE_CLIENT_SECRET"),
            "google_refresh_token": env.require("TP_CALENDAR_GOOGLE_REFRESH_TOKEN", "GOOGLE_REFRESH_TOKEN"),
            "calendar_id": env.optional("TP_CALENDAR_GOOGLE_CALENDAR_ID", "CALENDAR_ID", default="primary"),
            "sync_days": env.integer("TP_CALENDAR_DAYS", "SYNC_DAYS", default=21),
            "timezone": env.optional("TP_CALENDAR_TIMEZONE", "SYNC_TIMEZONE"),
            "prune": env.flag("TP_CALENDAR_PRUNE", "SYNC_PRUNE", default=True),
        }
        values.update({key: value for key, value in overrides.items() if value is not None})
        return cls(**values)

    def status(self) -> dict[str, Any]:
        """Non-secret settings, safe to serve over the API."""
        return {
            "calendar_id": self.calendar_id,
            "sync_days": self.sync_days,
            "timezone": self.timezone or "(calendar default)",
            "prune": self.prune,
        }
