"""The TrainingPeaks -> Google Calendar tool.

The first tool cronkit shipped with. The scheduling, HTTP surface, and status
reporting all live in :mod:`cronkit.core`; what is left here is the job itself.
"""

import argparse
from typing import Any

from cronkit.core.tool import Tool, ToolResult
from cronkit.tools.trainingpeaks_calendar.config import CalendarSyncConfig
from cronkit.tools.trainingpeaks_calendar.sync import run_sync


class TrainingPeaksCalendarTool(Tool):
    """Mirror timed TrainingPeaks workouts onto a Google Calendar."""

    name = "trainingpeaks-calendar"
    summary = "Sync timed TrainingPeaks workouts into Google Calendar."
    env_prefix = "TP_CALENDAR"
    # Everything was prefixed SYNC_ before cronkit existed; still honoured so a
    # live deployment keeps its cadence without being re-configured.
    legacy_env_prefixes = ("SYNC",)
    default_interval_minutes = 60.0

    def __init__(self, config: CalendarSyncConfig):
        self.config = config

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--days",
            dest="sync_days",
            type=int,
            default=None,
            help="Override how many days ahead to sync (default: TP_CALENDAR_DAYS, or 21).",
        )
        parser.add_argument(
            "--calendar",
            dest="calendar_id",
            default=None,
            help="Override the target calendar id.",
        )

    @classmethod
    def from_env(cls, **overrides: Any) -> "TrainingPeaksCalendarTool":
        return cls(CalendarSyncConfig.from_env(**overrides))

    async def run(self, *, dry_run: bool = False) -> ToolResult:
        result = await run_sync(self.config, dry_run=dry_run)
        return result.to_tool_result()

    def status(self) -> dict[str, Any]:
        return self.config.status()
