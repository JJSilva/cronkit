#!/usr/bin/env python3
"""One-time helper: turn a Strava API application into a long-lived refresh token.

Run this once on your laptop. It opens a browser, asks you to let your Strava
API application read and edit your activities, and prints the three values to
paste into Railway for the strava-rename tool.

Prerequisites (https://www.strava.com/settings/api, one time):
  1. Create an API application. Name, website and description can be anything.
  2. Set "Authorization Callback Domain" to: localhost
  3. Note the Client ID and Client Secret.

Usage:
    python scripts/strava_oauth_setup.py --client-id ... --client-secret ...

Stdlib only, so it runs without installing anything.
"""

import argparse
import http.server
import json
import secrets
import socket
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

AUTH_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
# read_all so private activities are listed; write so they can be renamed.
SCOPE = "read,activity:read_all,activity:write"
REQUIRED_SCOPES = {"activity:read_all", "activity:write"}


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Catches Strava's redirect and stashes the authorization code."""

    code: str | None = None
    error: str | None = None
    state: str = ""
    scope: str = ""

    def do_GET(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

        if params.get("state", [""])[0] != _CallbackHandler.state:
            _CallbackHandler.error = "State mismatch — possible cross-site request. Try again."
        elif "error" in params:
            _CallbackHandler.error = params["error"][0]
        else:
            _CallbackHandler.code = params.get("code", [None])[0]
            _CallbackHandler.scope = params.get("scope", [""])[0]

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
    parser.add_argument("--client-id", required=True, help="Strava API application Client ID.")
    parser.add_argument("--client-secret", required=True, help="Strava API application Client Secret.")
    args = parser.parse_args()

    port = _free_port()
    # Strava checks only the host against the app's callback domain, so any
    # port on localhost is accepted.
    redirect_uri = f"http://localhost:{port}/"
    _CallbackHandler.state = secrets.token_urlsafe(24)

    auth_url = f"{AUTH_URL}?" + urllib.parse.urlencode(
        {
            "client_id": args.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            # Always show the consent screen, so a scope added since the last
            # authorization is actually granted.
            "approval_prompt": "force",
            "state": _CallbackHandler.state,
        }
    )

    server = http.server.HTTPServer(("127.0.0.1", port), _CallbackHandler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    print("Opening your browser to authorize Strava access.")
    print("Leave BOTH activity checkboxes ticked (view private activities, and upload/edit).\n")
    print(f"If the browser does not open, visit:\n{auth_url}\n")
    # Flush before blocking on the callback, so a redirected stdout still shows the URL.
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
            "If Strava showed 'redirect_uri invalid', set the application's\n"
            "Authorization Callback Domain to 'localhost' and run this again."
        )
        return 1

    # Strava reports what was actually granted on the redirect; a user can
    # untick a checkbox on the consent screen.
    granted = set(_CallbackHandler.scope.split(","))
    missing = REQUIRED_SCOPES - granted
    if missing:
        print(f"Missing permission(s): {', '.join(sorted(missing))}. Run again and leave every box ticked.")
        return 1

    request = urllib.request.Request(
        TOKEN_URL,
        data=urllib.parse.urlencode(
            {
                "client_id": args.client_id,
                "client_secret": args.client_secret,
                "code": _CallbackHandler.code,
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
        print("Strava did not return a refresh token. Run this script again.")
        return 1

    athlete = payload.get("athlete") or {}
    who = " ".join(filter(None, (athlete.get("firstname"), athlete.get("lastname"))))
    print(f"\nSuccess{f' for {who}' if who else ''}. Set these on your Railway service:\n")
    print(f"  STRAVA_RENAME_CLIENT_ID={args.client_id}")
    print(f"  STRAVA_RENAME_CLIENT_SECRET={args.client_secret}")
    print(f"  STRAVA_RENAME_REFRESH_TOKEN={refresh_token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
