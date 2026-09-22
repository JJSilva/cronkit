"""The calendar tool's own configuration."""

import pytest

from cronkit.core.errors import ConfigError
from cronkit.tools.trainingpeaks_calendar.config import CalendarSyncConfig

NAMESPACED = {
    "TP_CALENDAR_AUTH_COOKIE": "cookie",
    "TP_CALENDAR_GOOGLE_CLIENT_ID": "id",
    "TP_CALENDAR_GOOGLE_CLIENT_SECRET": "secret",
    "TP_CALENDAR_GOOGLE_REFRESH_TOKEN": "refresh",
}

LEGACY = {
    "TP_AUTH_COOKIE": "cookie",
    "GOOGLE_CLIENT_ID": "id",
    "GOOGLE_CLIENT_SECRET": "secret",
    "GOOGLE_REFRESH_TOKEN": "refresh",
}

OPTIONAL = [
    "TP_CALENDAR_GOOGLE_CALENDAR_ID",
    "CALENDAR_ID",
    "TP_CALENDAR_DAYS",
    "SYNC_DAYS",
    "TP_CALENDAR_TIMEZONE",
    "SYNC_TIMEZONE",
    "TP_CALENDAR_PRUNE",
    "SYNC_PRUNE",
]


@pytest.fixture
def env(monkeypatch):
    for key in (*NAMESPACED, *LEGACY, *OPTIONAL):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def set_all(env, values):
    for key, value in values.items():
        env.setenv(key, value)


# --- credentials -----------------------------------------------------------


def test_the_namespaced_names_configure_the_tool(env):
    set_all(env, NAMESPACED)
    config = CalendarSyncConfig.from_env()

    assert config.tp_auth_cookie == "cookie"
    assert config.google_refresh_token == "refresh"


def test_the_pre_cronkit_names_still_configure_the_tool(env):
    """The live Railway deployment sets these; the refactor must not break it."""
    set_all(env, LEGACY)
    config = CalendarSyncConfig.from_env()

    assert config.tp_auth_cookie == "cookie"
    assert config.google_client_id == "id"


def test_the_namespaced_name_wins_over_the_legacy_one(env):
    set_all(env, LEGACY)
    env.setenv("TP_CALENDAR_AUTH_COOKIE", "newer")

    assert CalendarSyncConfig.from_env().tp_auth_cookie == "newer"


def test_missing_credentials_are_reported_by_name(env):
    set_all(env, NAMESPACED)
    env.delenv("TP_CALENDAR_AUTH_COOKIE")
    with pytest.raises(ConfigError, match="TP_CALENDAR_AUTH_COOKIE"):
        CalendarSyncConfig.from_env()


def test_the_missing_credential_error_also_names_the_legacy_variable(env):
    """An operator may only have heard of the old name."""
    set_all(env, NAMESPACED)
    env.delenv("TP_CALENDAR_AUTH_COOKIE")
    with pytest.raises(ConfigError, match="TP_AUTH_COOKIE"):
        CalendarSyncConfig.from_env()


# --- behaviour settings ----------------------------------------------------


def test_defaults_are_applied(env):
    set_all(env, NAMESPACED)
    config = CalendarSyncConfig.from_env()

    assert config.calendar_id == "primary"
    assert config.sync_days == 21
    assert config.prune is True
    assert config.timezone == ""


@pytest.mark.parametrize("name", ["TP_CALENDAR_DAYS", "SYNC_DAYS"])
def test_the_window_is_configurable_under_either_name(env, name):
    set_all(env, NAMESPACED)
    env.setenv(name, "45")

    assert CalendarSyncConfig.from_env().sync_days == 45


@pytest.mark.parametrize("name", ["TP_CALENDAR_PRUNE", "SYNC_PRUNE"])
def test_pruning_can_be_turned_off_under_either_name(env, name):
    set_all(env, NAMESPACED)
    env.setenv(name, "false")

    assert CalendarSyncConfig.from_env().prune is False


# --- CLI overrides ---------------------------------------------------------


def test_an_override_beats_the_environment(env):
    set_all(env, NAMESPACED)
    env.setenv("TP_CALENDAR_DAYS", "21")

    assert CalendarSyncConfig.from_env(sync_days=60).sync_days == 60


def test_an_absent_override_is_ignored(env):
    """CLI flags default to None and are passed through unconditionally."""
    set_all(env, NAMESPACED)
    env.setenv("TP_CALENDAR_DAYS", "21")

    assert CalendarSyncConfig.from_env(sync_days=None, calendar_id=None).sync_days == 21


# --- what the API is allowed to see ---------------------------------------


def test_status_shows_settings_but_never_credentials(env):
    set_all(env, NAMESPACED)
    status = CalendarSyncConfig.from_env().status()

    assert status["sync_days"] == 21
    assert "cookie" not in str(status)
    assert "refresh" not in str(status)
