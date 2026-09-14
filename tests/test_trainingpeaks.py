"""Parsing rules for TrainingPeaks workouts."""

from datetime import date, datetime

from schedulesync.trainingpeaks import parse_workout, sport_from_type_value


def raw(**overrides):
    base = {
        "workoutId": 3943689042,
        "workoutDay": "2026-09-16T00:00:00",
        "title": "Track Cycling",
        "description": "4 x 8min @ threshold",
        "workoutTypeValueId": 2,
        "totalTimePlanned": 2.0,
    }
    base.update(overrides)
    return base


def test_planned_start_time_is_read_from_start_time_planned():
    workout = parse_workout(raw(startTimePlanned="2026-09-16T06:30:00"))
    assert workout.planned_start == datetime(2026, 9, 16, 6, 30)
    assert workout.has_planned_time


def test_actual_start_time_does_not_count_as_a_planned_time():
    """``startTime`` is the device-recorded actual start, not a scheduled time.

    A completed workout must not become a calendar event on the strength of it.
    """
    workout = parse_workout(raw(startTime="2026-09-16T06:41:22", totalTime=2.01))
    assert workout.planned_start is None
    assert not workout.has_planned_time


def test_workout_without_any_time_has_no_planned_start():
    assert parse_workout(raw()).planned_start is None


def test_planned_start_is_realigned_to_the_workout_day():
    """The workout day is authoritative for placement."""
    workout = parse_workout(raw(startTimePlanned="2026-09-14T06:30:00"))
    assert workout.planned_start == datetime(2026, 9, 16, 6, 30)


def test_timezone_offsets_are_discarded_as_wall_clock():
    workout = parse_workout(raw(startTimePlanned="2026-09-16T06:30:00-07:00"))
    assert workout.planned_start == datetime(2026, 9, 16, 6, 30)


def test_fields_map_onto_the_calendar_shape():
    workout = parse_workout(raw(startTimePlanned="2026-09-16T06:30:00"))
    assert workout.id == "3943689042"
    assert workout.day == date(2026, 9, 16)
    assert workout.title == "Track Cycling"
    assert workout.description == "4 x 8min @ threshold"
    assert workout.sport == "Bike"
    assert workout.planned_hours == 2.0


def test_missing_title_falls_back_rather_than_producing_an_empty_event():
    assert parse_workout(raw(title=None)).title == "Workout"


def test_blank_description_becomes_none():
    assert parse_workout(raw(description="   ")).description is None


def test_zero_duration_is_treated_as_absent():
    assert parse_workout(raw(totalTimePlanned=0)).planned_hours is None


def test_records_without_an_id_or_day_are_dropped():
    assert parse_workout(raw(workoutId=None)) is None
    assert parse_workout(raw(workoutDay=None)) is None


def test_unparseable_planned_time_is_ignored_rather_than_raising():
    assert parse_workout(raw(startTimePlanned="not a date")).planned_start is None


def test_sport_lookup():
    assert sport_from_type_value(1) == "Swim"
    assert sport_from_type_value("3") == "Run"
    assert sport_from_type_value(True) is None
    assert sport_from_type_value(None) is None
    assert sport_from_type_value(9999) is None
