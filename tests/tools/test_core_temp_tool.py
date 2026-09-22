"""The run: which workouts get picked up, and what gets written back."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from cronkit.integrations.trainingpeaks import DeviceFile, Workout
from cronkit.tools.trainingpeaks_core_temp import tool as tool_module
from cronkit.tools.trainingpeaks_core_temp.config import CoreTempConfig
from cronkit.tools.trainingpeaks_core_temp.report import HEADER
from cronkit.tools.trainingpeaks_core_temp.tool import TrainingPeaksCoreTempTool

FIXTURE = Path(__file__).parent.parent / "fixtures" / "core_ride.fit.gz"
TODAY = date(2026, 9, 22)


@pytest.fixture(scope="module")
def core_bytes() -> bytes:
    return FIXTURE.read_bytes()


@pytest.fixture
def no_core_bytes() -> bytes:
    from tests.fixtures.make_core_fit import build

    return build([], datetime(2026, 9, 22, 13, 0, tzinfo=UTC))


def workout(**overrides) -> Workout:
    base = {
        "id": "100",
        "day": TODAY,
        "title": "Road Cycling",
        "description": None,
        "sport": "Bike",
        "planned_start": None,
        "planned_hours": None,
        "actual_start": datetime(2026, 9, 22, 13, 0),
        "actual_hours": 1.5,
        "comments": (),
    }
    base.update(overrides)
    return Workout(**base)


class FakeTP:
    """Stands in for TrainingPeaksClient, recording what the tool did."""

    def __init__(self, workouts, file_bytes, *, files=True):
        self._workouts = workouts
        self._file_bytes = file_bytes
        self._files = files
        self.comments = {w.id: list(w.comments) for w in workouts}
        self.posted: list[tuple[str, str]] = []
        self.downloads: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def workouts(self, start, end):
        return list(self._workouts)

    async def device_files(self, workout_id):
        return [DeviceFile(file_id="f1", file_name="a.fit.gz")] if self._files else []

    async def download_file(self, workout_id, file_id):
        self.downloads.append(workout_id)
        return self._file_bytes

    async def workout_comments(self, workout_id):
        return tuple(self.comments.get(workout_id, ()))

    async def add_comment(self, workout_id, text):
        self.posted.append((workout_id, text))
        self.comments.setdefault(workout_id, []).append(text)


def make_tool(**overrides) -> TrainingPeaksCoreTempTool:
    base = {"tp_auth_cookie": "cookie"}
    base.update(overrides)
    return TrainingPeaksCoreTempTool(CoreTempConfig(**base))


def wire(monkeypatch, fake):
    monkeypatch.setattr(tool_module, "TrainingPeaksClient", lambda *a, **k: fake)


# --- the happy path --------------------------------------------------------


async def test_a_completed_workout_gets_its_comment_written(monkeypatch, core_bytes):
    fake = FakeTP([workout()], core_bytes)
    wire(monkeypatch, fake)

    result = await make_tool().run()

    assert result.ok
    assert result.details["annotated"] == [f"{TODAY} Road Cycling"]
    assert len(fake.posted) == 1
    workout_id, text = fake.posted[0]
    assert workout_id == "100"
    assert HEADER in text
    assert "Core  avg" in text


async def test_the_athletes_own_comments_are_left_alone(monkeypatch, core_bytes):
    """The block is its own thread entry, so nothing existing is rewritten."""
    fake = FakeTP([workout(comments=("Felt strong.",))], core_bytes)
    wire(monkeypatch, fake)
    await make_tool().run()

    assert fake.comments["100"][0] == "Felt strong."
    assert HEADER in fake.comments["100"][1]


# --- what gets skipped -----------------------------------------------------


async def test_an_already_annotated_workout_is_left_alone(monkeypatch, core_bytes):
    """The processed check — and it must not even download the file."""
    fake = FakeTP([workout(comments=("Notes", f"{HEADER}\nold block"))], core_bytes)
    wire(monkeypatch, fake)

    result = await make_tool().run()

    assert result.details["already_annotated"] == [f"{TODAY} Road Cycling"]
    assert fake.posted == []
    assert fake.downloads == []


async def test_a_planned_only_workout_is_not_a_candidate(monkeypatch, core_bytes):
    fake = FakeTP([workout(actual_start=None, actual_hours=None)], core_bytes)
    wire(monkeypatch, fake)

    result = await make_tool().run()

    assert result.details["completed_workouts"] == 0
    assert fake.posted == []


async def test_a_workout_with_no_upload_is_skipped(monkeypatch, core_bytes):
    fake = FakeTP([workout()], core_bytes, files=False)
    wire(monkeypatch, fake)

    result = await make_tool().run()

    assert result.details["no_core_data"] == [f"{TODAY} Road Cycling"]
    assert fake.posted == []


async def test_a_file_without_core_data_gets_no_comment(monkeypatch, no_core_bytes):
    """A workout recorded without the sensor is left completely untouched."""
    fake = FakeTP([workout()], no_core_bytes)
    wire(monkeypatch, fake)

    result = await make_tool().run()

    assert result.details["no_core_data"] == [f"{TODAY} Road Cycling"]
    assert fake.posted == []


async def test_a_file_without_core_data_is_not_downloaded_twice(monkeypatch, no_core_bytes):
    """Otherwise every run re-fetches a multi-megabyte file forever."""
    fake = FakeTP([workout()], no_core_bytes)
    wire(monkeypatch, fake)
    core_tool = make_tool()

    await core_tool.run()
    await core_tool.run()

    assert fake.downloads == ["100"]


# --- races and failures ----------------------------------------------------


async def test_a_block_added_between_listing_and_writing_is_not_overwritten(monkeypatch, core_bytes):
    """The list snapshot can be minutes stale by the time we go to write."""
    fake = FakeTP([workout()], core_bytes)
    fake.comments["100"] = [f"{HEADER}\nwritten by another run"]
    wire(monkeypatch, fake)

    result = await make_tool().run()

    assert fake.posted == []
    assert result.details["already_annotated"] == [f"{TODAY} Road Cycling"]


async def test_one_failing_workout_does_not_stop_the_others(monkeypatch, core_bytes):
    fake = FakeTP([workout(id="100"), workout(id="200", title="Run")], core_bytes)

    async def explode(workout_id, file_id):
        if workout_id == "100":
            raise RuntimeError("upstream broke")
        return core_bytes

    fake.download_file = explode
    wire(monkeypatch, fake)

    result = await make_tool().run()

    assert not result.ok
    assert len(result.errors) == 1
    assert result.details["annotated"] == [f"{TODAY} Run"]


# --- dry run ---------------------------------------------------------------


async def test_a_dry_run_reports_without_writing(monkeypatch, core_bytes):
    fake = FakeTP([workout()], core_bytes)
    wire(monkeypatch, fake)

    result = await make_tool().run(dry_run=True)

    assert result.dry_run
    assert result.details["annotated"] == [f"{TODAY} Road Cycling"]
    assert fake.posted == []


# --- configuration ---------------------------------------------------------


def test_status_never_exposes_the_cookie():
    status = make_tool().status()
    assert "cookie" not in str(status)
    assert status["units"] == "F"


def test_fahrenheit_is_the_default(monkeypatch):
    monkeypatch.setenv("TP_CORE_AUTH_COOKIE", "cookie")
    monkeypatch.delenv("TP_CORE_UNITS", raising=False)
    config = CoreTempConfig.from_env()

    assert config.fahrenheit is True
    assert config.threshold_c == pytest.approx(38.0)


def test_celsius_can_be_asked_for(monkeypatch):
    monkeypatch.setenv("TP_CORE_AUTH_COOKIE", "cookie")
    monkeypatch.setenv("TP_CORE_UNITS", "C")
    assert CoreTempConfig.from_env().fahrenheit is False


def test_the_threshold_is_configured_in_the_display_unit(monkeypatch):
    """A Fahrenheit deployment should never have to think in Celsius."""
    monkeypatch.setenv("TP_CORE_AUTH_COOKIE", "cookie")
    monkeypatch.setenv("TP_CORE_UNITS", "F")
    monkeypatch.setenv("TP_CORE_THRESHOLD", "102.0")

    assert CoreTempConfig.from_env().threshold_c == pytest.approx(38.889, abs=0.01)


def test_an_explicit_celsius_threshold_still_wins(monkeypatch):
    monkeypatch.setenv("TP_CORE_AUTH_COOKIE", "cookie")
    monkeypatch.setenv("TP_CORE_THRESHOLD_C", "39.0")

    assert CoreTempConfig.from_env().threshold_c == pytest.approx(39.0)


def test_the_calendar_tools_cookie_is_reused(monkeypatch):
    """One TrainingPeaks credential should configure both tools."""
    for name in ("TP_CORE_AUTH_COOKIE", "TP_CALENDAR_AUTH_COOKIE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TP_AUTH_COOKIE", "shared")

    assert CoreTempConfig.from_env().tp_auth_cookie == "shared"
