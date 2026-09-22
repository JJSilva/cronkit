"""The HTTP surface: a dashboard, a health endpoint, and tool control.

Railway's healthcheck needs something listening on ``$PORT``, so the scheduler
runs as background tasks alongside a tiny Starlette app.

Security note: a Railway service is reachable from the public internet. Only
``/health`` is anonymous, and it deliberately returns nothing but liveness — no
tool names, no calendar address, no error text. Everything else needs either
``CRONKIT_API_TOKEN`` (for scripts) or a dashboard session from
``CRONKIT_DASHBOARD_PASSWORD``. Both fail closed when unset.

Routes
------
``GET  /``                      the dashboard, or the password form
``POST /login`` ``/logout``     dashboard session
``GET  /health``                anonymous liveness
``GET  /status``                daemon + every tool's state
``GET  /api/logs``              the in-memory log tail
``GET  /tools`` ``/tools/{n}``  the loaded tools
``POST /tools/{name}/run``      run one tool now (``?dry_run=1`` to preview)
``POST /sync``                  deprecated alias for the calendar tool
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import parse_qs

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from cronkit.core import auth, dashboard, logbuffer
from cronkit.core.daemon import Daemon, ToolRunner, ToolUnavailableError
from cronkit.core.logbuffer import LogBuffer

logger = logging.getLogger(__name__)

# The pre-cronkit deployment posted to /sync to force a calendar sync. Kept as
# an alias so an existing cron job or bookmark does not silently start 404ing.
LEGACY_SYNC_TOOL = "trainingpeaks-calendar"


def create_app(daemon: Daemon | None = None, *, logs: LogBuffer | None = None) -> Starlette:
    """Build the ASGI app with its background loops and its dashboard."""
    daemon = daemon or Daemon.from_env()
    logs = logs if logs is not None else logbuffer.install()
    token = daemon.config.api_token
    password = daemon.config.dashboard_password

    if not token and not password:
        logger.warning(
            "Neither CRONKIT_API_TOKEN nor CRONKIT_DASHBOARD_PASSWORD is set: every "
            "endpoint except /health will refuse every request."
        )

    def _deny() -> JSONResponse:
        # Identical response whether the credential is absent, wrong, or
        # unconfigured, so probing cannot distinguish the cases.
        return JSONResponse({"status": "unauthorized"}, status_code=401)

    def _authorized(request: Request) -> bool:
        return auth.is_authorized(request, token=token, password=password)

    def _resolve(request: Request, name: str | None = None) -> ToolRunner | JSONResponse:
        """Authorize, then look up the requested tool."""
        if not _authorized(request):
            return _deny()
        runner = daemon.runner(name or request.path_params["name"])
        if runner is None:
            return JSONResponse({"status": "not_found", "message": "No such tool."}, status_code=404)
        return runner

    def _secure_cookie(response: Response, name: str, value: str, *, max_age: int) -> None:
        # Secure is safe to set unconditionally: Railway terminates TLS, and a
        # local http:// run still works because browsers exempt localhost.
        response.set_cookie(
            name,
            value,
            max_age=max_age,
            httponly=name == auth.SESSION_COOKIE,
            secure=True,
            samesite="strict",
            path="/",
        )

    # --- the dashboard -----------------------------------------------------

    async def index(request: Request) -> Response:
        if not password:
            return HTMLResponse(dashboard.login_page("No dashboard password is configured."), status_code=503)
        if not auth.session_is_valid(password, request.cookies.get(auth.SESSION_COOKIE)):
            return HTMLResponse(dashboard.login_page(), status_code=401)

        csrf = request.cookies.get(auth.CSRF_COOKIE) or auth.issue_csrf()
        response = HTMLResponse(dashboard.dashboard_page(csrf))
        _secure_cookie(response, auth.CSRF_COOKIE, csrf, max_age=auth.DEFAULT_SESSION_HOURS * 3600)
        return response

    async def login(request: Request) -> Response:
        # Parsed by hand rather than via request.form(), which would drag in
        # python-multipart for a single urlencoded field.
        body = (await request.body()).decode("utf-8", "replace")
        presented = (parse_qs(body).get("password") or [""])[0]
        if not auth.password_is_correct(password, presented):
            logger.warning("Failed dashboard sign-in from %s", request.client.host if request.client else "?")
            return HTMLResponse(dashboard.login_page("Incorrect password."), status_code=401)

        response = RedirectResponse("/", status_code=303)
        _secure_cookie(response, auth.SESSION_COOKIE, auth.issue_session(password),
                       max_age=auth.DEFAULT_SESSION_HOURS * 3600)
        _secure_cookie(response, auth.CSRF_COOKIE, auth.issue_csrf(),
                       max_age=auth.DEFAULT_SESSION_HOURS * 3600)
        return response

    async def logout(_: Request) -> Response:
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(auth.SESSION_COOKIE, path="/")
        response.delete_cookie(auth.CSRF_COOKIE, path="/")
        return response

    # --- the API -----------------------------------------------------------

    async def health(_: Request) -> JSONResponse:
        # Anonymous and deliberately empty of detail. It reports that the process
        # is up, nothing about which tools are loaded or what failed.
        #
        # It stays 200 even after a failed run: an expired upstream credential
        # should not make Railway restart-loop a container that cannot fix
        # itself. Use /status to see whether runs are actually succeeding.
        return JSONResponse({"status": "ok"})

    async def status(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _deny()
        return JSONResponse(daemon.status())

    async def api_logs(request: Request) -> JSONResponse:
        if not _authorized(request):
            return _deny()
        try:
            limit = min(int(request.query_params.get("limit", "300")), 1000)
        except ValueError:
            limit = 300
        return JSONResponse({"records": logs.records(limit=limit, level=request.query_params.get("level"))})

    async def list_tools(request: Request) -> JSONResponse:
        if not _authorized(request):
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
        # A session-authenticated write needs CSRF cover; a token caller sets a
        # header no cross-site form can, so it is already immune.
        if not auth.token_is_valid(request, token) and not auth.csrf_is_valid(
            request, request.headers.get("x-csrf-token")
        ):
            return JSONResponse({"status": "forbidden", "message": "Bad CSRF token."}, status_code=403)

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
            Route("/", index),
            Route("/login", login, methods=["POST"]),
            Route("/logout", logout, methods=["POST"]),
            Route("/health", health),
            Route("/status", status),
            Route("/api/logs", api_logs),
            Route("/tools", list_tools),
            Route("/tools/{name}", tool_status),
            Route("/tools/{name}/run", run_tool, methods=["POST"]),
            Route("/sync", legacy_sync, methods=["POST"]),
        ],
        lifespan=lifespan,
    )
