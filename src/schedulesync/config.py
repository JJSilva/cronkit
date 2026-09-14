"""Configuration, loaded from environment variables.

Everything the service needs to run unattended on Railway comes from env vars —
there is no state on disk, which is what lets the container be recreated freely.
"""

import os
from dataclasses import dataclass


class ConfigError(Exception):
    """Raised when required configuration is missing or malformed."""


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(
            f"Missing required environment variable {name}. "
            f"See README.md for the full list and how to obtain each value."
        )
    return value


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from None


@dataclass(frozen=True)
class Config:
    """Resolved runtime configuration."""

    # TrainingPeaks: the Production_tpAuth cookie value, exchanged for a bearer
    # token on each run. This is the same credential the trainingpeaks-mcp
    # deployment uses.
    tp_auth_cookie: str

    # Google OAuth (installed-app style refresh token for the target account).
    google_client_id: str
    google_client_secret: str
    google_refresh_token: str

    # Calendar to write to, e.g. "you@gmail.com".
    calendar_id: str

    # Rolling sync window: [today, today + sync_days].
    sync_days: int = 21

    # How often the long-running server re-syncs.
    interval_minutes: int = 60

    # IANA zone for interpreting TrainingPeaks' naive planned start times.
    # When empty, the target calendar's own timezone is used.
    timezone: str = ""

    # Remove previously-synced events whose workout no longer has a planned time
    # (or no longer exists) in TrainingPeaks.
    prune: bool = True

    # Shared secret guarding /status and /sync. A Railway service is public,
    # so without this those endpoints refuse every request.
    api_token: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            tp_auth_cookie=_require("TP_AUTH_COOKIE"),
            google_client_id=_require("GOOGLE_CLIENT_ID"),
            google_client_secret=_require("GOOGLE_CLIENT_SECRET"),
            google_refresh_token=_require("GOOGLE_REFRESH_TOKEN"),
            calendar_id=os.environ.get("CALENDAR_ID", "").strip() or "primary",
            sync_days=_int("SYNC_DAYS", 21),
            interval_minutes=_int("SYNC_INTERVAL_MINUTES", 60),
            timezone=os.environ.get("SYNC_TIMEZONE", "").strip(),
            prune=os.environ.get("SYNC_PRUNE", "true").strip().lower() != "false",
            api_token=os.environ.get("SYNC_API_TOKEN", "").strip(),
        )
