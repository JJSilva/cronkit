"""Sync business rules, exercised against fake TrainingPeaks and Calendar clients."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from schedulesync import sync as sync_module
from schedulesync.config import Config
from schedulesync.gcal import SyncedEvent, event_id_for
from schedulesync.sync import event_window, needs_update, run_sync
from schedulesync.trainingpeaks import Workout

TZ = ZoneInfo("America/Los_Angeles")


def workout(**overrides) -> Workout:
    base = {
        "id": "3943689042",
        "day": date(2026, 9, 16),
        "title": "Track Cycling",
        "description": "4 x 8min",
        "sport": "Bike",
        "planned_start": datetime(2026, 9, 16, 6, 30),
        "planned_hours": 2.0,
    }
    base.update(overrides)
    return Workout(**base)


# --- event ids -------------------------------------------------------------


def test_event_id_is_stable_and_derived_from_the_workout_id():
    assert event_id_for("3943689042") == "tpplan3943689042"
    assert event_id_for("3943689042") == event_id_for("3943689042")


def test_distinct_workout_ids_never_collide():
    """The fallback encoding must be lossless.

    A lossy encoding would let two different workouts map to one event id and
    silently overwrite each other on the calendar.
    """
    raw_ids = ["abc-XYZ", "abc-XYz", "a/b", "a?b", "", "7", "3943689042"]
    generated = [event_id_for(r) for r in raw_ids]
    assert len(set(generated)) == len(raw_ids)


def test_event_ids_are_legal_for_the_calendar_api():
    """Google requires base32hex: 0-9 and a-v, at least 5 characters."""
    allowed = set("0123456789abcdefghijklmnopqrstuv")
    for raw_id in ["3943689042", "7", "abc-XYZ_!", "wz"]:
        generated = event_id_for(raw_id)
        assert set(generated) <= allowed, generated
        assert len(generated) >= 5, generated


# --- event window ----------------------------------------------------------


def test_event_runs_for_the_planned_duration():
    start, end = event_window(workout())
    assert start == datetime(2026, 9, 16, 6, 30)
    assert end == datetime(2026, 9, 16, 8, 30)


def test_missing_duration_falls_back_to_an_hour():
    _, end = event_window(workout(planned_hours=None))
    assert end == datetime(2026, 9, 16, 7, 30)


def test_fractional_hours_round_trip():
    _, end = event_window(workout(planned_hours=0.5833333333333334))
    assert end - datetime(2026, 9, 16, 6, 30) == timedelta(seconds=2100)


# --- change detection ------------------------------------------------------


def synced(**overrides) -> SyncedEvent:
    base = {
        "event_id": "tpplan3943689042",
        "workout_id": "3943689042",
        "summary": "Track Cycling",
        "description": "4 x 8min",
        "start": "2026-09-16T06:30:00-07:00",
        "end": "2026-09-16T08:30:00-07:00",
    }
    base.update(overrides)
    return SyncedEvent(**base)


def test_identical_event_needs_no_update():
    assert not needs_update(synced(), workout(), TZ)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("summary", "Track Cycling (moved)"),
        ("description", "different"),
        ("start", "2026-09-16T07:00:00-07:00"),
        ("end", "2026-09-16T09:00:00-07:00"),
    ],
)
def test_any_differing_field_triggers_an_update(field, value):
    assert needs_update(synced(**{field: value}), workout(), TZ)


def test_description_none_and_empty_string_are_equivalent():
    assert not needs_update(synced(description=None), workout(description=None), TZ)
    assert not needs_update(synced(description=""), workout(description=None), TZ)


# --- orchestration ---------------------------------------------------------


class FakeTP:
    def __init__(self, workouts):
        self._workouts = workouts

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def workouts(self, start, end):
        return [w for w in self._workouts if start <= w.day <= end]


class FakeCalendar:
    def __init__(self, existing=()):
        self.existing = list(existing)
        self.upserts: list[tuple[str, dict]] = []
        self.deleted: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def calendar_timezone(self):
        return "America/Los_Angeles"

    async def list_synced_events(self, start, end, tz):
        self.list_tz = tz
        return list(self.existing)

    def build_event_body(self, *, workout_id, summary, description, start, end, timezone):
        return {
            "summary": summary,
            "description": description or "",
            "start": {"dateTime": start.isoformat(), "timeZone": timezone},
            "end": {"dateTime": end.isoformat(), "timeZone": timezone},
            "workout_id": workout_id,
        }

    async def upsert_event(self, event_id, body):
        self.upserts.append((event_id, body))
        known = {e.event_id for e in self.existing}
        return "updated" if event_id in known else "created"

    async def delete_event(self, event_id):
        self.deleted.append(event_id)


@pytest.fixture
def config():
    return Config(
        tp_auth_cookie="cookie",
        google_client_id="id",
        google_client_secret="secret",
        google_refresh_token="refresh",
        calendar_id="athlete@example.com",
        sync_days=21,
    )


def wire(monkeypatch, tp, cal):
    monkeypatch.setattr(sync_module, "TrainingPeaksClient", lambda *a, **k: tp)
    monkeypatch.setattr(sync_module, "CalendarClient", lambda *a, **k: cal)


async def test_only_timed_workouts_reach_the_calendar(monkeypatch, config):
    timed = workout(id="1", title="Masters", planned_start=datetime(2026, 9, 16, 6, 0))
    untimed = workout(id="2", title="OWS", planned_start=None)
    cal = FakeCalendar()
    wire(monkeypatch, FakeTP([timed, untimed]), cal)

    result = await run_sync(config, today=date(2026, 9, 13))

    assert result.total_workouts == 2
    assert result.timed_workouts == 1
    assert result.skipped_untimed == 1
    assert [event_id for event_id, _ in cal.upserts] == ["tpplan1"]
    assert result.created == ["2026-09-16 Masters"]


async def test_title_and_description_carry_across(monkeypatch, config):
    cal = FakeCalendar()
    wire(monkeypatch, FakeTP([workout(title="Track Cycling", description="4 x 8min")]), cal)

    await run_sync(config, today=date(2026, 9, 13))

    _, body = cal.upserts[0]
    assert body["summary"] == "Track Cycling"
    assert body["description"] == "4 x 8min"


async def test_unchanged_events_are_not_rewritten(monkeypatch, config):
    cal = FakeCalendar(existing=[synced()])
    wire(monkeypatch, FakeTP([workout()]), cal)

    result = await run_sync(config, today=date(2026, 9, 13))

    assert cal.upserts == []
    assert result.unchanged == ["2026-09-16 Track Cycling"]


async def test_a_moved_workout_updates_in_place(monkeypatch, config):
    cal = FakeCalendar(existing=[synced()])
    wire(monkeypatch, FakeTP([workout(planned_start=datetime(2026, 9, 16, 17, 0))]), cal)

    result = await run_sync(config, today=date(2026, 9, 13))

    assert len(result.updated) == 1
    assert result.created == []
    assert cal.upserts[0][0] == "tpplan3943689042"


async def test_clearing_a_planned_time_removes_the_event(monkeypatch, config):
    cal = FakeCalendar(existing=[synced()])
    wire(monkeypatch, FakeTP([workout(planned_start=None)]), cal)

    result = await run_sync(config, today=date(2026, 9, 13))

    assert cal.deleted == ["tpplan3943689042"]
    assert len(result.deleted) == 1


async def test_pruning_can_be_disabled(monkeypatch, config):
    from dataclasses import replace

    cal = FakeCalendar(existing=[synced()])
    wire(monkeypatch, FakeTP([workout(planned_start=None)]), cal)

    result = await run_sync(replace(config, prune=False), today=date(2026, 9, 13))

    assert cal.deleted == []
    assert result.deleted == []


async def test_workouts_outside_the_window_are_ignored(monkeypatch, config):
    from dataclasses import replace

    far = workout(id="9", day=date(2026, 11, 1), planned_start=datetime(2026, 11, 1, 6, 0))
    cal = FakeCalendar()
    wire(monkeypatch, FakeTP([far]), cal)

    result = await run_sync(replace(config, sync_days=21), today=date(2026, 9, 13))

    assert result.total_workouts == 0
    assert cal.upserts == []


async def test_dry_run_touches_nothing(monkeypatch, config):
    cal = FakeCalendar(existing=[synced(event_id="tpplandead", workout_id="dead")])
    wire(monkeypatch, FakeTP([workout()]), cal)

    result = await run_sync(config, dry_run=True, today=date(2026, 9, 13))

    assert cal.upserts == []
    assert cal.deleted == []
    assert len(result.created) == 1
    assert len(result.deleted) == 1


async def test_one_failing_workout_does_not_abort_the_run(monkeypatch, config):
    good = workout(id="1", title="Good", planned_start=datetime(2026, 9, 16, 6, 0))
    bad = workout(id="2", title="Bad", planned_start=datetime(2026, 9, 17, 6, 0))
    cal = FakeCalendar()

    original = cal.upsert_event

    async def flaky(event_id, body):
        if event_id == "tpplan2":
            raise RuntimeError("boom")
        return await original(event_id, body)

    cal.upsert_event = flaky
    wire(monkeypatch, FakeTP([good, bad]), cal)

    result = await run_sync(config, today=date(2026, 9, 13))

    assert result.created == ["2026-09-16 Good"]
    assert len(result.errors) == 1
    assert "Bad" in result.errors[0]
