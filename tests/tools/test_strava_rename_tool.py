"""The run: which activities get renamed, and which are left alone."""

from datetime import date, datetime

import httpx
import pytest

from cronkit.core.errors import ConfigError
from cronkit.integrations.trainingpeaks import Workout
from cronkit.tools.strava_rename import tool as tool_module
from cronkit.tools.strava_rename.config import StravaRenameConfig
from cronkit.tools.strava_rename.strava import Activity, StravaAuthError, StravaClient
from cronkit.tools.strava_rename.tool import StravaRenameTool

TODAY = date(2026, 9, 22)
START = datetime(2026, 9, 22, 5, 41, 37)


def workout(**overrides) -> Workout:
    base = {
        "id": "100",
        "day": TODAY,
        "title": "Longish Run",
        "description": None,
        "sport": "Run",
        "planned_start": None,
        "planned_hours": 1.5,
        "actual_start": START,
        "actual_hours": 1.4,
    }
    base.update(overrides)
    return Workout(**base)


def activity(**overrides) -> Activity:
    base = {"id": "a1", "name": "Morning Run", "sport_type": "Run", "start_local": START}
    base.update(overrides)
    return Activity(**base)


class FakeTP:
    def __init__(self, workouts):
        self._workouts = workouts

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def workouts(self, start, end):
        return list(self._workouts)


class FakeStrava:
    def __init__(self, activities, *, refresh_token="rt"):
        self._activities = activities
        self.renames: list[tuple[str, str]] = []
        self.listed = 0
        self.refresh_token = refresh_token
        self.fail_on: set[str] = set()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def activities(self, after, before):
        self.listed += 1
        return list(self._activities)

    async def rename(self, activity_id, name):
        if activity_id in self.fail_on:
            raise RuntimeError("upstream broke")
        self.renames.append((activity_id, name))


def make_tool(**overrides) -> StravaRenameTool:
    base = {
        "tp_auth_cookie": "cookie",
        "strava_client_id": "1",
        "strava_client_secret": "secret",
        "strava_refresh_token": "rt",
    }
    base.update(overrides)
    tool = StravaRenameTool(StravaRenameConfig(**base))
    tool._today = lambda: TODAY
    return tool


def wire(monkeypatch, workouts, strava):
    monkeypatch.setattr(tool_module, "TrainingPeaksClient", lambda *a, **k: FakeTP(workouts))
    monkeypatch.setattr(tool_module, "StravaClient", lambda *a, **k: strava)


# --- the happy path --------------------------------------------------------


async def test_a_default_named_activity_takes_the_workout_title(monkeypatch):
    strava = FakeStrava([activity()])
    wire(monkeypatch, [workout()], strava)

    result = await make_tool().run()

    assert result.ok
    assert strava.renames == [("a1", "Longish Run")]
    assert result.details["renamed"] == ["2026-09-22 'Morning Run' -> 'Longish Run'"]


async def test_the_sport_is_appended_to_a_title_that_lacks_it(monkeypatch):
    strava = FakeStrava([activity(name="Morning Ride", sport_type="Ride")])
    wire(monkeypatch, [workout(title="Tempo", sport="Bike")], strava)

    result = await make_tool().run()

    assert strava.renames == [("a1", "Tempo Ride")]


async def test_an_activity_already_carrying_the_suffixed_name_is_left_alone(monkeypatch):
    strava = FakeStrava([activity(name="Masters Swim", sport_type="Swim")])
    wire(monkeypatch, [workout(title="Masters", sport="Swim")], strava)

    result = await make_tool().run()

    assert strava.renames == []
    assert result.details["already_named"] == ["2026-09-22 Masters Swim"]


# --- what is left alone ----------------------------------------------------


async def test_a_name_chosen_by_hand_is_never_overwritten(monkeypatch):
    strava = FakeStrava([activity(name="Medium Aerobic Ride", sport_type="Ride")])
    wire(monkeypatch, [workout(title="MA", sport="Bike")], strava)

    result = await make_tool().run()

    assert strava.renames == []
    assert result.details["custom_name_kept"] == ["2026-09-22 Medium Aerobic Ride"]


async def test_an_already_renamed_activity_is_not_renamed_again(monkeypatch):
    strava = FakeStrava([activity(name="Longish Run")])
    wire(monkeypatch, [workout()], strava)

    result = await make_tool().run()

    assert strava.renames == []
    assert result.details["already_named"] == ["2026-09-22 Longish Run"]


async def test_an_unplanned_uploads_generic_title_is_not_copied(monkeypatch):
    """TrainingPeaks titles an unplanned upload "Running" — no better than "Morning Run"."""
    strava = FakeStrava([activity()])
    wire(monkeypatch, [workout(title="Running", planned_hours=None)], strava)

    result = await make_tool().run()

    assert strava.renames == []
    assert result.details["generic_tp_title"] == ["2026-09-22 Running"]


async def test_a_workout_with_no_strava_counterpart_is_reported(monkeypatch):
    strava = FakeStrava([])
    wire(monkeypatch, [workout()], strava)

    result = await make_tool().run()

    assert result.details["no_strava_match"] == ["2026-09-22 Longish Run"]


async def test_planned_only_workouts_do_not_even_query_strava(monkeypatch):
    strava = FakeStrava([activity()])
    wire(monkeypatch, [workout(actual_start=None, actual_hours=None)], strava)

    result = await make_tool().run()

    assert result.details["completed_workouts"] == 0
    assert strava.listed == 0


# --- failures and dry run --------------------------------------------------


async def test_one_failing_rename_does_not_stop_the_others(monkeypatch):
    later = datetime(2026, 9, 22, 12, 0)
    strava = FakeStrava([activity(id="a1"), activity(id="a2", name="Lunch Swim", sport_type="Swim", start_local=later)])
    strava.fail_on = {"a1"}
    wire(monkeypatch, [workout(), workout(id="200", title="Masters", sport="Swim", actual_start=later)], strava)

    result = await make_tool().run()

    assert not result.ok
    assert len(result.errors) == 1
    assert strava.renames == [("a2", "Masters Swim")]


async def test_a_dry_run_reports_without_renaming(monkeypatch):
    strava = FakeStrava([activity()])
    wire(monkeypatch, [workout()], strava)

    result = await make_tool().run(dry_run=True)

    assert result.dry_run
    assert result.details["renamed"] == ["2026-09-22 'Morning Run' -> 'Longish Run'"]
    assert strava.renames == []


async def test_a_rotated_refresh_token_is_carried_to_the_next_run(monkeypatch):
    seen: list[str] = []
    strava = FakeStrava([activity(name="Longish Run")], refresh_token="rotated")
    monkeypatch.setattr(tool_module, "TrainingPeaksClient", lambda *a, **k: FakeTP([workout()]))

    def build(client_id, secret, refresh_token):
        seen.append(refresh_token)
        return strava

    monkeypatch.setattr(tool_module, "StravaClient", build)
    rename_tool = make_tool()

    await rename_tool.run()
    await rename_tool.run()

    assert seen == ["rt", "rotated"]


# --- the Strava client -----------------------------------------------------


def _transport(handler):
    return httpx.MockTransport(handler)


async def test_the_client_refreshes_then_sends_only_the_name():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "at", "refresh_token": "new", "expires_at": 9e9})
        return httpx.Response(200, json={})

    async with StravaClient("1", "s", "old", transport=_transport(handler)) as client:
        await client.rename("42", "Longish Run")
        assert client.refresh_token == "new"

    put = requests[-1]
    assert put.method == "PUT"
    assert put.url.path == "/api/v3/activities/42"
    assert put.headers["Authorization"] == "Bearer at"
    assert put.content == b'{"name":"Longish Run"}'


async def test_a_rejected_refresh_token_is_an_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "Bad Request"})

    async with StravaClient("1", "s", "old", transport=_transport(handler)) as client:
        with pytest.raises(StravaAuthError):
            await client.activities(datetime(2026, 9, 21).astimezone(), datetime(2026, 9, 23).astimezone())


async def test_a_missing_write_scope_is_an_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "at", "expires_at": 9e9})
        return httpx.Response(401, json={"message": "Authorization Error"})

    async with StravaClient("1", "s", "rt", transport=_transport(handler)) as client:
        with pytest.raises(StravaAuthError, match="activity:write"):
            await client.rename("42", "x")


# --- configuration ---------------------------------------------------------


def _strava_env(monkeypatch):
    monkeypatch.setenv("STRAVA_RENAME_CLIENT_ID", "1")
    monkeypatch.setenv("STRAVA_RENAME_CLIENT_SECRET", "secret")
    monkeypatch.setenv("STRAVA_RENAME_REFRESH_TOKEN", "rt")


def test_the_shared_trainingpeaks_cookie_is_reused(monkeypatch):
    for name in ("STRAVA_RENAME_TP_AUTH_COOKIE", "TP_CORE_AUTH_COOKIE", "TP_CALENDAR_AUTH_COOKIE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TP_AUTH_COOKIE", "shared")
    _strava_env(monkeypatch)

    assert StravaRenameConfig.from_env().tp_auth_cookie == "shared"


def test_an_unknown_timezone_is_rejected_at_load(monkeypatch):
    monkeypatch.setenv("TP_AUTH_COOKIE", "c")
    _strava_env(monkeypatch)
    monkeypatch.setenv("STRAVA_RENAME_TIMEZONE", "Mars/Olympus_Mons")

    with pytest.raises(ConfigError):
        StravaRenameConfig.from_env()


def test_status_never_exposes_a_credential():
    status = make_tool().status()
    assert set(status) == {"lookback_days", "timezone", "tolerance_minutes", "extra_default_patterns"}
    assert "secret" not in str(status)
