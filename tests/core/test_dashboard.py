"""The dashboard: who gets in, and what a session may do once inside."""

import logging

import pytest
from starlette.testclient import TestClient

from cronkit.core import auth
from cronkit.core.config import DaemonConfig
from cronkit.core.daemon import Daemon, ToolRunner
from cronkit.core.logbuffer import LogBuffer
from cronkit.core.schedule import Schedule
from cronkit.core.server import create_app

PASSWORD = "correct-horse-battery-staple"
TOKEN = "s3cret-token-value"


@pytest.fixture
def logs() -> LogBuffer:
    return LogBuffer(capacity=50)


@pytest.fixture
def daemon(fake_tool_cls):
    runner = ToolRunner(fake_tool_cls, tool=fake_tool_cls(), schedule=Schedule(min_minutes=60, max_minutes=60))
    return Daemon(DaemonConfig(api_token=TOKEN, dashboard_password=PASSWORD), [runner])


@pytest.fixture
def client(daemon, logs):
    # https, because the session cookie is Secure and a cookie jar will not send
    # it over plain http. Railway terminates TLS, so this is the real shape.
    return TestClient(create_app(daemon, logs=logs), base_url="https://testserver")


def sign_in(client) -> None:
    response = client.post("/login", data={"password": PASSWORD}, follow_redirects=False)
    assert response.status_code == 303


# --- the password gate -----------------------------------------------------


def test_the_dashboard_is_closed_to_anonymous_visitors(client):
    response = client.get("/")
    assert response.status_code == 401
    assert "Password" in response.text


def test_a_wrong_password_is_refused(client):
    response = client.post("/login", data={"password": "guess"}, follow_redirects=False)
    assert response.status_code == 401
    assert auth.SESSION_COOKIE not in response.cookies


def test_the_right_password_opens_it(client):
    sign_in(client)
    page = client.get("/")

    assert page.status_code == 200
    assert "cronkit" in page.text


def test_the_page_never_contains_the_password(client):
    sign_in(client)
    assert PASSWORD not in client.get("/").text


def test_signing_out_closes_it_again(client):
    sign_in(client)
    client.post("/logout", follow_redirects=False)

    assert client.get("/").status_code == 401


def test_with_no_password_configured_the_dashboard_refuses_everyone(fake_tool_cls, logs):
    """Unset means shut, never open — the same rule the API follows."""
    daemon = Daemon(DaemonConfig(api_token=TOKEN, dashboard_password=""), [ToolRunner.load(fake_tool_cls)])
    anonymous = TestClient(create_app(daemon, logs=logs), base_url="https://testserver")

    assert anonymous.get("/").status_code == 503
    assert anonymous.post("/login", data={"password": ""}, follow_redirects=False).status_code == 401


# --- what a session may reach ---------------------------------------------


def test_a_session_can_read_status(client):
    sign_in(client)
    payload = client.get("/status").json()

    assert [t["name"] for t in payload["tools"]] == ["fake"]


def test_a_session_can_read_the_logs(client, logs):
    logging.getLogger("cronkit.test").handle(
        logging.LogRecord("cronkit.test", logging.INFO, __file__, 1, "hello from a run", None, None)
    )
    logs.emit(logging.LogRecord("cronkit.test", logging.INFO, __file__, 1, "hello from a run", None, None))
    sign_in(client)

    records = client.get("/api/logs").json()["records"]
    assert any("hello from a run" in r["message"] for r in records)


def test_the_logs_are_not_readable_anonymously(client):
    assert client.get("/api/logs").status_code == 401


def test_a_session_can_force_a_run(client, daemon):
    sign_in(client)
    csrf = client.cookies[auth.CSRF_COOKIE]

    response = client.post("/tools/fake/run", headers={"X-CSRF-Token": csrf})

    assert response.status_code == 200
    assert daemon.runners[0].tool.runs == [False]


def test_a_session_can_force_a_dry_run(client, daemon):
    sign_in(client)
    csrf = client.cookies[auth.CSRF_COOKIE]

    client.post("/tools/fake/run?dry_run=1", headers={"X-CSRF-Token": csrf})

    assert daemon.runners[0].tool.runs == [True]


# --- CSRF ------------------------------------------------------------------


def test_a_session_run_without_the_csrf_token_is_refused(client, daemon):
    """A cross-site form can ride the cookie; it cannot set the header."""
    sign_in(client)

    response = client.post("/tools/fake/run")

    assert response.status_code == 403
    assert daemon.runners[0].tool.runs == []


def test_a_wrong_csrf_token_is_refused(client):
    sign_in(client)
    assert client.post("/tools/fake/run", headers={"X-CSRF-Token": "nope"}).status_code == 403


def test_a_token_caller_needs_no_csrf_token(client, daemon):
    """Scripts authenticate with a header no cross-site form can set."""
    response = client.post("/tools/fake/run", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 200
    assert daemon.runners[0].tool.runs == [False]


# --- session cookies -------------------------------------------------------


def test_the_session_cookie_is_locked_down(client):
    response = client.post("/login", data={"password": PASSWORD}, follow_redirects=False)
    header = response.headers["set-cookie"]

    assert "HttpOnly" in header
    assert "Secure" in header
    assert "SameSite=strict" in header.replace("samesite", "SameSite")


def test_a_forged_session_cookie_is_rejected(client):
    client.cookies.set(auth.SESSION_COOKIE, "99999999999.forged-signature")
    assert client.get("/").status_code == 401


def test_an_expired_session_is_rejected():
    assert not auth.session_is_valid(PASSWORD, f"1.{auth._sign(PASSWORD, '1')}")


def test_a_session_signed_with_another_password_is_rejected():
    """Changing the password must invalidate every outstanding session."""
    cookie = auth.issue_session("old-password")
    assert not auth.session_is_valid("new-password", cookie)


def test_a_session_does_not_authorize_when_no_password_is_set():
    assert not auth.session_is_valid("", auth.issue_session("anything"))
