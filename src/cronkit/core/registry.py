"""The catalogue of known tools.

Tools register themselves at import time via :func:`register`; importing
:mod:`cronkit.tools` populates the registry. Keeping the lookup here — rather
than scattering imports through the CLI and the server — means both entry points
see exactly the same set.
"""

from cronkit.core.errors import ToolNotFoundError
from cronkit.core.tool import Tool

_REGISTRY: dict[str, type[Tool]] = {}
_loaded = False


def register(tool_cls: type[Tool]) -> type[Tool]:
    """Register a tool class. Usable as a decorator.

    Registration is keyed on :attr:`Tool.name`; re-registering the same class is
    a no-op so that a re-imported module does not blow up, but two *different*
    classes claiming one name is a programming error worth failing loudly on.
    """
    name = getattr(tool_cls, "name", "")
    if not name:
        raise ValueError(f"{tool_cls.__name__} must set a non-empty `name`.")
    existing = _REGISTRY.get(name)
    if existing is not None and existing is not tool_cls:
        raise ValueError(
            f"Two different tools are registered as {name!r}: "
            f"{existing.__name__} and {tool_cls.__name__}."
        )
    _REGISTRY[name] = tool_cls
    return tool_cls


def ensure_loaded() -> None:
    """Import the built-in tools, if that has not happened yet.

    Every lookup below goes through this, so neither the CLI nor the server has
    to remember to import :mod:`cronkit.tools` first. The import is deferred to
    here rather than done at module scope because ``cronkit.tools`` imports this
    module in turn.
    """
    global _loaded
    if _loaded:
        return
    _loaded = True
    import cronkit.tools  # noqa: F401  (import side effect: registration)


def all_tools() -> list[type[Tool]]:
    """Every registered tool class, in name order."""
    ensure_loaded()
    return [_REGISTRY[name] for name in sorted(_REGISTRY)]


def tool_names() -> list[str]:
    ensure_loaded()
    return sorted(_REGISTRY)


def get(name: str) -> type[Tool]:
    ensure_loaded()
    try:
        return _REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "none"
        raise ToolNotFoundError(f"No tool named {name!r}. Registered tools: {known}.") from None


def select(names: list[str]) -> list[type[Tool]]:
    """Resolve an explicit list of names, preserving the caller's order."""
    return [get(name) for name in names]
