"""What day it is, for the athlete rather than the UTC container."""

from datetime import UTC, date, datetime

import pytest

from cronkit.core import clock
from cronkit.core.errors import ConfigError

# 06:30 UTC on the 23rd is still 23:30 on the 22nd in Los Angeles.
EVENING_IN_LA = datetime(2026, 9, 23, 6, 30, tzinfo=UTC)


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return EVENING_IN_LA.astimezone(tz) if tz else EVENING_IN_LA.replace(tzinfo=None)


@pytest.fixture
def evening_in_la(monkeypatch):
    monkeypatch.setattr(clock, "datetime", FrozenDatetime)


def test_today_is_the_athletes_day_not_utcs(evening_in_la):
    assert clock.today("America/Los_Angeles") == date(2026, 9, 22)


def test_no_timezone_means_the_hosts_day(evening_in_la):
    assert clock.today() == date(2026, 9, 23)


def test_a_tools_own_timezone_beats_the_shared_one(monkeypatch):
    monkeypatch.setenv("CRONKIT_TIMEZONE", "America/New_York")
    monkeypatch.setenv("MY_TOOL_TIMEZONE", "America/Los_Angeles")
    assert clock.timezone_from_env("MY_TOOL_TIMEZONE") == "America/Los_Angeles"


def test_the_shared_timezone_applies_when_a_tool_sets_none(monkeypatch):
    monkeypatch.delenv("MY_TOOL_TIMEZONE", raising=False)
    monkeypatch.setenv("CRONKIT_TIMEZONE", "America/Los_Angeles")
    assert clock.timezone_from_env("MY_TOOL_TIMEZONE") == "America/Los_Angeles"


def test_nothing_set_means_the_host_zone(monkeypatch):
    monkeypatch.delenv("MY_TOOL_TIMEZONE", raising=False)
    monkeypatch.delenv("CRONKIT_TIMEZONE", raising=False)
    assert clock.timezone_from_env("MY_TOOL_TIMEZONE") == ""


def test_an_unknown_zone_is_rejected_at_load(monkeypatch):
    monkeypatch.setenv("CRONKIT_TIMEZONE", "Mars/Olympus_Mons")
    with pytest.raises(ConfigError, match="CRONKIT_TIMEZONE"):
        clock.timezone_from_env("MY_TOOL_TIMEZONE")
