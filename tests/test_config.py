"""Configuration parsing, with emphasis on the sync cadence."""

import pytest

from schedulesync.config import Config, ConfigError

REQUIRED = {
    "TP_AUTH_COOKIE": "cookie",
    "GOOGLE_CLIENT_ID": "id",
    "GOOGLE_CLIENT_SECRET": "secret",
    "GOOGLE_REFRESH_TOKEN": "refresh",
}

INTERVAL_VARS = [
    "SYNC_INTERVAL_MINUTES",
    "SYNC_INTERVAL_MIN_MINUTES",
    "SYNC_INTERVAL_MAX_MINUTES",
]


@pytest.fixture
def env(monkeypatch):
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)
    for key in INTERVAL_VARS:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


# --- the randomised interval ----------------------------------------------


def test_a_range_produces_delays_inside_the_bounds(env):
    env.setenv("SYNC_INTERVAL_MIN_MINUTES", "5")
    env.setenv("SYNC_INTERVAL_MAX_MINUTES", "15")
    config = Config.from_env()

    delays = [config.next_interval_seconds() for _ in range(200)]
    assert all(5 * 60 <= d <= 15 * 60 for d in delays)


def test_a_range_actually_varies(env):
    """A fixed cadence would defeat the point of configuring a range."""
    env.setenv("SYNC_INTERVAL_MIN_MINUTES", "5")
    env.setenv("SYNC_INTERVAL_MAX_MINUTES", "15")
    config = Config.from_env()

    assert len({config.next_interval_seconds() for _ in range(50)}) > 1


def test_equal_bounds_give_a_fixed_interval(env):
    env.setenv("SYNC_INTERVAL_MIN_MINUTES", "10")
    env.setenv("SYNC_INTERVAL_MAX_MINUTES", "10")
    config = Config.from_env()

    assert {config.next_interval_seconds() for _ in range(10)} == {600.0}


def test_the_single_value_form_still_works(env):
    """Existing deployments set only SYNC_INTERVAL_MINUTES."""
    env.setenv("SYNC_INTERVAL_MINUTES", "30")
    config = Config.from_env()

    assert config.interval_min_minutes == 30
    assert config.interval_max_minutes == 30
    assert config.next_interval_seconds() == 1800.0


def test_one_bound_overrides_the_single_value(env):
    env.setenv("SYNC_INTERVAL_MINUTES", "10")
    env.setenv("SYNC_INTERVAL_MAX_MINUTES", "20")
    config = Config.from_env()

    assert config.interval_min_minutes == 10
    assert config.interval_max_minutes == 20


def test_default_is_hourly(env):
    config = Config.from_env()
    assert config.next_interval_seconds() == 3600.0


# --- rejected configurations ----------------------------------------------


def test_inverted_bounds_are_rejected(env):
    env.setenv("SYNC_INTERVAL_MIN_MINUTES", "15")
    env.setenv("SYNC_INTERVAL_MAX_MINUTES", "5")
    with pytest.raises(ConfigError, match="cannot exceed"):
        Config.from_env()


@pytest.mark.parametrize("value", ["0", "-5"])
def test_non_positive_intervals_are_rejected(env, value):
    env.setenv("SYNC_INTERVAL_MIN_MINUTES", value)
    with pytest.raises(ConfigError, match="positive"):
        Config.from_env()


def test_a_non_numeric_interval_is_rejected(env):
    env.setenv("SYNC_INTERVAL_MIN_MINUTES", "soon")
    with pytest.raises(ConfigError, match="must be a number"):
        Config.from_env()


def test_missing_credentials_are_reported_by_name(env):
    env.delenv("TP_AUTH_COOKIE")
    with pytest.raises(ConfigError, match="TP_AUTH_COOKIE"):
        Config.from_env()


# --- reporting -------------------------------------------------------------


def test_label_describes_a_range(env):
    env.setenv("SYNC_INTERVAL_MIN_MINUTES", "5")
    env.setenv("SYNC_INTERVAL_MAX_MINUTES", "15")
    assert Config.from_env().interval_label == "every 5-15 min (randomised)"


def test_label_describes_a_fixed_interval(env):
    env.setenv("SYNC_INTERVAL_MINUTES", "10")
    assert Config.from_env().interval_label == "every 10 min"
