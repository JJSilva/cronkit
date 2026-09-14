"""Minimal TrainingPeaks API client.

Only what the sync needs: authenticate, resolve the athlete id, and list
workouts in a date range.

Auth mirrors the trainingpeaks-mcp server: a ``Production_tpAuth`` cookie is
exchanged for a short-lived OAuth bearer token via ``GET /users/v3/token``.

The important field here is ``startTimePlanned`` — the time you set on a planned
workout in the TrainingPeaks UI. It is distinct from ``startTime``, which is the
actual start recorded by your device on upload and therefore only ever present
on workouts you have already completed.
"""

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import httpx

API_BASE = "https://tpapi.trainingpeaks.com"
TOKEN_ENDPOINT = "/users/v3/token"
TOKEN_REFRESH_BUFFER = 60  # refresh this many seconds before expiry
REQUEST_TIMEOUT = 30.0

logger = logging.getLogger(__name__)

# Mirrors trainingpeaks-mcp's WORKOUT_TYPE_VALUE_TO_SPORT so sport names match
# what the rest of the toolchain already uses.
WORKOUT_TYPE_VALUE_TO_SPORT: dict[int, str] = {
    1: "Swim",
    2: "Bike",
    3: "Run",
    4: "Brick",
    5: "Crosstrain",
    6: "Race",
    7: "DayOff",
    8: "MtnBike",
    9: "Strength",
    10: "Custom",
    11: "XCSki",
    12: "Rowing",
    13: "Walk",
    29: "Strength",
    100: "Other",
}


class TrainingPeaksError(Exception):
    """A TrainingPeaks API call failed."""


class TrainingPeaksAuthError(TrainingPeaksError):
    """The stored cookie is missing, expired, or rejected.

    Raised separately because it is the one failure that needs a human: the
    cookie must be refreshed and redeployed.
    """


def sport_from_type_value(value: Any) -> str | None:
    """Resolve a base-sport name from a ``workoutTypeValueId``."""
    if isinstance(value, bool):  # bool subclasses int; never a valid id
        return None
    try:
        return WORKOUT_TYPE_VALUE_TO_SPORT.get(int(value))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class Workout:
    """A TrainingPeaks workout, reduced to the fields the calendar needs."""

    id: str
    day: date
    title: str
    description: str | None
    sport: str | None
    # Naive local datetime from ``startTimePlanned``; None when no time is set.
    planned_start: datetime | None
    # Planned duration in hours, as TrainingPeaks reports it.
    planned_hours: float | None

    @property
    def has_planned_time(self) -> bool:
        """Whether this workout carries a planned start time."""
        return self.planned_start is not None


def _parse_naive(value: Any) -> datetime | None:
    """Parse a TrainingPeaks timestamp, discarding any offset.

    Planned start times come back as local wall-clock strings such as
    ``2026-09-16T06:00:00``. Any offset TrainingPeaks attaches is dropped: the
    time is interpreted in the athlete's configured timezone downstream.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Unparseable TrainingPeaks timestamp: %r", value)
        return None
    return parsed.replace(tzinfo=None)


def _parse_day(value: Any) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def parse_workout(raw: dict[str, Any]) -> Workout | None:
    """Convert a raw v6 workout object into a :class:`Workout`.

    Returns None for records without an id or day, which cannot be scheduled.
    """
    workout_id = raw.get("workoutId")
    day = _parse_day(raw.get("workoutDay"))
    if workout_id is None or day is None:
        return None

    planned_start = _parse_naive(raw.get("startTimePlanned"))
    # Guard against a planned time that disagrees with the workout's day: the
    # day field is authoritative for placement, so realign rather than
    # scheduling the event on the wrong date.
    if planned_start is not None and planned_start.date() != day:
        planned_start = planned_start.replace(year=day.year, month=day.month, day=day.day)

    hours = raw.get("totalTimePlanned")
    if not isinstance(hours, int | float) or hours <= 0:
        hours = None

    title = (raw.get("title") or "").strip()
    description = raw.get("description")
    if isinstance(description, str):
        description = description.strip() or None

    return Workout(
        id=str(workout_id),
        day=day,
        title=title or "Workout",
        description=description,
        sport=sport_from_type_value(raw.get("workoutTypeValueId")),
        planned_start=planned_start,
        planned_hours=float(hours) if hours is not None else None,
    )


class TrainingPeaksClient:
    """Async TrainingPeaks client scoped to a single athlete."""

    def __init__(self, auth_cookie: str, timeout: float = REQUEST_TIMEOUT):
        self._cookie = auth_cookie
        self._client = httpx.AsyncClient(timeout=timeout)
        self._access_token: str | None = None
        self._expires_at: float = 0.0
        self._athlete_id: int | None = None

    async def __aenter__(self) -> "TrainingPeaksClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def _token(self) -> str:
        """Return a valid bearer token, exchanging the cookie when needed."""
        if self._access_token and time.time() < self._expires_at - TOKEN_REFRESH_BUFFER:
            return self._access_token

        response = await self._client.get(
            f"{API_BASE}{TOKEN_ENDPOINT}",
            headers={
                "Cookie": f"Production_tpAuth={self._cookie}",
                "Accept": "application/json",
            },
        )
        if response.status_code == 401:
            raise TrainingPeaksAuthError(
                "TrainingPeaks rejected the auth cookie (401). The cookie has expired — "
                "capture a fresh Production_tpAuth value and update TP_AUTH_COOKIE."
            )
        if response.status_code != 200:
            raise TrainingPeaksError(f"Token exchange failed with HTTP {response.status_code}")

        payload = response.json()
        token = (payload or {}).get("token") or {}
        access_token = token.get("access_token")
        if not payload.get("success") or not access_token:
            raise TrainingPeaksAuthError("Token exchange returned no access_token.")

        self._access_token = access_token
        self._expires_at = time.time() + float(token.get("expires_in", 3600))
        return access_token

    async def _get(self, endpoint: str, *, retry_on_401: bool = True) -> Any:
        token = await self._token()
        response = await self._client.get(
            f"{API_BASE}{endpoint}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )

        if response.status_code == 401 and retry_on_401:
            # Token may have expired mid-flight; drop it and try once more.
            self._access_token = None
            return await self._get(endpoint, retry_on_401=False)

        if response.status_code == 401:
            raise TrainingPeaksAuthError("TrainingPeaks returned 401 after a token refresh.")
        if response.status_code != 200:
            raise TrainingPeaksError(f"GET {endpoint} failed with HTTP {response.status_code}")

        return response.json()

    async def athlete_id(self) -> int:
        """Resolve and cache the athlete id for the authenticated user."""
        if self._athlete_id is not None:
            return self._athlete_id

        data = await self._get("/users/v3/user")
        user = (data or {}).get("user", data) or {}
        person_id = user.get("personId")
        athletes = user.get("athletes") or []

        athlete_id = None
        if athletes:
            email = (user.get("email") or "").lower()
            # A coach account lists many athletes; find the entry that is the
            # user themselves before falling back.
            for entry in athletes:
                if entry.get("coachedBy") == person_id and (entry.get("email") or "").lower() == email:
                    athlete_id = entry.get("athleteId")
                    break
            if not athlete_id:
                athlete_id = person_id or athletes[0].get("athleteId")
        else:
            athlete_id = person_id

        if not athlete_id:
            raise TrainingPeaksAuthError("Could not resolve an athlete id for this account.")

        self._athlete_id = int(athlete_id)
        return self._athlete_id

    async def workouts(self, start: date, end: date) -> list[Workout]:
        """List workouts between ``start`` and ``end`` inclusive."""
        athlete_id = await self.athlete_id()
        endpoint = f"/fitness/v6/athletes/{athlete_id}/workouts/{start.isoformat()}/{end.isoformat()}"
        data = await self._get(endpoint)

        if not isinstance(data, list):
            return []

        workouts = []
        for raw in data:
            if not isinstance(raw, dict):
                continue
            parsed = parse_workout(raw)
            if parsed is not None:
                workouts.append(parsed)
        return workouts
