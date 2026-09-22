"""Which Strava activity is which TrainingPeaks workout, and which names are fair game.

Pairing is by start time. Both services take it from the same device file, so a
genuine pair starts within a second or two of each other; a small tolerance
absorbs rounding without ever pairing two different sessions. Sport is checked
as well, so a brick's run and ride — minutes apart — cannot be crossed.

A name is *default* when a machine chose it: Strava's "Morning Run" family, or a
device's bare sport name such as "Road Cycling". Only default names are ever
replaced, which is also what makes the tool idempotent — once renamed, an
activity carries a real title and is never considered again. The same test is
applied to the TrainingPeaks side: an unplanned upload is titled "Running", and
copying that over "Morning Run" would be no improvement.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta

from cronkit.integrations.trainingpeaks import Workout
from cronkit.tools.strava_rename.strava import Activity

TIMES_OF_DAY = ("Morning", "Lunch", "Afternoon", "Evening", "Night")

# The activity part of a Strava-generated name, as Strava renders sport types.
STRAVA_SPORT_WORDS = (
    "Run",
    "Trail Run",
    "Virtual Run",
    "Ride",
    "Virtual Ride",
    "Gravel Ride",
    "Mountain Bike Ride",
    "E-Bike Ride",
    "E-Mountain Bike Ride",
    "Swim",
    "Walk",
    "Hike",
    "Workout",
    "Weight Training",
    "Rowing",
    "Row",
    "Yoga",
    "Elliptical",
    "Stair-Stepper",
    "HIIT",
    "Pilates",
    "Crossfit",
)

# Names devices give an activity, as TrainingPeaks (and sometimes Strava)
# receives them from Garmin and friends.
DEVICE_SPORT_NAMES = (
    "Workout",
    "Running",
    "Treadmill Running",
    "Trail Running",
    "Track Running",
    "Indoor Running",
    "Virtual Running",
    "Cycling",
    "Road Cycling",
    "Indoor Cycling",
    "Virtual Cycling",
    "Track Cycling",
    "Gravel Cycling",
    "Mountain Biking",
    "Swimming",
    "Lap Swimming",
    "Pool Swim",
    "Pool Swimming",
    "Open Water Swimming",
    "Walking",
    "Hiking",
    "Strength",
    "Strength Training",
    "Cardio",
    "Multisport",
    "Triathlon",
    "Rowing",
    "Indoor Rowing",
    "Yoga",
)

_STRAVA_DEFAULT = re.compile(
    rf"^(?:{'|'.join(TIMES_OF_DAY)}) (?:{'|'.join(re.escape(w) for w in STRAVA_SPORT_WORDS)})$",
    re.IGNORECASE,
)
_DEVICE_DEFAULT = {name.casefold() for name in DEVICE_SPORT_NAMES}

# TrainingPeaks base sport -> the Strava sport types it can pair with. A sport
# not listed here (Brick, Custom, Other, ...) pairs with anything.
SPORT_COMPATIBILITY: dict[str, frozenset[str]] = {
    "Bike": frozenset({"Ride", "VirtualRide", "GravelRide", "EBikeRide", "Velomobile", "Handcycle"}),
    "MtnBike": frozenset({"MountainBikeRide", "EMountainBikeRide", "Ride", "GravelRide"}),
    "Run": frozenset({"Run", "TrailRun", "VirtualRun"}),
    "Swim": frozenset({"Swim"}),
    "Walk": frozenset({"Walk", "Hike"}),
    "Strength": frozenset({"WeightTraining", "Workout", "Crossfit"}),
    "Rowing": frozenset({"Rowing", "VirtualRow"}),
    "XCSki": frozenset({"NordicSki", "BackcountrySki"}),
}


def is_default_name(name: str, extra_patterns: Iterable[re.Pattern[str]] = ()) -> bool:
    """Whether a machine, not a person, chose this name."""
    stripped = " ".join(name.split())
    if not stripped:
        return True
    if _STRAVA_DEFAULT.match(stripped) or stripped.casefold() in _DEVICE_DEFAULT:
        return True
    return any(pattern.search(stripped) for pattern in extra_patterns)


def sports_compatible(tp_sport: str | None, strava_sport_type: str) -> bool:
    allowed = SPORT_COMPATIBILITY.get(tp_sport or "")
    return allowed is None or strava_sport_type in allowed


@dataclass(frozen=True)
class Pair:
    workout: Workout
    activity: Activity
    offset: timedelta


def pair_up(workouts: Iterable[Workout], activities: Iterable[Activity], tolerance: timedelta) -> list[Pair]:
    """Pair completed workouts with activities, closest start times first.

    Greedy on the smallest offset across *all* candidates, so one activity can
    never be claimed by a workout it is further from while a closer workout goes
    unmatched. Each side is used at most once.
    """
    candidates: list[Pair] = []
    activities = list(activities)
    for workout in workouts:
        if workout.actual_start is None:
            continue
        for activity in activities:
            offset = abs(activity.start_local - workout.actual_start)
            if offset <= tolerance and sports_compatible(workout.sport, activity.sport_type):
                candidates.append(Pair(workout, activity, offset))

    candidates.sort(key=lambda p: p.offset)
    used_workouts: set[str] = set()
    used_activities: set[str] = set()
    pairs: list[Pair] = []
    for pair in candidates:
        if pair.workout.id in used_workouts or pair.activity.id in used_activities:
            continue
        used_workouts.add(pair.workout.id)
        used_activities.add(pair.activity.id)
        pairs.append(pair)

    pairs.sort(key=lambda p: p.workout.actual_start)
    return pairs
