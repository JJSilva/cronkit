"""Daemon-level configuration — the settings that are not any one tool's.

Everything comes from environment variables. There is no state on disk, which
is what lets the container be recreated freely.

Per-tool settings live with their tool; see
``cronkit.tools.trainingpeaks_calendar.config``.
"""

from dataclasses import dataclass, field

from cronkit.core import env


@dataclass(frozen=True)
class DaemonConfig:
    """Resolved configuration for the daemon itself."""

    #: Shared secret guarding every endpoint except ``/health``. A Railway
    #: service is public, so without this those endpoints refuse every request.
    api_token: str = ""

    #: Port the HTTP server binds. Railway sets ``PORT``.
    port: int = 8000

    #: Which tools to load. Empty means every registered tool.
    tools: list[str] = field(default_factory=list)

    #: Run each tool once at startup rather than waiting out the first interval.
    run_on_start: bool = True

    @classmethod
    def from_env(cls) -> "DaemonConfig":
        return cls(
            # SYNC_API_TOKEN is the pre-cronkit name, still honoured so a live
            # deployment does not lock itself out on upgrade.
            api_token=env.optional("CRONKIT_API_TOKEN", "SYNC_API_TOKEN"),
            port=env.integer("PORT", default=8000),
            tools=env.csv_list("CRONKIT_TOOLS"),
            run_on_start=env.flag("CRONKIT_RUN_ON_START", default=True),
        )
