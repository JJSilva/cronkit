"""The HTTP surface: a health endpoint for the platform, plus tool control.

Railway's healthcheck needs something listening on ``$PORT``, so the scheduler
runs as background tasks alongside a tiny Starlette app.

Security note: a Railway service is reachable from the public internet. Only
``/health`` is anonymous, and it deliberately returns nothing but liveness — no
tool names, no calendar address, no error text. Everything revealing sits behind
``CRONKIT_API_TOKEN``.

Routes
------
``GET  /``, ``GET /health``     anonymous liveness
``GET  /status``                daemon + every tool's state
``GET  /tools``                 the loaded tools and their schedules
``GET  /tools/{name}``          one tool's state
``POST /tools/{name}/run``      run one tool now (``?dry_run=1`` to preview)
``POST /sync``                  deprecated alias for the calendar tool
"""

import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from cronkit.core.config import DaemonConfig
from cronkit.core.daemon import Daemon, ToolRunner, ToolUnavailableError

logger = logging.getLogger(__name__)

# The pre-cronkit deployment posted to /sync to force a calendar sync. Kept as
# an alias so an existing cron job or bookmark does not silently start 404ing.
LEGACY_SYNC_TOOL = "trainingpeaks-calendar"


def _presented_token(request: Request) -> str:
    """Pull the caller's token from either supported header."""
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value:
        return value.strip()
    # X-Sync-Token is the pre-cronkit header name, still accepted.
    return (request.headers.get("x-cronkit-token") or request.headers.get("x-sync-token") or "").strip()


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


def create_app(daemon: Daemon | None = None) -> Starlette:
    """Build the ASGI app with its background sync loops."""
    daemon = daemon or Daemon.from_env()
    token = daemon.config.api_token

    if not token:
        logger.warning(
            "CRONKIT_API_TOKEN is not set: every endpoint except /health will refuse "
            "every request. Set it to enable them."
        )

    def _deny() -> JSONResponse:
        # Identical response whether the token is absent, wrong, or unconfigured,
        # so probing cannot distinguish the cases.
        return JSONResponse({"status": "unauthorized"}, status_code=401)

    def _resolve(request: Request, name: str | None = None) -> ToolRunner | JSONResponse:
        """Authorize, then look up the requested tool."""
        if not is_authorized(request, token):
            return _deny()
        runner = daemon.runner(name or request.path_params["name"])
        if runner is None:
            return JSONResponse({"status": "not_found", "message": "No such tool."}, status_code=404)
        return runner

    async def health(_: Request) -> JSONResponse:
        # Anonymous and deliberately empty of detail. It reports that the process
        # is up, nothing about which tools are loaded or what failed.
        #
        # It stays 200 even after a failed run: an expired upstream credential
        # should not make Railway restart-loop a container that cannot fix
        # itself. Use /status to see whether runs are actually succeeding.
        return JSONResponse({"status": "ok"})

    async def status(request: Request) -> JSONResponse:
        if not is_authorized(request, token):
            return _deny()
        return JSONResponse(daemon.status())

    async def list_tools(request: Request) -> JSONResponse:
        if not is_authorized(request, token):
            return _deny()
        return JSONResponse(
            {
                "status": "ok",
                "tools": [
                    {
                        "name": runner.name,
                        "summary": runner.summary,
                        "state": runner.state,
                        "schedule": runner.schedule.as_dict() if runner.schedule else None,
                    }
                    for runner in daemon.runners
                ],
            }
        )

    async def tool_status(request: Request) -> JSONResponse:
        resolved = _resolve(request)
        if isinstance(resolved, JSONResponse):
            return resolved
        return JSONResponse({"status": "ok", **resolved.status()})

    async def _run(request: Request, runner: ToolRunner) -> JSONResponse:
        dry_run = request.query_params.get("dry_run", "").lower() in {"1", "true", "yes"}
        try:
            result = await runner.run_once(dry_run=dry_run)
        except ToolUnavailableError:
            return JSONResponse(
                {"status": "unavailable", "message": "Tool is not configured; see service logs."},
                status_code=503,
            )
        except Exception:
            # The exception text can quote upstream API error bodies, so it is
            # logged for the operator rather than returned to the caller.
            logger.exception("Triggered run of %s failed", runner.name)
            return JSONResponse(
                {"status": "error", "message": "Run failed; see service logs."},
                status_code=500,
            )
        return JSONResponse({"status": "ok", "tool": runner.name, **result.as_dict()})

    async def run_tool(request: Request) -> JSONResponse:
        resolved = _resolve(request)
        if isinstance(resolved, JSONResponse):
            return resolved
        return await _run(request, resolved)

    async def legacy_sync(request: Request) -> JSONResponse:
        resolved = _resolve(request, LEGACY_SYNC_TOOL)
        if isinstance(resolved, JSONResponse):
            return resolved
        logger.info("POST /sync is deprecated; use POST /tools/%s/run", LEGACY_SYNC_TOOL)
        return await _run(request, resolved)

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        await daemon.start()
        try:
            yield
        finally:
            await daemon.stop()

    return Starlette(
        routes=[
            Route("/", health),
            Route("/health", health),
            Route("/status", status),
            Route("/tools", list_tools),
            Route("/tools/{name}", tool_status),
            Route("/tools/{name}/run", run_tool, methods=["POST"]),
            Route("/sync", legacy_sync, methods=["POST"]),
        ],
        lifespan=lifespan,
    )


def create_app_from_env(config: DaemonConfig | None = None) -> Starlette:
    """Entry point for ``uvicorn cronkit.core.server:create_app_from_env --factory``."""
    return create_app(Daemon.from_env(config))
