"""HTTP surface: what a public Railway deployment exposes, and to whom."""

from datetime import date

import pytest
from starlette.testclient import TestClient

from schedulesync import server as server_module
from schedulesync.config import Config
from schedulesync.server import create_app
from schedulesync.sync import SyncResult

TOKEN = "s3cret-token-value"


def make_config(**overrides) -> Config:
    base = {
        "tp_auth_cookie": "cookie",
        "google_client_id": "id",
        "google_client_secret": "secret",
        "google_refresh_token": "refresh",
        "calendar_id": "athlete@example.com",
        "api_token": TOKEN,
    }
    base.update(overrides)
    return Config(**base)


def client(config: Config | None = None) -> TestClient:
    # Constructed without the context manager on purpose: that skips the
    # lifespan, so the background sync loop never starts and no test can reach
    # the real TrainingPeaks or Google APIs.
    return TestClient(create_app(config or make_config()))


def fake_result() -> SyncResult:
    result = SyncResult(window_start=date(2026, 9, 13), window_end=date(2026, 10, 4), timezone="UTC")
    result.created.append("2026-09-16 Track Cycling")
    return result


# --- the anonymous endpoint ------------------------------------------------


def test_health_is_anonymous_and_reports_liveness():
    response = client().get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_leaks_nothing_about_the_athlete():
    """A public URL must not disclose the calendar address or training data."""
    body = client().get("/health").text
    assert "athlete@example.com" not in body
    assert "Track Cycling" not in body
    for leaky in ("calendar_id", "last_error", "last_result"):
        assert leaky not in body


def test_root_matches_health():
    assert client().get("/").json() == {"status": "ok"}


# --- authorization ---------------------------------------------------------


@pytest.mark.parametrize("path,method", [("/status", "get"), ("/sync", "post")])
def test_protected_endpoints_reject_anonymous_callers(path, method):
    response = getattr(client(), method)(path)
    assert response.status_code == 401


@pytest.mark.parametrize("path,method", [("/status", "get"), ("/sync", "post")])
def test_protected_endpoints_reject_a_wrong_token(path, method):
    response = getattr(client(), method)(path, headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": f"Bearer {TOKEN}"},
        {"X-Sync-Token": TOKEN},
    ],
)
def test_status_accepts_either_header_form(headers):
    response = client().get("/status", headers=headers)
    assert response.status_code == 200
    assert response.json()["calendar_id"] == "athlete@example.com"


def test_an_unconfigured_token_fails_closed():
    """With no token set, the protected endpoints must not fall open."""
    anonymous = client(make_config(api_token=""))
    assert anonymous.get("/status").status_code == 401
    assert anonymous.post("/sync").status_code == 401
    # Not even a guessed token opens it.
    assert anonymous.get("/status", headers={"Authorization": "Bearer anything"}).status_code == 401


def test_a_token_prefix_is_not_accepted():
    response = client().get("/status", headers={"Authorization": f"Bearer {TOKEN[:-1]}"})
    assert response.status_code == 401


def test_health_still_works_when_no_token_is_configured():
    """Railway's healthcheck must not depend on the secret being set."""
    assert client(make_config(api_token="")).get("/health").status_code == 200


# --- triggered sync --------------------------------------------------------


def test_authorized_sync_runs_and_reports(monkeypatch):
    async def fake_run_sync(config, *, dry_run=False, today=None):
        return fake_result()

    monkeypatch.setattr(server_module, "run_sync", fake_run_sync)
    response = client().post("/sync", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 200
    assert response.json()["created"] == ["2026-09-16 Track Cycling"]


def test_sync_failure_does_not_leak_upstream_error_text(monkeypatch):
    """Exception text can quote upstream API bodies; it belongs in the logs."""

    async def boom(config, *, dry_run=False, today=None):
        raise RuntimeError("Google refused the refresh token: client_secret=abc123")

    monkeypatch.setattr(server_module, "run_sync", boom)
    response = client().post("/sync", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 500
    assert "abc123" not in response.text
    assert "client_secret" not in response.text
    assert response.json()["message"] == "Sync failed; see service logs."


def test_sync_rejects_get():
    assert client().get("/sync").status_code == 405
