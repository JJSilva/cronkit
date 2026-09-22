"""What day it is — for the athlete, not the container.

The container runs in UTC, where "today" rolls over at 5pm Pacific. A tool that
windows its work by day must ask in the athlete's zone, or an evening session
falls out of today's window before it has been handled.

``CRONKIT_TIMEZONE`` sets that zone for every tool at once; a tool's own
``<PREFIX>_TIMEZONE`` overrides it.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cronkit.core import env
from cronkit.core.errors import ConfigError

SHARED_TIMEZONE_VAR = "CRONKIT_TIMEZONE"


def timezone_from_env(*names: str) -> str:
    """The first of ``names`` (then ``CRONKIT_TIMEZONE``) that is set, validated.

    Returns an empty string when none is set, meaning the host's own zone. An
    unknown zone is a :class:`ConfigError` at load, not a surprise at run time.
    """
    for name in (*names, SHARED_TIMEZONE_VAR):
        value = env.optional(name)
        if value:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ConfigError(f"{name}: unknown timezone {value!r}") from exc
            return value
    return ""


def today(timezone: str = "") -> date:
    """Today's date in ``timezone``, or in the host's zone when it is empty."""
    return datetime.now(ZoneInfo(timezone) if timezone else None).date()
