"""The Tymewear run: which workouts get picked up, and what gets written back."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from cronkit.integrations.trainingpeaks import DeviceFile, Workout
from cronkit.tools.trainingpeaks_core_temp.report import HEADER as CORE_HEADER
from cronkit.tools.trainingpeaks_tymewear import tool as tool_module
from cronkit.tools.trainingpeaks_tymewear.config import TymewearConfig
from cronkit.tools.trainingpeaks_tymewear.report import HEADER
from cronkit.tools.trainingpeaks_tymewear.tool import TrainingPeaksTymewearTool

FIXTURES = Path(__file__).parent.parent / "fixtures"
TODAY = date(2026, 10, 26)


@pytest.fixture(scope="module")
def tyme_bytes() -> bytes:
    return (FIXTURES / "tymewear_run.fit.gz").read_bytes()


@pytest.fixture
def no_tyme_bytes() -> bytes:
    from tests.fixtures.make_tymewear_fit import build

    return build([], datetime(2026, 10, 26, 22, 40, tzinfo=UTC))


def workout(**overrides) -> Workout:
    base = {
        "id": "100",
        "day": TODAY,
        "title": "Running",
        "description": None,
        "sport": "Run",
        "planned_start": None,
        "planned_hours": None,
        "actual_start": datetime(2026, 10, 26, 15, 40),
        "actual_hours": 0.33,
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


def make_tool(**overrides) -> TrainingPeaksTymewearTool:
    base = {"tp_auth_cookie": "cookie"}
    base.update(overrides)
    return TrainingPeaksTymewearTool(TymewearConfig(**base))


def wire(monkeypatch, fake):
    monkeypatch.setattr(tool_module, "TrainingPeaksClient", lambda *a, **k: fake)


# --- the happy path --------------------------------------------------------


async def test_a_completed_workout_gets_its_comment_written(monkeypatch, tyme_bytes):
    fake = FakeTP([workout()], tyme_bytes)
    wire(monkeypatch, fake)

    result = await make_tool().run()

    assert result.ok
    assert result.details["annotated"] == [f"{TODAY} Running"]
    workout_id, text = fake.posted[0]
    assert workout_id == "100"
    assert HEADER in text
    assert "BR  avg" in text


async def test_a_core_block_already_in_the_thread_does_not_stop_this_one(monkeypatch, tyme_bytes):
    """Both tools annotate the same run; CORE going first must not suppress Tymewear."""
    fake = FakeTP([workout(comments=(f"{CORE_HEADER}\nCore  avg 98.6 °F",))], tyme_bytes)
    wire(monkeypatch, fake)

    await make_tool().run()

    assert len(fake.posted) == 1
    assert CORE_HEADER in fake.comments["100"][0]
    assert HEADER in fake.comments["100"][1]


# --- what gets skipped -----------------------------------------------------


async def test_an_already_annotated_workout_is_left_alone(monkeypatch, tyme_bytes):
    """The processed check — and it must not even download the file."""
    fake = FakeTP([workout(comments=(f"{HEADER}\nold block",))], tyme_bytes)
    wire(monkeypatch, fake)

    result = await make_tool().run()

    assert result.details["already_annotated"] == [f"{TODAY} Running"]
    assert fake.posted == []
    assert fake.downloads == []


async def test_a_planned_only_workout_is_not_a_candidate(monkeypatch, tyme_bytes):
    fake = FakeTP([workout(actual_start=None, actual_hours=None)], tyme_bytes)
    wire(monkeypatch, fake)

    result = await make_tool().run()

    assert result.details["completed_workouts"] == 0
    assert fake.posted == []


async def test_a_workout_with_no_upload_is_skipped(monkeypatch, tyme_bytes):
    fake = FakeTP([workout()], tyme_bytes, files=False)
    wire(monkeypatch, fake)

    result = await make_tool().run()

    assert result.details["no_tymewear_data"] == [f"{TODAY} Running"]
    assert fake.posted == []


async def test_a_file_without_tymewear_data_gets_no_comment_and_is_not_refetched(monkeypatch, no_tyme_bytes):
    fake = FakeTP([workout()], no_tyme_bytes)
    wire(monkeypatch, fake)
    tyme_tool = make_tool()

    first = await tyme_tool.run()
    await tyme_tool.run()

    assert first.details["no_tymewear_data"] == [f"{TODAY} Running"]
    assert fake.posted == []
    assert fake.downloads == ["100"]


# --- races and failures ----------------------------------------------------


async def test_a_block_added_between_listing_and_writing_is_not_duplicated(monkeypatch, tyme_bytes):
    fake = FakeTP([workout()], tyme_bytes)
    fake.comments["100"] = [f"{HEADER}\nwritten by another run"]
    wire(monkeypatch, fake)

    result = await make_tool().run()

    assert fake.posted == []
    assert result.details["already_annotated"] == [f"{TODAY} Running"]


async def test_one_failing_workout_does_not_stop_the_others(monkeypatch, tyme_bytes):
    fake = FakeTP([workout(id="100"), workout(id="200", title="Tempo")], tyme_bytes)

    async def explode(workout_id, file_id):
        if workout_id == "100":
            raise RuntimeError("upstream broke")
        return tyme_bytes

    fake.download_file = explode
    wire(monkeypatch, fake)

    result = await make_tool().run()

    assert not result.ok
    assert len(result.errors) == 1
    assert result.details["annotated"] == [f"{TODAY} Tempo"]


async def test_a_dry_run_reports_without_writing(monkeypatch, tyme_bytes):
    fake = FakeTP([workout()], tyme_bytes)
    wire(monkeypatch, fake)

    result = await make_tool().run(dry_run=True)

    assert result.dry_run
    assert result.details["annotated"] == [f"{TODAY} Running"]
    assert fake.posted == []


# --- configuration ---------------------------------------------------------


def test_status_never_exposes_the_cookie():
    assert "cookie" not in str(make_tool().status())


def test_the_shared_trainingpeaks_cookie_is_reused(monkeypatch):
    for name in ("TP_TYMEWEAR_AUTH_COOKIE", "TP_CORE_AUTH_COOKIE", "TP_CALENDAR_AUTH_COOKIE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TP_AUTH_COOKIE", "shared")

    assert TymewearConfig.from_env().tp_auth_cookie == "shared"


def test_the_table_size_does_not_collide_with_the_schedule(monkeypatch):
    """<prefix>_INTERVAL_MINUTES is the scheduler's cadence, so the table uses its own name."""
    monkeypatch.setenv("TP_TYMEWEAR_AUTH_COOKIE", "cookie")
    monkeypatch.setenv("TP_TYMEWEAR_INTERVAL_MINUTES", "30")
    monkeypatch.setenv("TP_TYMEWEAR_TABLE_MINUTES", "2")

    assert TymewearConfig.from_env().table_minutes == 2.0


def test_the_shared_timezone_configures_this_tool(monkeypatch):
    monkeypatch.setenv("TP_TYMEWEAR_AUTH_COOKIE", "cookie")
    monkeypatch.delenv("TP_TYMEWEAR_TIMEZONE", raising=False)
    monkeypatch.setenv("CRONKIT_TIMEZONE", "America/Los_Angeles")

    assert TymewearConfig.from_env().timezone == "America/Los_Angeles"
