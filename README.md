# ScheduleSync

Syncs **timed** TrainingPeaks workouts into a Google Calendar, running unattended
on Railway.

- Only workouts with a **planned start time** set in TrainingPeaks are synced.
  Untimed workouts are ignored entirely.
- The workout **title** becomes the event title.
- The workout **description** becomes the event description.
- The event lasts the workout's planned duration (one hour when TrainingPeaks
  has none).
- If you later clear a workout's time or delete it, the matching event is
  removed again.

## The `startTimePlanned` vs `startTime` distinction

TrainingPeaks exposes two different time fields, and only one of them means
"this session is scheduled for 6:30am":

| Field | Meaning | When it is set |
| --- | --- | --- |
| `startTimePlanned` | The time you set on a planned workout in the TrainingPeaks UI | Only when you set one |
| `startTime` | The actual start recorded by your watch/head unit | Only after you upload a completed workout |

ScheduleSync reads **`startTimePlanned`**. Using `startTime` instead would sync
only workouts you had already finished, filling the calendar with the past.

This is also why the sibling `trainingpeaks-mcp` server cannot drive this sync
as-is: it maps `startTime` into its responses and drops `startTimePlanned`
entirely (`src/tp_mcp/client/models.py`, `WorkoutSummary`). ScheduleSync talks to
the TrainingPeaks API directly and reads the right field.

## How it stays idempotent without a database

Each event is created with a deterministic id derived from the TrainingPeaks
workout id (`tpplan<workoutId>`, which is valid base32hex). Re-running finds
the same id and updates in place instead of creating duplicates, so there is no
state to persist and the Railway container can be rebuilt or redeployed freely.

Events are tagged with a private extended property (`schedulesync=1`). Pruning
only ever considers events carrying that tag, so nothing else on your calendar
can be touched.

## Setup

### 1. Google Calendar credentials

In the [Google Cloud Console](https://console.cloud.google.com/), once:

1. Create or pick a project.
2. Enable the **Google Calendar API**.
3. Configure the **OAuth consent screen** as *External*, then **publish the
   app** (*Audience → Publish app*). Leaving it in *Testing* means only
   accounts listed under *Test users* can authorize — signing in with any
   other gives `Error 403: access_denied` — **and Google expires the refresh
   token every 7 days**, which breaks the sync weekly.

   Publishing without Google verification is fine for a personal app: you will
   see a "Google hasn't verified this app" warning once, and continue via
   *Advanced → Go to ... (unsafe)*.
4. Create an **OAuth client ID** of type **Desktop app**. Note the client ID and
   client secret.

Then, on your laptop, exchange that for a long-lived refresh token:

```bash
python scripts/google_oauth_setup.py \
  --client-id YOUR_CLIENT_ID \
  --client-secret YOUR_CLIENT_SECRET
```

Sign in as the account that owns the calendar. The script prints the three
`GOOGLE_*` values to set on Railway.

> If you see `Error 403: access_denied`, the consent screen is still in
> *Testing* and the account you used is not an approved tester. Publish the app,
> or add that account under *Audience → Test users*.

### 2. TrainingPeaks credential

`TP_AUTH_COOKIE` is the `Production_tpAuth` cookie value from a logged-in
TrainingPeaks session — the same credential your `trainingpeaks-mcp` deployment
uses. Copy it from your browser's dev tools (Application → Cookies →
`trainingpeaks.com`).

This cookie **expires periodically** and is the one thing that needs occasional
manual attention. When it lapses, `/health` reports a `last_error` mentioning an
expired cookie; capture a fresh value and update the Railway variable.

### 3. Configure

Copy `.env.example` to `.env` for local runs, or set the same keys as Railway
service variables:

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `TP_AUTH_COOKIE` | yes | — | TrainingPeaks `Production_tpAuth` cookie |
| `GOOGLE_CLIENT_ID` | yes | — | OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | yes | — | OAuth client secret |
| `GOOGLE_REFRESH_TOKEN` | yes | — | From the setup script |
| `CALENDAR_ID` | no | `primary` | Target calendar, e.g. `you@gmail.com` |
| `SYNC_DAYS` | no | `21` | How many days ahead to sync |
| `SYNC_INTERVAL_MINUTES` | no | `60` | Fixed interval, and the default for both bounds below |
| `SYNC_INTERVAL_MIN_MINUTES` | no | = above | Lower bound of the randomised interval |
| `SYNC_INTERVAL_MAX_MINUTES` | no | = above | Upper bound of the randomised interval |
| `SYNC_TIMEZONE` | no | calendar's own | IANA zone for planned start times |
| `SYNC_PRUNE` | no | `true` | Remove events whose workout lost its time |
| `SYNC_API_TOKEN` | for `serve` | — | Shared secret guarding `/status` and `/sync` |

## Running

Check what it would do, without writing anything:

```bash
schedulesync sync --dry-run
schedulesync sync --dry-run --days 60
```

Run a single real sync:

```bash
schedulesync sync
```

Run the scheduled service (what Railway starts):

```bash
schedulesync serve
```

## Deploying to Railway

```bash
railway init      # or: railway link, for an existing project
railway up
```

`railway.json` builds the Dockerfile and starts `schedulesync serve`, with
`/health` as the healthcheck. Set the environment variables above in the service
settings.

Endpoints:

| Endpoint | Auth | Purpose |
| --- | --- | --- |
| `GET /health` | none | Liveness only — returns `{"status":"ok"}` and nothing else |
| `GET /status` | token | Last run's full report, the cadence, and the next scheduled run |
| `POST /sync` | token | Sync now (`?dry_run=true` to preview) |

Authenticate with either header:

```bash
curl -H "Authorization: Bearer $SYNC_API_TOKEN" https://your-app.up.railway.app/status
curl -X POST -H "X-Sync-Token: $SYNC_API_TOKEN" https://your-app.up.railway.app/sync
```

`/health` stays `200` even after a failed sync, so an expired TrainingPeaks
cookie does not make Railway restart-loop a container that cannot fix itself.
Check `/status` to see whether runs are actually succeeding.

### Sync cadence

Each wait is drawn uniformly from `[SYNC_INTERVAL_MIN_MINUTES,
SYNC_INTERVAL_MAX_MINUTES]`, so the service does not poll on an exact schedule.
Setting the two equal — or setting only `SYNC_INTERVAL_MINUTES` — gives a fixed
interval instead. Invalid ranges (inverted or non-positive) are rejected at
startup rather than silently clamped.

Note that each run constructs fresh API clients, so both access tokens are
re-exchanged every sync. That is fine at intervals of a few minutes; if you want
to poll considerably more often, cache the clients across runs first.

## Security

This repo is public; the deployment is not. Things worth knowing:

- **Every credential comes from the environment.** Nothing is read from or
  written to disk at runtime, and `.env` is gitignored. Never commit real values
  — put them in Railway's service variables.
- **A Railway service is reachable from the public internet.** Only `/health` is
  anonymous, and it returns nothing but liveness — no calendar address, no
  workout titles, no error text. `/status` and `/sync` require `SYNC_API_TOKEN`,
  compared in constant time, and **fail closed**: if the token is unset they
  refuse every request rather than falling open.
- **Upstream error text is never returned over HTTP.** A failed `/sync` responds
  with a fixed message and logs the detail for the operator, so API error bodies
  cannot leak to a caller.
- **Pruning is scoped by a private property.** Deletion only ever considers
  events tagged `schedulesync=1`, so a bug cannot reach the rest of your
  calendar.
- **The OAuth scope is `calendar`**, which grants access to your calendars only —
  not Gmail, Drive, or contacts.
- `scripts/google_oauth_setup.py` prints a refresh token to your terminal. Treat
  it like a password: it grants calendar access until revoked, at
  <https://myaccount.google.com/permissions>.

Found a security problem? Open an issue.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check .
```
