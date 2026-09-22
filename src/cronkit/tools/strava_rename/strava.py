"""Minimal Strava API client.

Only what the rename needs: refresh an access token, list activities in a time
range, and change an activity's name.

Auth is Strava's standard OAuth refresh flow. The refresh token must carry the
``activity:read_all`` scope (so private activities are listed too) and
``activity:write`` (so they can be renamed); ``scripts/strava_oauth_setup.py``
mints one with both.

Strava *may* hand back a different refresh token on refresh, in which case the
old one stops working. The daemon keeps no state on disk, so the newest token is
held in memory for the life of the process and a warning is logged — see
:attr:`StravaClient.refresh_token`.
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

API_BASE = "https://www.strava.com/api/v3"
TOKEN_URL = "https://www.strava.com/oauth/token"
TOKEN_REFRESH_BUFFER = 60
REQUEST_TIMEOUT = 30.0
PAGE_SIZE = 100
MAX_PAGES = 5  # 500 activities: far beyond any window this tool asks for

logger = logging.getLogger(__name__)


class StravaError(Exception):
    """A Strava API call failed."""


class StravaAuthError(StravaError):
    """The refresh token was rejected, or lacks a scope this tool needs.

    Raised separately because it is the one failure that needs a human: run
    ``scripts/strava_oauth_setup.py`` again and update the Railway variable.
    """


@dataclass(frozen=True)
class Activity:
    """A Strava activity, reduced to what matching and renaming need."""

    id: str
    name: str
    sport_type: str
    # Naive local wall-clock time from ``start_date_local``. Strava suffixes it
    # with ``Z`` even though it is not UTC; the suffix is discarded.
    start_local: datetime


def parse_activity(raw: dict[str, Any]) -> Activity | None:
    """Convert a raw summary activity. Returns None if it cannot be matched."""
    activity_id = raw.get("id")
    start = raw.get("start_date_local")
    if activity_id is None or not isinstance(start, str):
        return None
    try:
        start_local = datetime.fromisoformat(start.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        logger.warning("Unparseable Strava start_date_local: %r", start)
        return None
    return Activity(
        id=str(activity_id),
        name=str(raw.get("name") or ""),
        sport_type=str(raw.get("sport_type") or raw.get("type") or ""),
        start_local=start_local,
    )


class StravaClient:
    """Async Strava client for the authenticated athlete."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        *,
        timeout: float = REQUEST_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        #: The newest refresh token seen. Callers that outlive one client should
        #: read this back and pass it to the next, in case Strava rotated it.
        self.refresh_token = refresh_token
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)
        self._access_token: str | None = None
        self._expires_at: float = 0.0

    async def __aenter__(self) -> "StravaClient":
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
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            },
        )
        if response.status_code in (400, 401):
            raise StravaAuthError(
                f"Strava rejected the refresh token (HTTP {response.status_code}). Run "
                "scripts/strava_oauth_setup.py again and update STRAVA_RENAME_REFRESH_TOKEN."
            )
        if response.status_code != 200:
            raise StravaError(f"Strava token refresh failed with HTTP {response.status_code}")

        payload = response.json() or {}
        access_token = payload.get("access_token")
        if not access_token:
            raise StravaAuthError("Strava token refresh returned no access_token.")

        rotated = payload.get("refresh_token")
        if rotated and rotated != self.refresh_token:
            logger.warning(
                "Strava issued a new refresh token; it is kept in memory for now, but update "
                "STRAVA_RENAME_REFRESH_TOKEN before the next redeploy or the tool will stop authenticating."
            )
            self.refresh_token = rotated

        self._access_token = access_token
        self._expires_at = float(payload.get("expires_at") or time.time() + 3600)
        return access_token

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        retry_on_401: bool = True,
    ) -> httpx.Response:
        token = await self._token()
        response = await self._client.request(
            method,
            f"{API_BASE}{endpoint}",
            params=params,
            json=json,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )

        if response.status_code == 401 and retry_on_401:
            # Token may have expired mid-flight; drop it and try once more.
            self._access_token = None
            return await self._request(method, endpoint, params=params, json=json, retry_on_401=False)

        if response.status_code in (401, 403):
            # A 401 after a fresh token, or a 403 on write, means a missing scope.
            raise StravaAuthError(
                f"Strava refused {method} {endpoint} (HTTP {response.status_code}). The refresh token "
                "probably lacks activity:read_all or activity:write — rerun scripts/strava_oauth_setup.py."
            )
        if response.status_code == 429:
            raise StravaError("Strava rate limit reached (HTTP 429); the next run will retry.")
        if not response.is_success:
            raise StravaError(f"{method} {endpoint} failed with HTTP {response.status_code}")

        return response

    async def activities(self, after: datetime, before: datetime) -> list[Activity]:
        """List activities that started between two aware datetimes."""
        activities: list[Activity] = []
        for page in range(1, MAX_PAGES + 1):
            response = await self._request(
                "GET",
                "/athlete/activities",
                params={
                    "after": int(after.timestamp()),
                    "before": int(before.timestamp()),
                    "per_page": PAGE_SIZE,
                    "page": page,
                },
            )
            batch = response.json()
            if not isinstance(batch, list):
                break
            activities.extend(a for a in (parse_activity(r) for r in batch if isinstance(r, dict)) if a)
            if len(batch) < PAGE_SIZE:
                break
        return activities

    async def rename(self, activity_id: str, name: str) -> None:
        """Change one activity's name. Nothing else on it is touched."""
        await self._request("PUT", f"/activities/{activity_id}", json={"name": name})
