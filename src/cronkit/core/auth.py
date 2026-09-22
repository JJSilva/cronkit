"""Who is allowed to call the protected endpoints.

Two ways in, both fail closed:

*Token* — ``Authorization: Bearer <CRONKIT_API_TOKEN>`` or ``X-Cronkit-Token``.
This is what curl and any scripted caller uses.

*Session* — a signed cookie the dashboard sets after the password form. The
cookie carries only an expiry and a signature over it; there is nothing in it
worth stealing and it cannot be extended by editing it, because the signature is
an HMAC keyed on the password itself. Changing the password therefore
invalidates every outstanding session, which is the behaviour you want from a
single-secret deployment.

Everything compares in constant time, and an unset secret authorizes nobody.
"""

import base64
import hashlib
import hmac
import logging
import secrets
import time

from starlette.requests import Request

logger = logging.getLogger(__name__)

SESSION_COOKIE = "cronkit_session"
CSRF_COOKIE = "cronkit_csrf"
DEFAULT_SESSION_HOURS = 12


def _sign(secret: str, payload: str) -> str:
    digest = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def issue_session(secret: str, *, hours: int = DEFAULT_SESSION_HOURS) -> str:
    """Mint a cookie value that is valid for ``hours``."""
    expires = str(int(time.time()) + hours * 3600)
    return f"{expires}.{_sign(secret, expires)}"


def session_is_valid(secret: str, cookie: str | None) -> bool:
    """Check a cookie's signature and expiry."""
    if not secret or not cookie:
        return False
    expires, _, signature = cookie.partition(".")
    if not expires or not signature:
        return False
    if not hmac.compare_digest(signature, _sign(secret, expires)):
        return False
    try:
        return int(expires) > time.time()
    except ValueError:
        return False


def _presented_token(request: Request) -> str:
    """Pull the caller's token from any supported header."""
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value:
        return value.strip()
    # X-Sync-Token is the pre-cronkit header name, still accepted.
    return (request.headers.get("x-cronkit-token") or request.headers.get("x-sync-token") or "").strip()


def token_is_valid(request: Request, expected: str) -> bool:
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


def is_authorized(request: Request, *, token: str, password: str) -> bool:
    """Whether this request may touch a protected endpoint, by either route."""
    if token_is_valid(request, token):
        return True
    return session_is_valid(password, request.cookies.get(SESSION_COOKIE))


def password_is_correct(password: str, presented: str) -> bool:
    if not password or not presented:
        return False
    return secrets.compare_digest(presented, password)


def issue_csrf() -> str:
    return secrets.token_urlsafe(32)


def csrf_is_valid(request: Request, presented: str | None) -> bool:
    """Double-submit check: the form value must match the cookie.

    Only required for session-authenticated writes. A token caller sends an
    explicit header that a cross-site form cannot set, so it is already immune.
    """
    cookie = request.cookies.get(CSRF_COOKIE)
    if not cookie or not presented:
        return False
    return secrets.compare_digest(cookie, presented)
