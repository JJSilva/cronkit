"""The scheduler: one independent loop per tool, plus the state they report.

Each tool gets its own task, its own cadence, and its own lock, so a slow or
failing tool cannot delay or break any other. A tool that fails to *configure*
is kept in the lineup as ``unconfigured`` rather than taking the daemon down —
with several tools sharing one deployment, one missing credential must not stop
the rest from running.
"""

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from cronkit.core import registry
from cronkit.core.config import DaemonConfig
from cronkit.core.errors import ConfigError, CronkitError
from cronkit.core.schedule import Schedule
from cronkit.core.tool import Tool, ToolResult

logger = logging.getLogger(__name__)


class ToolUnavailableError(CronkitError):
    """A tool was asked to run but never configured successfully."""


class ToolRunner:
    """Owns one tool's periodic task and the record of its most recent run."""

    def __init__(
        self,
        tool_cls: type[Tool],
        *,
        tool: Tool | None = None,
        schedule: Schedule | None = None,
        load_error: str | None = None,
    ):
        self.tool_cls = tool_cls
        self.name = tool_cls.name
        self.summary = tool_cls.summary
        self.tool = tool
        self.schedule = schedule
        self.load_error = load_error

        self.last_run_at: datetime | None = None
        self.last_result: ToolResult | None = None
        self.last_error: str | None = None
        self.next_run_at: datetime | None = None
        self.run_count = 0
        self.failure_count = 0

        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

    @classmethod
    def load(cls, tool_cls: type[Tool]) -> "ToolRunner":
        """Build a runner, capturing rather than raising configuration errors."""
        try:
            schedule = tool_cls.schedule_from_env()
            tool = tool_cls.from_env()
        except ConfigError as exc:
            logger.warning("Tool %s is unconfigured and will not run: %s", tool_cls.name, exc)
            return cls(tool_cls, load_error=str(exc))
        return cls(tool_cls, tool=tool, schedule=schedule)

    @property
    def available(self) -> bool:
        """Whether the tool configured successfully and can be run."""
        return self.tool is not None

    @property
    def scheduled(self) -> bool:
        """Whether the daemon will run this tool on a timer."""
        return self.available and self.schedule is not None and self.schedule.enabled

    @property
    def state(self) -> str:
        if not self.available:
            return "unconfigured"
        if not self.scheduled:
            return "manual"
        return "scheduled"

    async def run_once(self, *, dry_run: bool = False) -> ToolResult:
        """Run the tool once, serialized so overlapping triggers cannot interleave."""
        if self.tool is None:
            raise ToolUnavailableError(f"Tool {self.name!r} is not configured: {self.load_error}")

        async with self._lock:
            self.run_count += 1
            try:
                result = await self.tool.run(dry_run=dry_run)
            except Exception as exc:
                self.last_run_at = datetime.now(UTC)
                self.last_error = str(exc)
                self.failure_count += 1
                raise
            self.last_run_at = datetime.now(UTC)
            self.last_result = result
            self.last_error = None
            if result.errors:
                self.failure_count += 1
            logger.info("%s: %s", self.name, result.summary)
            return result

    async def _loop(self, *, run_on_start: bool) -> None:
        assert self.schedule is not None, "only scheduled runners start a loop"
        if not run_on_start:
            await self._wait()

        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Never let one bad run kill the loop; the next tick retries.
                logger.exception("Scheduled run of %s failed", self.name)
            await self._wait()

    async def _wait(self) -> None:
        """Sleep out one interval, recording when the next run is due."""
        assert self.schedule is not None
        delay = self.schedule.next_delay_seconds()
        self.next_run_at = datetime.now(UTC) + timedelta(seconds=delay)
        logger.info("%s: next run in %.1f minutes", self.name, delay / 60)
        await asyncio.sleep(delay)

    async def start(self, *, run_on_start: bool = True) -> None:
        if not self.scheduled or self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(run_on_start=run_on_start), name=f"cronkit:{self.name}")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    def status(self) -> dict[str, Any]:
        """Everything an authorised caller can see about this tool."""
        return {
            "name": self.name,
            "summary": self.summary,
            "state": self.state,
            "schedule": self.schedule.as_dict() if self.schedule else None,
            "config": self.tool.status() if self.tool else None,
            "load_error": self.load_error,
            "runs": self.run_count,
            "failures": self.failure_count,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
            "last_error": self.last_error,
            "last_result": self.last_result.as_dict() if self.last_result else None,
        }


class Daemon:
    """Holds every loaded tool and drives their schedules."""

    def __init__(self, config: DaemonConfig, runners: list[ToolRunner]):
        self.config = config
        self.runners = runners
        self._by_name = {runner.name: runner for runner in runners}
        self.started_at: datetime | None = None

    @classmethod
    def from_env(cls, config: DaemonConfig | None = None) -> "Daemon":
        """Load the configured subset of registered tools.

        An empty ``CRONKIT_TOOLS`` means every registered tool, which is what
        keeps a single-tool deployment zero-config.
        """
        config = config or DaemonConfig.from_env()
        classes = registry.select(config.tools) if config.tools else registry.all_tools()
        return cls(config, [ToolRunner.load(tool_cls) for tool_cls in classes])

    def runner(self, name: str) -> ToolRunner | None:
        return self._by_name.get(name)

    async def start(self) -> None:
        self.started_at = datetime.now(UTC)
        for runner in self.runners:
            await runner.start(run_on_start=self.config.run_on_start)
        scheduled = [r.name for r in self.runners if r.scheduled]
        logger.info(
            "cronkit started with %d tool(s); scheduled: %s",
            len(self.runners),
            ", ".join(scheduled) or "none",
        )

    async def stop(self) -> None:
        for runner in self.runners:
            await runner.stop()

    def status(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "tools": [runner.status() for runner in self.runners],
        }
