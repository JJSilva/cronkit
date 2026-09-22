"""Which names count as default, and which activity pairs with which workout."""

import re
from datetime import date, datetime, timedelta

import pytest

from cronkit.integrations.trainingpeaks import Workout
from cronkit.tools.strava_rename.matching import is_default_name, pair_up
from cronkit.tools.strava_rename.strava import Activity, parse_activity

TOLERANCE = timedelta(minutes=2)


def workout(id="1", start=datetime(2026, 9, 22, 5, 41, 37), sport="Run", title="Longish Run") -> Workout:
    return Workout(
        id=id,
        day=start.date() if start else date(2026, 9, 22),
        title=title,
        description=None,
        sport=sport,
        planned_start=None,
        planned_hours=1.5,
        actual_start=start,
        actual_hours=1.4,
    )


def activity(id="a", start=datetime(2026, 9, 22, 5, 41, 37), sport_type="Run", name="Morning Run") -> Activity:
    return Activity(id=id, name=name, sport_type=sport_type, start_local=start)


# --- default names ---------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "Morning Run",
        "Lunch Swim",
        "Afternoon Ride",
        "Evening Weight Training",
        "Night Virtual Ride",
        "morning run",
        "  Morning   Run ",
        "Running",
        "Road Cycling",
        "Lap Swimming",
        "Open Water Swimming",
        "",
    ],
)
def test_machine_chosen_names_are_default(name):
    assert is_default_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "Medium Aerobic Ride",
        "Morning Run with the club",
        "Race Day 10k",
        "Longish Run",
        "Keis",
        "MA",
        "Taper Fartlek Run",
        "Morning",
    ],
)
def test_names_a_person_chose_are_not_default(name):
    assert not is_default_name(name)


def test_extra_patterns_extend_the_default_list():
    zwift = re.compile(r"^Zwift - ", re.IGNORECASE)
    assert is_default_name("Zwift - Volcano Flat in Watopia", [zwift])
    assert not is_default_name("Zwift - Volcano Flat in Watopia")


# --- pairing ---------------------------------------------------------------


def test_the_same_device_file_pairs_even_a_second_apart():
    """Observed live: TrainingPeaks and Strava can disagree by one second."""
    swim = workout(start=datetime(2026, 9, 21, 12, 40, 57), sport="Swim", title="Masters")
    lunch_swim = activity(start=datetime(2026, 9, 21, 12, 40, 58), sport_type="Swim", name="Lunch Swim")

    pairs = pair_up([swim], [lunch_swim], TOLERANCE)

    assert len(pairs) == 1
    assert pairs[0].offset == timedelta(seconds=1)


def test_start_times_beyond_the_tolerance_do_not_pair():
    pairs = pair_up([workout()], [activity(start=datetime(2026, 9, 22, 5, 50))], TOLERANCE)
    assert pairs == []


def test_a_brick_does_not_cross_its_run_and_ride():
    """A run and a ride under two minutes apart must each find its own sport."""
    run = workout(id="run", start=datetime(2026, 9, 22, 9, 3, 20), sport="Run")
    ride = workout(id="ride", start=datetime(2026, 9, 22, 9, 5, 3), sport="Bike")
    strava_run = activity(id="r", start=datetime(2026, 9, 22, 9, 3, 20), sport_type="Run")
    strava_ride = activity(id="b", start=datetime(2026, 9, 22, 9, 5, 3), sport_type="Ride")

    pairs = pair_up([run, ride], [strava_ride, strava_run], TOLERANCE)

    assert {(p.workout.id, p.activity.id) for p in pairs} == {("run", "r"), ("ride", "b")}


def test_the_closest_start_wins_when_two_workouts_compete():
    early = workout(id="early", start=datetime(2026, 9, 22, 6, 0, 0))
    late = workout(id="late", start=datetime(2026, 9, 22, 6, 1, 30))
    only = activity(start=datetime(2026, 9, 22, 6, 1, 29))

    pairs = pair_up([early, late], [only], TOLERANCE)

    assert [p.workout.id for p in pairs] == ["late"]


def test_a_sport_without_a_mapping_pairs_with_anything():
    pairs = pair_up([workout(sport="Brick")], [activity(sport_type="Ride")], TOLERANCE)
    assert len(pairs) == 1


def test_workouts_without_a_recorded_start_are_ignored():
    assert pair_up([workout(start=None)], [activity()], TOLERANCE) == []


# --- parsing ---------------------------------------------------------------


def test_start_date_local_is_wall_clock_despite_its_z_suffix():
    parsed = parse_activity(
        {"id": 20282356889, "name": "Morning Run", "sport_type": "Run", "start_date_local": "2026-09-22T05:41:37Z"}
    )
    assert parsed.start_local == datetime(2026, 9, 22, 5, 41, 37)
    assert parsed.id == "20282356889"


def test_activities_without_a_start_are_dropped():
    assert parse_activity({"id": 1, "name": "x"}) is None
