"""The scheduler: isolation between tools, and the state they report."""

import asyncio

import pytest

from cronkit.core.config import DaemonConfig
from cronkit.core.daemon import Daemon, ToolRunner, ToolUnavailableError
from cronkit.core.schedule import Schedule


def runner(tool_cls, **kwargs) -> ToolRunner:
    return ToolRunner(tool_cls, tool=tool_cls(), schedule=Schedule(min_minutes=60, max_minutes=60), **kwargs)


# --- loading ---------------------------------------------------------------


def test_a_configured_tool_loads_ready_to_run(fake_tool_cls):
    loaded = ToolRunner.load(fake_tool_cls)

    assert loaded.available
    assert loaded.scheduled
    assert loaded.state == "scheduled"


def test_an_unconfigured_tool_is_kept_but_not_scheduled(fake_tool_cls):
    """One missing credential must not stop the daemon's other tools."""
    fake_tool_cls.unconfigured = True
    loaded = ToolRunner.load(fake_tool_cls)

    assert not loaded.available
    assert loaded.state == "unconfigured"
    assert "FAKE_TOKEN" in loaded.load_error


def test_a_disabled_tool_loads_but_stays_manual(fake_tool_cls, monkeypatch):
    monkeypatch.setenv("FAKE_ENABLED", "false")
    loaded = ToolRunner.load(fake_tool_cls)

    assert loaded.available
    assert not loaded.scheduled
    assert loaded.state == "manual"


# --- running ---------------------------------------------------------------


async def test_running_records_the_result(fake_tool_cls):
    one = runner(fake_tool_cls)
    result = await one.run_once()

    assert result.summary == "did nothing"
    assert one.run_count == 1
    assert one.last_result is result
    assert one.last_error is None


async def test_dry_run_is_passed_through(fake_tool_cls):
    one = runner(fake_tool_cls)
    await one.run_once(dry_run=True)

    assert one.tool.runs == [True]


async def test_a_failure_is_recorded_and_re_raised(fake_tool_cls):
    fake_tool_cls.explode = True
    one = runner(fake_tool_cls)

    with pytest.raises(RuntimeError):
        await one.run_once()

    assert one.failure_count == 1
    assert one.last_error is not None
    assert one.last_run_at is not None


async def test_an_unconfigured_tool_refuses_to_run(fake_tool_cls):
    fake_tool_cls.unconfigured = True
    one = ToolRunner.load(fake_tool_cls)

    with pytest.raises(ToolUnavailableError):
        await one.run_once()


async def test_overlapping_runs_are_serialized(fake_tool_cls):
    """A triggered run landing mid-schedule must not interleave with it."""
    gate = asyncio.Event()
    concurrent = 0
    peak = 0

    class Slow(fake_tool_cls):
        async def run(self, *, dry_run: bool = False):
            nonlocal concurrent, peak
            concurrent += 1
            peak = max(peak, concurrent)
            await gate.wait()
            concurrent -= 1
            return await super().run(dry_run=dry_run)

    one = runner(Slow)
    tasks = [asyncio.create_task(one.run_once()) for _ in range(3)]
    await asyncio.sleep(0)
    gate.set()
    await asyncio.gather(*tasks)

    assert peak == 1


# --- the loop --------------------------------------------------------------


async def test_the_loop_runs_immediately_then_waits(fake_tool_cls):
    one = ToolRunner(fake_tool_cls, tool=fake_tool_cls(), schedule=Schedule(min_minutes=60, max_minutes=60))
    await one.start()
    await asyncio.sleep(0.05)
    await one.stop()

    assert one.run_count == 1
    assert one.next_run_at is not None


async def test_run_on_start_can_be_turned_off(fake_tool_cls):
    one = ToolRunner(fake_tool_cls, tool=fake_tool_cls(), schedule=Schedule(min_minutes=60, max_minutes=60))
    await one.start(run_on_start=False)
    await asyncio.sleep(0.05)
    await one.stop()

    assert one.run_count == 0
    assert one.next_run_at is not None


async def test_a_failing_run_does_not_kill_the_loop(fake_tool_cls):
    """The next tick has to retry; an expired cookie is a temporary condition."""
    fake_tool_cls.explode = True
    one = ToolRunner(fake_tool_cls, tool=fake_tool_cls(), schedule=Schedule(min_minutes=60, max_minutes=60))
    await one.start()
    await asyncio.sleep(0.05)

    assert one._task is not None and not one._task.done()
    await one.stop()


async def test_an_unscheduled_tool_starts_no_task(fake_tool_cls):
    schedule = Schedule(min_minutes=60, max_minutes=60, enabled=False)
    one = ToolRunner(fake_tool_cls, tool=fake_tool_cls(), schedule=schedule)
    await one.start()

    assert one._task is None


# --- the daemon ------------------------------------------------------------


def test_the_daemon_loads_every_registered_tool_by_default():
    daemon = Daemon.from_env(DaemonConfig())

    assert "trainingpeaks-calendar" in [r.name for r in daemon.runners]


def test_cronkit_tools_narrows_what_is_loaded():
    daemon = Daemon.from_env(DaemonConfig(tools=["trainingpeaks-calendar"]))

    assert [r.name for r in daemon.runners] == ["trainingpeaks-calendar"]


async def test_tools_run_independently_of_one_another(fake_tool_cls):
    """A tool that fails must not stop its neighbour from being scheduled."""

    class Broken(fake_tool_cls):
        name = "broken"
        explode = True

    good = ToolRunner(fake_tool_cls, tool=fake_tool_cls(), schedule=Schedule(min_minutes=60, max_minutes=60))
    bad = ToolRunner(Broken, tool=Broken(), schedule=Schedule(min_minutes=60, max_minutes=60))
    daemon = Daemon(DaemonConfig(), [good, bad])

    await daemon.start()
    await asyncio.sleep(0.05)
    await daemon.stop()

    assert good.run_count == 1 and good.last_error is None
    assert bad.failure_count == 1


def test_status_reports_every_tool(fake_tool_cls):
    daemon = Daemon(DaemonConfig(), [runner(fake_tool_cls)])
    status = daemon.status()

    assert [t["name"] for t in status["tools"]] == ["fake"]
    assert status["tools"][0]["schedule"]["label"] == "every 60 min"
    assert status["tools"][0]["config"] == {"mode": "fake"}
