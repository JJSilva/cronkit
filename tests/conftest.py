"""Shared fixtures: a tiny in-memory tool the framework tests can drive."""

from typing import Any

import pytest

from cronkit.core.errors import ConfigError
from cronkit.core.schedule import Schedule
from cronkit.core.tool import Tool, ToolResult


class FakeTool(Tool):
    """A tool that records its runs instead of talking to anything."""

    name = "fake"
    summary = "A tool that does nothing, quickly."
    env_prefix = "FAKE"

    #: Set by a test to make from_env() fail the way a missing credential would.
    unconfigured = False
    #: Set by a test to make run() raise.
    explode = False

    def __init__(self, **overrides: Any):
        self.overrides = overrides
        self.runs: list[bool] = []

    @classmethod
    def from_env(cls, **overrides: Any) -> "FakeTool":
        if cls.unconfigured:
            raise ConfigError("Missing required environment variable FAKE_TOKEN.")
        return cls(**overrides)

    async def run(self, *, dry_run: bool = False) -> ToolResult:
        self.runs.append(dry_run)
        if self.explode:
            raise RuntimeError("upstream said client_secret=abc123")
        return ToolResult(summary="did nothing", details={"items": ["one"]}, dry_run=dry_run)

    def status(self) -> dict[str, Any]:
        return {"mode": "fake"}


@pytest.fixture
def fake_tool_cls(monkeypatch):
    """A fresh FakeTool subclass, so per-test flags cannot leak."""

    class Fresh(FakeTool):
        pass

    monkeypatch.setenv("FAKE_INTERVAL_MINUTES", "60")
    return Fresh


@pytest.fixture
def hourly() -> Schedule:
    return Schedule(min_minutes=60, max_minutes=60)
