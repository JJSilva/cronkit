"""Configuration for the Strava rename tool.

The TrainingPeaks credential is shared with the other TrainingPeaks tools, so the
same ``TP_AUTH_COOKIE`` fallback chain applies. The Strava credentials are this
tool's own.
"""

import re
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

from cronkit.core import clock, env
from cronkit.core.errors import ConfigError


@dataclass(frozen=True)
class StravaRenameConfig:
    """Everything this tool needs."""

    tp_auth_cookie: str
    strava_client_id: str
    strava_client_secret: str
    strava_refresh_token: str

    # How many days back from today to consider. 1 is today only.
    lookback_days: int = 1

    # IANA zone that decides what "today" is; see cronkit.core.clock. Blank
    # means the host's own zone, which on Railway is UTC.
    timezone: str = ""

    # How far apart two start times may be and still be the same session.
    tolerance_minutes: float = 2.0

    # Further regexes for names to treat as default, beyond the built-in list.
    extra_default_patterns: tuple[re.Pattern[str], ...] = field(default=())

    @property
    def zone(self) -> ZoneInfo | None:
        return ZoneInfo(self.timezone) if self.timezone else None

    @classmethod
    def from_env(cls, **overrides: Any) -> "StravaRenameConfig":
        raw_patterns = env.optional("STRAVA_RENAME_DEFAULT_NAME_PATTERNS")
        try:
            patterns = tuple(re.compile(p.strip(), re.IGNORECASE) for p in raw_patterns.split(";;") if p.strip())
        except re.error as exc:
            raise ConfigError(f"STRAVA_RENAME_DEFAULT_NAME_PATTERNS: invalid regex: {exc}") from exc

        values: dict[str, Any] = {
            "tp_auth_cookie": env.require(
                "STRAVA_RENAME_TP_AUTH_COOKIE", "TP_CORE_AUTH_COOKIE", "TP_CALENDAR_AUTH_COOKIE", "TP_AUTH_COOKIE"
            ),
            "strava_client_id": env.require("STRAVA_RENAME_CLIENT_ID", "STRAVA_CLIENT_ID"),
            "strava_client_secret": env.require("STRAVA_RENAME_CLIENT_SECRET", "STRAVA_CLIENT_SECRET"),
            "strava_refresh_token": env.require("STRAVA_RENAME_REFRESH_TOKEN", "STRAVA_REFRESH_TOKEN"),
            "lookback_days": env.integer("STRAVA_RENAME_LOOKBACK_DAYS", default=1),
            "timezone": clock.timezone_from_env("STRAVA_RENAME_TIMEZONE"),
            "tolerance_minutes": env.number("STRAVA_RENAME_TOLERANCE_MINUTES", default=2.0),
            "extra_default_patterns": patterns,
        }
        values.update({key: value for key, value in overrides.items() if value is not None})
        return cls(**values)

    def status(self) -> dict[str, Any]:
        """Non-secret settings, safe to serve over the API."""
        return {
            "lookback_days": self.lookback_days,
            "timezone": self.timezone or "(host default)",
            "tolerance_minutes": self.tolerance_minutes,
            "extra_default_patterns": [p.pattern for p in self.extra_default_patterns],
        }
