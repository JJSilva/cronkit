"""HTTP surface: what a public Railway deployment exposes, and to whom."""

import pytest
from starlette.testclient import TestClient

from cronkit.core.config import DaemonConfig
from cronkit.core.daemon import Daemon, ToolRunner
from cronkit.core.schedule import Schedule
from cronkit.core.server import create_app

TOKEN = "s3cret-token-value"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def tool_cls(fake_tool_cls):
    class Calendar(fake_tool_cls):
        # The real tool's name, so the deprecated /sync alias resolves.
        name = "trainingpeaks-calendar"
        summary = "Sync timed TrainingPeaks workouts into Google Calendar."

        def status(self):
            return {"calendar_id": "athlete@example.com"}

    return Calendar


@pytest.fixture
def daemon(tool_cls):
    # Built by hand rather than from_env() so no test can reach the real
    # TrainingPeaks or Google APIs.
    runner = ToolRunner(tool_cls, tool=tool_cls(), schedule=Schedule(min_minutes=60, max_minutes=60))
    return Daemon(DaemonConfig(api_token=TOKEN), [runner])


@pytest.fixture
def client(daemon):
    # Constructed without the context manager on purpose: that skips the
    # lifespan, so the background loops never start.
    return TestClient(create_app(daemon))


# --- the anonymous endpoint ------------------------------------------------


def test_health_is_anonymous_and_reports_liveness(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_leaks_nothing_about_the_tools(client):
    """A public URL must not disclose what this daemon does or for whom."""
    body = client.get("/health").text
    assert "athlete@example.com" not in body
    assert "trainingpeaks" not in body.lower()
    for leaky in ("tools", "last_error", "last_result"):
        assert leaky not in body


def test_root_serves_the_dashboard_not_health(client):
    """`/` became the dashboard; Railway's healthcheck uses /health."""
    response = client.get("/")
    assert response.headers["content-type"].startswith("text/html")


def test_health_still_works_when_no_token_is_configured(tool_cls):
    """Railway's healthcheck must not depend on the secret being set."""
    daemon = Daemon(DaemonConfig(api_token=""), [ToolRunner.load(tool_cls)])
    assert TestClient(create_app(daemon)).get("/health").status_code == 200


# --- authorization ---------------------------------------------------------

PROTECTED = [
    ("/status", "get"),
    ("/tools", "get"),
    ("/tools/trainingpeaks-calendar", "get"),
    ("/tools/trainingpeaks-calendar/run", "post"),
    ("/sync", "post"),
]


@pytest.mark.parametrize("path,method", PROTECTED)
def test_protected_endpoints_reject_anonymous_callers(client, path, method):
    assert getattr(client, method)(path).status_code == 401


@pytest.mark.parametrize("path,method", PROTECTED)
def test_protected_endpoints_reject_a_wrong_token(client, path, method):
    response = getattr(client, method)(path, headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": f"Bearer {TOKEN}"},
        {"X-Cronkit-Token": TOKEN},
        # The pre-cronkit header name, still accepted.
        {"X-Sync-Token": TOKEN},
    ],
)
def test_status_accepts_every_supported_header_form(client, headers):
    assert client.get("/status", headers=headers).status_code == 200


def test_an_unconfigured_token_fails_closed(tool_cls):
    """With no token set, the protected endpoints must not fall open."""
    daemon = Daemon(DaemonConfig(api_token=""), [ToolRunner.load(tool_cls)])
    anonymous = TestClient(create_app(daemon))

    assert anonymous.get("/status").status_code == 401
    assert anonymous.post("/tools/trainingpeaks-calendar/run").status_code == 401
    # Not even a guessed token opens it.
    assert anonymous.get("/status", headers={"Authorization": "Bearer anything"}).status_code == 401


def test_a_token_prefix_is_not_accepted(client):
    response = client.get("/status", headers={"Authorization": f"Bearer {TOKEN[:-1]}"})
    assert response.status_code == 401


def test_an_unknown_tool_is_not_disclosed_to_anonymous_callers(client):
    """404 vs 401 would confirm which tools exist; authorization comes first."""
    assert client.get("/tools/nope").status_code == 401
    assert client.get("/tools/nope", headers=AUTH).status_code == 404


# --- listing and status ----------------------------------------------------


def test_tools_lists_what_is_loaded(client):
    payload = client.get("/tools", headers=AUTH).json()

    assert [t["name"] for t in payload["tools"]] == ["trainingpeaks-calendar"]
    assert payload["tools"][0]["schedule"]["label"] == "every 60 min"


def test_status_reports_each_tools_configuration(client):
    payload = client.get("/status", headers=AUTH).json()

    assert payload["tools"][0]["config"] == {"calendar_id": "athlete@example.com"}
    assert payload["tools"][0]["state"] == "scheduled"


def test_one_tools_status_is_addressable(client):
    payload = client.get("/tools/trainingpeaks-calendar", headers=AUTH).json()

    assert payload["name"] == "trainingpeaks-calendar"


# --- triggered runs --------------------------------------------------------


def test_an_authorized_run_reports_what_happened(client):
    payload = client.post("/tools/trainingpeaks-calendar/run", headers=AUTH).json()

    assert payload["ok"] is True
    assert payload["tool"] == "trainingpeaks-calendar"
    assert payload["details"]["items"] == ["one"]


def test_dry_run_is_passed_through(client, daemon):
    client.post("/tools/trainingpeaks-calendar/run?dry_run=1", headers=AUTH)

    assert daemon.runners[0].tool.runs == [True]


def test_a_failed_run_does_not_leak_upstream_error_text(client, tool_cls):
    """Exception text can quote upstream API bodies; it belongs in the logs."""
    tool_cls.explode = True
    response = client.post("/tools/trainingpeaks-calendar/run", headers=AUTH)

    assert response.status_code == 500
    assert "abc123" not in response.text
    assert "client_secret" not in response.text
    assert response.json()["message"] == "Run failed; see service logs."


def test_running_an_unconfigured_tool_is_a_503(tool_cls):
    tool_cls.unconfigured = True
    daemon = Daemon(DaemonConfig(api_token=TOKEN), [ToolRunner.load(tool_cls)])
    client = TestClient(create_app(daemon))

    response = client.post("/tools/trainingpeaks-calendar/run", headers=AUTH)
    assert response.status_code == 503
    # The load error names a variable; that belongs in the logs, not the body.
    assert "FAKE_TOKEN" not in response.text


def test_run_rejects_get(client):
    assert client.get("/tools/trainingpeaks-calendar/run").status_code == 405


# --- the deprecated alias --------------------------------------------------


def test_the_old_sync_endpoint_still_triggers_the_calendar_tool(client, daemon):
    """An existing cron job posting to /sync must not silently start 404ing."""
    response = client.post("/sync", headers=AUTH)

    assert response.status_code == 200
    assert response.json()["tool"] == "trainingpeaks-calendar"
    assert daemon.runners[0].tool.runs == [False]


def test_the_old_sync_endpoint_is_absent_when_that_tool_is_not_loaded(fake_tool_cls):
    daemon = Daemon(DaemonConfig(api_token=TOKEN), [ToolRunner.load(fake_tool_cls)])
    response = TestClient(create_app(daemon)).post("/sync", headers=AUTH)

    assert response.status_code == 404
