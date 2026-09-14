"""Minimal Google Calendar client.

Authenticates as the calendar owner with a long-lived OAuth refresh token, which
is what lets the service run unattended.

Events are written with *deterministic* ids derived from the TrainingPeaks
workout id. That is what makes the sync stateless: there is no database mapping
workouts to events, so re-running is naturally idempotent and the container can
be rebuilt or moved without losing track of what it has already created.
"""

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from typing import Any
from urllib.parse import quote

import httpx

API_BASE = "https://www.googleapis.com/calendar/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"
TOKEN_REFRESH_BUFFER = 60
REQUEST_TIMEOUT = 30.0

# Marks events this tool owns, so pruning can never touch anything else on the
# calendar.
MARKER_KEY = "schedulesync"
MARKER_VALUE = "1"
WORKOUT_ID_KEY = "tpWorkoutId"

# Google event ids must be base32hex: digits 0-9 and lowercase a-v, 5-1024 chars.
_BASE32HEX = "0123456789abcdefghijklmnopqrstuv"
# The prefix is 6 characters so that even a single-character workout id
# clears Google's 5-character minimum without padding, which would risk
# colliding with a genuine id. Every character is itself base32hex-legal
# (a-v only, so no w/x/y/z).
_EVENT_ID_PREFIX = "tpplan"

logger = logging.getLogger(__name__)


def _seg(value: str) -> str:
    """Escape a value for safe use as a single URL path segment."""
    return quote(value, safe="")


def _local_midnight(day: date, tz: tzinfo) -> datetime:
    """Midnight at the start of ``day`` in ``tz``."""
    return datetime(day.year, day.month, day.day, tzinfo=tz)


class CalendarError(Exception):
    """A Google Calendar API call failed."""


class CalendarAuthError(CalendarError):
    """Google rejected the refresh token; a human must re-authorize."""


def event_id_for(workout_id: str) -> str:
    """Build a stable, API-legal event id for a TrainingPeaks workout id.

    TrainingPeaks ids are numeric, which is already valid base32hex, so the
    common case is a readable ``tpplan<workout id>``. Anything unexpected is
    encoded character-by-character so the function is total.
    """
    body = workout_id.strip().lower()
    if body and all(c in _BASE32HEX for c in body):
        return f"{_EVENT_ID_PREFIX}{body}"
    # Fallback: hex-encode each byte as two characters. Masking a byte down to a
    # single character instead would be lossy, letting two different workouts
    # collide onto one event and silently overwrite each other.
    encoded = workout_id.encode("utf-8").hex()
    return f"{_EVENT_ID_PREFIX}{encoded}"


@dataclass(frozen=True)
class SyncedEvent:
    """An existing calendar event previously created by this tool."""

    event_id: str
    workout_id: str | None
    summary: str
    description: str | None
    start: str | None
    end: str | None


class CalendarClient:
    """Async Google Calendar client for a single calendar."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        calendar_id: str,
        timeout: float = REQUEST_TIMEOUT,
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self.calendar_id = calendar_id
        # Pre-quoted for use in request paths, so a calendar id containing a
        # slash or query character cannot reshape the URL.
        self._cal = _seg(calendar_id)
        self._client = httpx.AsyncClient(timeout=timeout)
        self._access_token: str | None = None
        self._expires_at: float = 0.0

    async def __aenter__(self) -> "CalendarClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def _token(self) -> str:
        if self._access_token and time.time() < self._expires_at - TOKEN_REFRESH_BUFFER:
            return self._access_token

        response = await self._client.post(
            TOKEN_URL,
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": self._refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if response.status_code != 200:
            raise CalendarAuthError(
                f"Google refused the refresh token (HTTP {response.status_code}): {response.text[:300]}. "
                "Re-run scripts/google_oauth_setup.py and update GOOGLE_REFRESH_TOKEN."
            )

        payload = response.json()
        access_token = payload.get("access_token")
        if not access_token:
            raise CalendarAuthError("Google token response contained no access_token.")

        self._access_token = access_token
        self._expires_at = time.time() + float(payload.get("expires_in", 3600))
        return access_token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        retry_on_401: bool = True,
        allow_status: tuple[int, ...] = (),
    ) -> httpx.Response:
        token = await self._token()
        response = await self._client.request(
            method,
            f"{API_BASE}{path}",
            params=params,
            json=json,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )

        if response.status_code == 401 and retry_on_401:
            self._access_token = None
            return await self._request(
                method, path, params=params, json=json, retry_on_401=False, allow_status=allow_status
            )

        if response.status_code == 401:
            raise CalendarAuthError("Google returned 401 after refreshing the access token.")
        if response.status_code in allow_status or response.is_success:
            return response

        raise CalendarError(f"{method} {path} failed with HTTP {response.status_code}: {response.text[:300]}")

    async def calendar_timezone(self) -> str:
        """Return the target calendar's own IANA timezone."""
        response = await self._request("GET", f"/calendars/{self._cal}")
        return response.json().get("timeZone") or "UTC"

    async def list_synced_events(self, start: date, end: date, tz: tzinfo) -> list[SyncedEvent]:
        """List events in the window that this tool created.

        Filtered server-side on the private marker property, so events created by
        anything else are never returned and therefore never pruned.

        The bounds are local midnight on ``start`` through local midnight after
        ``end``, expressed in ``tz``. Using the calendar's own timezone rather
        than UTC keeps the listed set exactly equal to the synced window — which
        matters because anything listed here and no longer wanted gets pruned,
        and the window deliberately begins today so that past workouts are left
        on the calendar as history.
        """
        events: list[SyncedEvent] = []
        page_token: str | None = None

        while True:
            params: dict[str, Any] = {
                "timeMin": _local_midnight(start, tz).isoformat(),
                "timeMax": _local_midnight(end + timedelta(days=1), tz).isoformat(),
                "privateExtendedProperty": f"{MARKER_KEY}={MARKER_VALUE}",
                "singleEvents": "true",
                "showDeleted": "false",
                "maxResults": 2500,
            }
            if page_token:
                params["pageToken"] = page_token

            response = await self._request("GET", f"/calendars/{self._cal}/events", params=params)
            payload = response.json()

            for item in payload.get("items", []):
                private = (item.get("extendedProperties") or {}).get("private") or {}
                events.append(
                    SyncedEvent(
                        event_id=item.get("id", ""),
                        workout_id=private.get(WORKOUT_ID_KEY),
                        summary=item.get("summary") or "",
                        description=item.get("description"),
                        start=(item.get("start") or {}).get("dateTime"),
                        end=(item.get("end") or {}).get("dateTime"),
                    )
                )

            page_token = payload.get("nextPageToken")
            if not page_token:
                break

        return events

    def build_event_body(
        self,
        *,
        workout_id: str,
        summary: str,
        description: str | None,
        start: datetime,
        end: datetime,
        timezone: str,
    ) -> dict[str, Any]:
        """Build the Calendar event payload for a workout.

        ``start``/``end`` are naive wall-clock times; ``timezone`` tells Google
        how to interpret them, which keeps the event correct across DST.
        """
        return {
            "summary": summary,
            "description": description or "",
            "start": {"dateTime": start.isoformat(timespec="seconds"), "timeZone": timezone},
            "end": {"dateTime": end.isoformat(timespec="seconds"), "timeZone": timezone},
            "extendedProperties": {
                "private": {MARKER_KEY: MARKER_VALUE, WORKOUT_ID_KEY: workout_id},
            },
        }

    async def upsert_event(self, event_id: str, body: dict[str, Any]) -> str:
        """Create the event, or overwrite it if that id already exists.

        Returns "created" or "updated".
        """
        insert_body = {**body, "id": event_id}
        response = await self._request(
            "POST",
            f"/calendars/{self._cal}/events",
            json=insert_body,
            allow_status=(409,),
        )
        if response.status_code != 409:
            return "created"

        # The id is already taken — either a live event we previously created, or
        # one that was cancelled. A full PUT covers both, reviving a cancelled
        # event in the latter case.
        await self._request("PUT", f"/calendars/{self._cal}/events/{_seg(event_id)}", json=body)
        return "updated"

    async def delete_event(self, event_id: str) -> None:
        """Delete an event, tolerating one that is already gone."""
        await self._request(
            "DELETE",
            f"/calendars/{self._cal}/events/{_seg(event_id)}",
            allow_status=(404, 410),
        )
