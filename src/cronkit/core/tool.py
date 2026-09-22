"""The contract every cronkit tool implements.

A *tool* is one self-contained job the daemon knows how to run on a schedule.
It owns its own configuration, its own upstream clients, and its own cadence;
the daemon only knows how to build it, run it, and report what happened.

Adding one means writing a :class:`Tool` subclass and registering it — see
``docs/adding-a-tool.md``.
"""

import argparse
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from cronkit.core.schedule import Schedule


@dataclass
class ToolResult:
    """The outcome of one run, in a shape both the CLI and the API can render.

    ``details`` must be JSON-serialisable and must not contain secrets or
    anything that would be unsafe to return over HTTP: it is included verbatim
    in ``/status`` and in the response to a triggered run.
    """

    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        """Whether the run completed without recording any per-item errors."""
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "summary": self.summary,
            "dry_run": self.dry_run,
            "errors": list(self.errors),
            "details": self.details,
        }


class Tool(ABC):
    """One scheduled job.

    Subclasses set the class-level metadata, implement :meth:`from_env` and
    :meth:`run`, and optionally add CLI flags via :meth:`add_arguments`.
    """

    #: Stable slug used on the CLI, in the API, and in ``CRONKIT_TOOLS``.
    name: ClassVar[str]

    #: One line shown by ``cronkit list``.
    summary: ClassVar[str]

    #: Environment-variable namespace, e.g. ``TP_CALENDAR`` gives
    #: ``TP_CALENDAR_INTERVAL_MINUTES`` and ``TP_CALENDAR_ENABLED``.
    env_prefix: ClassVar[str]

    #: Older namespaces still honoured, so a live deployment keeps working
    #: across a rename. Consulted in order, after :attr:`env_prefix`.
    legacy_env_prefixes: ClassVar[tuple[str, ...]] = ()

    #: Cadence used when the environment does not specify one.
    default_interval_minutes: ClassVar[float] = 60.0

    @classmethod
    def schedule_from_env(cls) -> Schedule:
        """Resolve this tool's cadence from the environment."""
        return Schedule.from_env(
            cls.env_prefix,
            default_minutes=cls.default_interval_minutes,
            legacy_prefixes=cls.legacy_env_prefixes,
        )

    @classmethod  # noqa: B027  (an optional hook, deliberately not abstract)
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Add per-tool flags to ``cronkit run <name>``.

        Whatever is parsed here is passed to :meth:`from_env` as keyword
        overrides. Most tools need none, so the default is to add nothing.
        """

    @classmethod
    @abstractmethod
    def from_env(cls, **overrides: Any) -> "Tool":
        """Build the tool from environment variables.

        Raises :class:`~cronkit.core.errors.ConfigError` when something required
        is missing; the daemon catches that and marks the tool unconfigured
        instead of failing to start.
        """

    @abstractmethod
    async def run(self, *, dry_run: bool = False) -> ToolResult:
        """Do the work once."""

    def status(self) -> dict[str, Any]:
        """Non-secret configuration to show in ``/status``.

        Anything returned here is served to an authorised caller, so it may
        include things like a target calendar id — but never a credential.
        """
        return {}
