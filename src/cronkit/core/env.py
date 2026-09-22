"""Reading configuration out of the environment.

Every accessor takes *one or more* variable names and uses the first one that is
set. That is what lets a tool advertise a namespaced name while still honouring
the name an existing deployment already has in its Railway variables::

    auth_cookie = env.require("TP_CALENDAR_AUTH_COOKIE", "TP_AUTH_COOKIE")

The daemon has no state on disk, so the environment is the whole configuration
surface; keeping the lookups in one place keeps the error messages consistent.
"""

import os
from collections.abc import Iterable

from cronkit.core.errors import ConfigError

TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


def _first(names: Iterable[str]) -> tuple[str, str] | None:
    """Return the (name, value) of the first variable that is set and non-blank."""
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return name, value
    return None


def optional(*names: str, default: str = "") -> str:
    found = _first(names)
    return found[1] if found else default


def require(*names: str) -> str:
    found = _first(names)
    if found:
        return found[1]
    primary = names[0]
    alternatives = "".join(f" (or {name})" for name in names[1:])
    raise ConfigError(
        f"Missing required environment variable {primary}{alternatives}. "
        f"See README.md for the full list and how to obtain each value."
    )


def flag(*names: str, default: bool) -> bool:
    found = _first(names)
    if not found:
        return default
    name, raw = found
    lowered = raw.lower()
    if lowered in TRUE_VALUES:
        return True
    if lowered in FALSE_VALUES:
        return False
    raise ConfigError(f"{name} must be true or false, got {raw!r}")


def number(*names: str, default: float) -> float:
    found = _first(names)
    if not found:
        return default
    name, raw = found
    try:
        return float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from None


def integer(*names: str, default: int) -> int:
    found = _first(names)
    if not found:
        return default
    name, raw = found
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from None


def csv_list(*names: str) -> list[str]:
    """Parse a comma-separated variable into a list of trimmed, non-empty items."""
    found = _first(names)
    if not found:
        return []
    return [item.strip() for item in found[1].split(",") if item.strip()]
