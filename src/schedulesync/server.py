"""Long-running service: a sync loop plus HTTP endpoints for Railway.

Railway's healthcheck needs something listening on $PORT, so the scheduler runs
as a background task alongside a tiny Starlette app.

Security note: a Railway service is reachable from the public internet. Only
``/health`` is anonymous, and it deliberately returns nothing but liveness — no
calendar address, no workout titles, no error text. Everything revealing sits
behind ``SYNC_API_TOKEN``.
"""

import asyncio
import contextlib
import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from schedulesync.config import Config
from schedulesync.sync import SyncResult, run_sync

logger = logging.getLogger(__name__)


def _result_payload(result: SyncResult) -> dict[str, Any]:
    return {
        "window": {"start": result.window_start.isoformat(), "end": result.window_end.isoformat()},
        "timezone": result.timezone,
        "dry_run": result.dry_run,
        "workouts": {
            "total": result.total_workouts,
            "timed": result.timed_workouts,
            "skipped_untimed": result.skipped_untimed,
        },
        "created": result.created,
        "updated": result.updated,
        "unchanged": result.unchanged,
        "deleted": result.deleted,
        "errors": result.errors,
    }


def _presented_token(request: Request) -> str:
    """Pull the caller's token from either supported header."""
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value:
        return value.strip()
    return request.headers.get("x-sync-token", "").strip()


def is_authorized(request: Request, expected: str) -> bool:
    """Constant-time check of the caller's token.

    Fails closed: with no token configured there is no way to authorize, so the
    protected endpoints stay shut rather than falling open to the internet.
    """
    if not expected:
        return False
    presented = _presented_token(request)
    if not presented:
        return False
    return secrets.compare_digest(presented, expected)


class SyncService:
    """Owns the periodic sync task and the most recent run's status."""

    def __init__(self, config: Config):
        self.config = config
        self.last_run_at: datetime | None = None
        self.last_result: SyncResult | None = None
        self.last_error: str | None = None
        self.next_sync_at: datetime | None = None
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

    async def sync_once(self, *, dry_run: bool = False) -> SyncResult:
        """Run one sync, serialized so overlapping triggers cannot interleave."""
        async with self._lock:
            try:
                result = await run_sync(self.config, dry_run=dry_run)
            except Exception as exc:
                self.last_run_at = datetime.now(UTC)
                self.last_error = str(exc)
                raise
            self.last_run_at = datetime.now(UTC)
            self.last_result = result
            self.last_error = None
            logger.info("sync: %s", result.summary_line())
            return result

    async def _loop(self) -> None:
        while True:
            try:
                await self.sync_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Never let one bad run kill the loop; the next tick retries.
                logger.exception("Scheduled sync failed")
            # Drawn fresh each time, so the cadence does not settle into an
            # exact rhythm.
            delay = self.config.next_interval_seconds()
            self.next_sync_at = datetime.now(UTC) + timedelta(seconds=delay)
            logger.info("next sync in %.1f minutes", delay / 60)
            await asyncio.sleep(delay)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None


def create_app(config: Config | None = None) -> Starlette:
    """Build the ASGI app with its background sync loop."""
    service = SyncService(config or Config.from_env())
    token = service.config.api_token

    if not token:
        logger.warning(
            "SYNC_API_TOKEN is not set: /status and /sync will refuse every request. "
            "Set it to enable them."
        )

    def _deny() -> JSONResponse:
        # Identical response whether the token is absent, wrong, or unconfigured,
        # so probing cannot distinguish the cases.
        return JSONResponse({"status": "unauthorized"}, status_code=401)

    async def health(_: Request) -> JSONResponse:
        # Anonymous and deliberately empty of detail. It reports that the process
        # is up, nothing about the athlete, the calendar, or what failed.
        #
        # It stays 200 even after a failed sync: an expired TrainingPeaks cookie
        # should not make Railway restart-loop a container that cannot fix
        # itself. Use /status to see whether runs are actually succeeding.
        return JSONResponse({"status": "ok"})

    async def status(request: Request) -> JSONResponse:
        if not is_authorized(request, token):
            return _deny()
        return JSONResponse(
            {
                "status": "ok",
                "calendar_id": service.config.calendar_id,
                "interval": service.config.interval_label,
                "interval_min_minutes": service.config.interval_min_minutes,
                "interval_max_minutes": service.config.interval_max_minutes,
                "last_run_at": service.last_run_at.isoformat() if service.last_run_at else None,
                "next_sync_at": service.next_sync_at.isoformat() if service.next_sync_at else None,
                "last_error": service.last_error,
                "last_result": _result_payload(service.last_result) if service.last_result else None,
            }
        )

    async def trigger(request: Request) -> JSONResponse:
        if not is_authorized(request, token):
            return _deny()
        dry_run = request.query_params.get("dry_run", "").lower() in {"1", "true", "yes"}
        try:
            result = await service.sync_once(dry_run=dry_run)
        except Exception:
            # The exception text can quote upstream API error bodies, so it is
            # logged for the operator rather than returned to the caller.
            logger.exception("Triggered sync failed")
            return JSONResponse({"status": "error", "message": "Sync failed; see service logs."}, status_code=500)
        return JSONResponse({"status": "ok", **_result_payload(result)})

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        await service.start()
        try:
            yield
        finally:
            await service.stop()

    return Starlette(
        routes=[
            Route("/", health),
            Route("/health", health),
            Route("/status", status),
            Route("/sync", trigger, methods=["POST"]),
        ],
        lifespan=lifespan,
    )
