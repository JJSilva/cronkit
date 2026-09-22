"""Per-tool cadence: the randomised interval, and the env vars that set it."""

import pytest

from cronkit.core.errors import ConfigError
from cronkit.core.schedule import Schedule

PREFIX = "TP_CALENDAR"
LEGACY = ("SYNC",)

INTERVAL_VARS = [
    f"{p}_{suffix}"
    for p in (PREFIX, *LEGACY)
    for suffix in ("INTERVAL_MINUTES", "INTERVAL_MIN_MINUTES", "INTERVAL_MAX_MINUTES", "ENABLED")
]


@pytest.fixture
def env(monkeypatch):
    for key in INTERVAL_VARS:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def from_env() -> Schedule:
    return Schedule.from_env(PREFIX, default_minutes=60.0, legacy_prefixes=LEGACY)


# --- the randomised interval ----------------------------------------------


def test_a_range_produces_delays_inside_the_bounds():
    schedule = Schedule(min_minutes=5, max_minutes=15)
    delays = [schedule.next_delay_seconds() for _ in range(200)]
    assert all(5 * 60 <= d <= 15 * 60 for d in delays)


def test_a_range_actually_varies():
    """A fixed cadence would defeat the point of configuring a range."""
    schedule = Schedule(min_minutes=5, max_minutes=15)
    assert len({schedule.next_delay_seconds() for _ in range(50)}) > 1


def test_equal_bounds_give_a_fixed_interval():
    schedule = Schedule(min_minutes=10, max_minutes=10)
    assert {schedule.next_delay_seconds() for _ in range(10)} == {600.0}


# --- reading the environment ----------------------------------------------


def test_a_range_is_read_from_the_namespaced_vars(env):
    env.setenv("TP_CALENDAR_INTERVAL_MIN_MINUTES", "5")
    env.setenv("TP_CALENDAR_INTERVAL_MAX_MINUTES", "15")
    schedule = from_env()

    assert (schedule.min_minutes, schedule.max_minutes) == (5, 15)


def test_the_single_value_form_sets_both_bounds(env):
    env.setenv("TP_CALENDAR_INTERVAL_MINUTES", "30")
    schedule = from_env()

    assert schedule.min_minutes == schedule.max_minutes == 30
    assert schedule.next_delay_seconds() == 1800.0


def test_one_bound_overrides_the_single_value(env):
    env.setenv("TP_CALENDAR_INTERVAL_MINUTES", "10")
    env.setenv("TP_CALENDAR_INTERVAL_MAX_MINUTES", "20")
    schedule = from_env()

    assert (schedule.min_minutes, schedule.max_minutes) == (10, 20)


def test_the_default_applies_when_nothing_is_set(env):
    assert from_env().next_delay_seconds() == 3600.0


def test_a_tool_can_be_registered_but_left_unscheduled(env):
    env.setenv("TP_CALENDAR_ENABLED", "false")
    schedule = from_env()

    assert schedule.enabled is False
    assert schedule.label == "disabled"


# --- backwards compatibility ----------------------------------------------


def test_the_pre_cronkit_var_names_still_work(env):
    """A deployment that predates the rename must keep its cadence untouched."""
    env.setenv("SYNC_INTERVAL_MIN_MINUTES", "5")
    env.setenv("SYNC_INTERVAL_MAX_MINUTES", "15")
    schedule = from_env()

    assert (schedule.min_minutes, schedule.max_minutes) == (5, 15)


def test_the_namespaced_var_wins_over_the_legacy_one(env):
    env.setenv("SYNC_INTERVAL_MINUTES", "60")
    env.setenv("TP_CALENDAR_INTERVAL_MINUTES", "5")

    assert from_env().min_minutes == 5


# --- rejected configurations ----------------------------------------------


def test_inverted_bounds_are_rejected():
    with pytest.raises(ConfigError, match="cannot exceed"):
        Schedule(min_minutes=15, max_minutes=5)


@pytest.mark.parametrize("value", [0, -5])
def test_non_positive_intervals_are_rejected(value):
    with pytest.raises(ConfigError, match="positive"):
        Schedule(min_minutes=value, max_minutes=60)


def test_a_non_numeric_interval_is_rejected(env):
    env.setenv("TP_CALENDAR_INTERVAL_MIN_MINUTES", "soon")
    with pytest.raises(ConfigError, match="must be a number"):
        from_env()


# --- reporting -------------------------------------------------------------


def test_label_describes_a_range():
    assert Schedule(min_minutes=5, max_minutes=15).label == "every 5-15 min (randomised)"


def test_label_describes_a_fixed_interval():
    assert Schedule(min_minutes=10, max_minutes=10).label == "every 10 min"
