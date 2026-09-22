#!/usr/bin/env python3
"""One-time helper: turn a Google OAuth client into a long-lived refresh token.

Run this once on your laptop. It opens a browser, asks you to grant calendar
access to the account that owns the target calendar, and prints the refresh
token to paste into Railway as TP_CALENDAR_GOOGLE_REFRESH_TOKEN.

Prerequisites (Google Cloud Console, one time):
  1. Create or pick a project.
  2. Enable the Google Calendar API.
  3. Configure the OAuth consent screen as "External", and add the target
     account as a Test user.
  4. Create an OAuth client ID of type "Desktop app" and note the client ID and
     client secret.

Usage:
    python scripts/google_oauth_setup.py --client-id ... --client-secret ...

Stdlib only, so it runs without installing anything.
"""

import argparse
import http.server
import json
import secrets
import socket
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/calendar"


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Catches Google's redirect and stashes the authorization code."""

    code: str | None = None
    error: str | None = None
    state: str = ""

    def do_GET(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

        if params.get("state", [""])[0] != _CallbackHandler.state:
            _CallbackHandler.error = "State mismatch — possible cross-site request. Try again."
        elif "error" in params:
            _CallbackHandler.error = params["error"][0]
        else:
            _CallbackHandler.code = params.get("code", [None])[0]

        ok = _CallbackHandler.code is not None
        body = (
            b"<h2>Authorized.</h2><p>You can close this tab and return to the terminal.</p>"
            if ok
            else b"<h2>Authorization failed.</h2><p>Check the terminal for details.</p>"
        )
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: object) -> None:
        """Silence the default request logging."""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--client-id", required=True, help="OAuth client ID (Desktop app).")
    parser.add_argument("--client-secret", required=True, help="OAuth client secret.")
    args = parser.parse_args()

    port = _free_port()
    redirect_uri = f"http://127.0.0.1:{port}/"
    _CallbackHandler.state = secrets.token_urlsafe(24)

    auth_url = f"{AUTH_URL}?" + urllib.parse.urlencode(
        {
            "client_id": args.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            # Force the consent screen so Google always returns a refresh token,
            # even if this client was authorized before.
            "prompt": "consent",
            "state": _CallbackHandler.state,
        }
    )

    server = http.server.HTTPServer(("127.0.0.1", port), _CallbackHandler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    print("Opening your browser to authorize calendar access.")
    print("Sign in as the account that owns the target calendar.\n")
    print(f"If the browser does not open, visit:\n{auth_url}\n")
    # Flush before blocking on the callback: when stdout is redirected to a file
    # or pipe it is block-buffered, which would hide the URL for the entire wait
    # — exactly when the user needs it.
    sys.stdout.flush()
    webbrowser.open(auth_url)

    thread.join(timeout=300)
    server.server_close()

    if _CallbackHandler.error:
        print(f"Authorization failed: {_CallbackHandler.error}")
        return 1
    if not _CallbackHandler.code:
        print(
            "Timed out waiting for authorization.\n\n"
            "If the browser showed 'Error 403: access_denied', the OAuth consent\n"
            "screen is in Testing mode and the account you signed in with is not\n"
            "an approved tester. Either:\n"
            "  - add that account under Audience > Test users, or\n"
            "  - publish the app (Audience > Publish app), which also stops\n"
            "    Google expiring the refresh token every 7 days.\n"
            "Then run this script again."
        )
        return 1

    request = urllib.request.Request(
        TOKEN_URL,
        data=urllib.parse.urlencode(
            {
                "code": _CallbackHandler.code,
                "client_id": args.client_id,
                "client_secret": args.client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            }
        ).encode(),
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        print(f"Token exchange failed: HTTP {exc.code}\n{exc.read().decode(errors='replace')}")
        return 1

    refresh_token = payload.get("refresh_token")
    if not refresh_token:
        print(
            "Google did not return a refresh token. Revoke this app's access at\n"
            "https://myaccount.google.com/permissions and run this script again."
        )
        return 1

    print("\nSuccess. Set these on your Railway service:\n")
    print(f"  TP_CALENDAR_GOOGLE_CLIENT_ID={args.client_id}")
    print(f"  TP_CALENDAR_GOOGLE_CLIENT_SECRET={args.client_secret}")
    print(f"  TP_CALENDAR_GOOGLE_REFRESH_TOKEN={refresh_token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
