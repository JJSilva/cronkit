"""How often a tool runs.

Each tool owns its own cadence, so a cheap job can tick every few minutes while
an expensive one runs hourly. A schedule is a *range*: each wait is drawn fresh
from ``[min_minutes, max_minutes]``, which spreads requests out instead of
hitting an upstream API on an exact, fingerprintable rhythm. Setting both bounds
to the same value gives a fixed interval.
"""

import random
from dataclasses import dataclass

from cronkit.core import env
from cronkit.core.errors import ConfigError


@dataclass(frozen=True)
class Schedule:
    """A tool's run cadence."""

    min_minutes: float
    max_minutes: float
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.min_minutes <= 0 or self.max_minutes <= 0:
            raise ConfigError(
                f"Schedule bounds must be positive, got {self.min_minutes:g} and {self.max_minutes:g} minutes."
            )
        if self.min_minutes > self.max_minutes:
            raise ConfigError(
                f"Schedule minimum ({self.min_minutes:g} min) cannot exceed the maximum "
                f"({self.max_minutes:g} min)."
            )

    @property
    def label(self) -> str:
        """Human-readable description of the cadence."""
        if not self.enabled:
            return "disabled"
        if self.min_minutes == self.max_minutes:
            return f"every {self.min_minutes:g} min"
        return f"every {self.min_minutes:g}-{self.max_minutes:g} min (randomised)"

    def next_delay_seconds(self) -> float:
        """Seconds to wait before the next run.

        Drawn fresh for each wait, so the schedule does not settle into a fixed
        rhythm. Not security-sensitive, so the default RNG is fine.
        """
        return random.uniform(self.min_minutes, self.max_minutes) * 60

    def as_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "label": self.label,
            "min_minutes": self.min_minutes,
            "max_minutes": self.max_minutes,
        }

    @classmethod
    def from_env(
        cls,
        prefix: str,
        *,
        default_minutes: float,
        legacy_prefixes: tuple[str, ...] = (),
    ) -> "Schedule":
        """Build a schedule from ``<PREFIX>_INTERVAL_*`` variables.

        For prefix ``TP_CALENDAR`` that means:

        ``TP_CALENDAR_INTERVAL_MINUTES``
            Single value; supplies the default for both bounds.
        ``TP_CALENDAR_INTERVAL_MIN_MINUTES`` / ``..._MAX_MINUTES``
            Override either bound individually.
        ``TP_CALENDAR_ENABLED``
            Set false to register the tool but never schedule it.

        ``legacy_prefixes`` are consulted in order after the primary one, so a
        deployment that predates the rename keeps working untouched.
        """
        prefixes = (prefix, *legacy_prefixes)

        def names(suffix: str) -> tuple[str, ...]:
            return tuple(f"{p}_{suffix}" for p in prefixes)

        fixed = env.number(*names("INTERVAL_MINUTES"), default=default_minutes)
        return cls(
            min_minutes=env.number(*names("INTERVAL_MIN_MINUTES"), default=fixed),
            max_minutes=env.number(*names("INTERVAL_MAX_MINUTES"), default=fixed),
            enabled=env.flag(*names("ENABLED"), default=True),
        )
