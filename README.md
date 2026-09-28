# cronkit

A small cron daemon for personal automations. One deployment runs a set of
**tools** — independent jobs, each with its own credentials and its own cadence —
behind one health endpoint and one API.

It started life as a single TrainingPeaks → Google Calendar sync; that sync is
now the first tool.

| Tool | What it does |
| --- | --- |
| `trainingpeaks-calendar` | Syncs timed TrainingPeaks workouts into a Google Calendar |
| `trainingpeaks-core-temp` | Posts CORE body-temperature data into TrainingPeaks post-activity comments |
| `strava-rename` | Gives default-named Strava activities ("Morning Run") their TrainingPeaks workout title |

To add another, see **[docs/adding-a-tool.md](docs/adding-a-tool.md)**.

## Architecture

```
src/cronkit/
  cli.py                       cronkit list / run / run-all / serve
  core/
    tool.py                    the Tool contract + ToolResult
    registry.py                the catalogue of known tools
    schedule.py                per-tool cadence (a randomised interval)
    daemon.py                  one independent loop per tool
    server.py                  /health, /status, /tools, /tools/{name}/run
    config.py                  daemon-level settings
    env.py                     environment lookups, with alias chains
    errors.py
  integrations/
    trainingpeaks.py           API client shared by both TrainingPeaks tools
  tools/
    __init__.py                registers every tool
    trainingpeaks_calendar/    tool #1: config, sync rules, Google Calendar client
    trainingpeaks_core_temp/   tool #2: config, FIT parsing, comment formatting
    strava_rename/             tool #3: config, Strava client, pairing and default-name rules
```

Three properties the design leans on:

- **Tools are isolated.** Each gets its own asyncio task, its own lock, and its
  own schedule. A tool that fails — or that never configured, because a
  credential is missing — is reported as such and skipped; the others keep
  running. One expired cookie must not take down a deployment doing five things.
- **`core/` never imports from `tools/`.** The framework knows the `Tool`
  contract and nothing about any particular job. Upstream API clients live with
  their tool until a second tool needs one, at which point they move to
  `integrations/`.
- **Everything comes from the environment.** No state on disk, so the container
  can be rebuilt or moved freely.

## The dashboard

`/` serves a single page listing every tool with its schedule, its last run, and
the daemon's recent log output — plus **Run now** and **Dry run** buttons.

It is guarded by `CRONKIT_DASHBOARD_PASSWORD`. Sign in once and a signed session
cookie lasts 12 hours. There is no user list and no registration: one password,
set in Railway.

- The cookie is `HttpOnly`, `Secure` and `SameSite=Strict`, and is signed with an
  HMAC keyed on the password itself — so **changing the password immediately
  invalidates every outstanding session**.
- Run buttons are `POST` and carry a CSRF token checked against a second cookie.
  Scripted callers using the bearer token skip that check, because a header is
  something a cross-site form cannot set.
- With the password unset the page returns `503` and refuses everyone. It is
  never left open.

The page is a single self-contained document — no CDN, no build step, no
external requests — so the whole UI ships inside the container.

## Running


```bash
cronkit list                                   # what's registered, and is it configured?
cronkit run trainingpeaks-calendar --dry-run   # preview one tool
cronkit run trainingpeaks-calendar --days 60   # tools can add their own flags
cronkit run trainingpeaks-core-temp --dry-run
cronkit run strava-rename --dry-run --days 7   # preview a week of renames
cronkit run-all                                # every configured tool, once
cronkit serve                                  # the scheduled daemon (what Railway starts)
```

`cronkit run <tool> --help` lists that tool's flags.

## Configuration

Daemon-level settings are prefixed `CRONKIT_`. Each tool namespaces its own under
its own prefix, so two tools can never collide. Copy `.env.example` to `.env` for
local runs, or set the same keys as Railway service variables.

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `CRONKIT_API_TOKEN` | for scripts | — | Bearer token for scripted API callers |
| `CRONKIT_DASHBOARD_PASSWORD` | for the dashboard | — | Password for the sign-in form at `/` |
| `CRONKIT_TOOLS` | no | all | Comma-separated list of tools to load |
| `CRONKIT_RUN_ON_START` | no | `true` | Run each tool once at startup rather than waiting out the first interval |
| `CRONKIT_TIMEZONE` | recommended | host's | IANA zone that decides what "today" is for every tool, e.g. `America/Los_Angeles` |
| `PORT` | no | `8000` | Port the HTTP server binds; Railway sets this |

Every tool also gets, from its prefix:

| Variable | Default | Purpose |
| --- | --- | --- |
| `<PREFIX>_INTERVAL_MINUTES` | tool's own | Fixed cadence; sets both bounds below |
| `<PREFIX>_INTERVAL_MIN_MINUTES` | = above | Lower bound of the randomised cadence |
| `<PREFIX>_INTERVAL_MAX_MINUTES` | = above | Upper bound |
| `<PREFIX>_ENABLED` | `true` | `false` loads the tool but never schedules it |
| `<PREFIX>_TIMEZONE` | `CRONKIT_TIMEZONE` | Overrides the shared zone for one tool |

> **Set `CRONKIT_TIMEZONE`.** The container runs in UTC, where "today" rolls
> over at 5pm Pacific. Every tool windows its work by day, so without it an
> evening session drops out of "today" before it has been handled.

Each wait is drawn uniformly from `[min, max]`, so a tool does not poll on an
exact schedule. Equal bounds give a fixed interval. Invalid ranges (inverted or
non-positive) are rejected at load rather than silently clamped.

### Upgrading from ScheduleSync

The pre-cronkit variable names are all still accepted, so an existing deployment
keeps working without being touched. The namespaced name wins when both are set.

```
TP_AUTH_COOKIE          -> TP_CALENDAR_AUTH_COOKIE
GOOGLE_CLIENT_ID        -> TP_CALENDAR_GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET    -> TP_CALENDAR_GOOGLE_CLIENT_SECRET
GOOGLE_REFRESH_TOKEN    -> TP_CALENDAR_GOOGLE_REFRESH_TOKEN
CALENDAR_ID             -> TP_CALENDAR_GOOGLE_CALENDAR_ID
SYNC_DAYS               -> TP_CALENDAR_DAYS
SYNC_TIMEZONE           -> TP_CALENDAR_TIMEZONE
SYNC_PRUNE              -> TP_CALENDAR_PRUNE
SYNC_INTERVAL_*_MINUTES -> TP_CALENDAR_INTERVAL_*_MINUTES
SYNC_API_TOKEN          -> CRONKIT_API_TOKEN
```

`POST /sync` still works as a deprecated alias for
`POST /tools/trainingpeaks-calendar/run`, and `X-Sync-Token` is still accepted
alongside `X-Cronkit-Token`. The CLI is the one breaking change:
`schedulesync sync` is now `cronkit run trainingpeaks-calendar`, and
`schedulesync serve` is `cronkit serve`.

---

# Tool: `trainingpeaks-calendar`

Syncs **timed** TrainingPeaks workouts into a Google Calendar.

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

This tool reads **`startTimePlanned`**. Using `startTime` instead would sync only
workouts you had already finished, filling the calendar with the past.

This is also why the sibling `trainingpeaks-mcp` server cannot drive this sync
as-is: it maps `startTime` into its responses and drops `startTimePlanned`
entirely (`src/tp_mcp/client/models.py`, `WorkoutSummary`). This tool talks to the
TrainingPeaks API directly and reads the right field.

## How it stays idempotent without a database

Each event is created with a deterministic id derived from the TrainingPeaks
workout id (`tpplan<workoutId>`, which is valid base32hex). Re-running finds
the same id and updates in place instead of creating duplicates, so there is no
state to persist and the Railway container can be rebuilt or redeployed freely.

Events are tagged with a private extended property (`schedulesync=1` — frozen at
the project's old name, because it is stamped on every event already on the
calendar). Pruning only ever considers events carrying that tag, so nothing else
on your calendar can be touched.

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
`TP_CALENDAR_GOOGLE_*` values to set on Railway.

> If you see `Error 403: access_denied`, the consent screen is still in
> *Testing* and the account you used is not an approved tester. Publish the app,
> or add that account under *Audience → Test users*.

### 2. TrainingPeaks credential

`TP_CALENDAR_AUTH_COOKIE` is the `Production_tpAuth` cookie value from a
logged-in TrainingPeaks session — the same credential your `trainingpeaks-mcp`
deployment uses. Copy it from your browser's dev tools (Application → Cookies →
`trainingpeaks.com`).

This cookie **expires periodically** and is the one thing that needs occasional
manual attention. When it lapses, `/status` reports a `last_error` mentioning an
expired cookie; capture a fresh value and update the Railway variable.

### 3. Configure

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `TP_CALENDAR_AUTH_COOKIE` | yes | — | TrainingPeaks `Production_tpAuth` cookie |
| `TP_CALENDAR_GOOGLE_CLIENT_ID` | yes | — | OAuth client ID |
| `TP_CALENDAR_GOOGLE_CLIENT_SECRET` | yes | — | OAuth client secret |
| `TP_CALENDAR_GOOGLE_REFRESH_TOKEN` | yes | — | From the setup script |
| `TP_CALENDAR_GOOGLE_CALENDAR_ID` | no | `primary` | Target calendar, e.g. `you@gmail.com` |
| `TP_CALENDAR_DAYS` | no | `21` | How many days ahead to sync |
| `TP_CALENDAR_TIMEZONE` | no | `CRONKIT_TIMEZONE`, then the calendar's own | IANA zone for planned start times and for "today" |
| `TP_CALENDAR_PRUNE` | no | `true` | Remove events whose workout lost its time |
| `TP_CALENDAR_INTERVAL_*_MINUTES` | no | `60` | Cadence — see the table above |

Note that each run constructs fresh API clients, so both access tokens are
re-exchanged every sync. That is fine at intervals of a few minutes; if you want
to poll considerably more often, cache the clients across runs first.

---

# Tool: `trainingpeaks-core-temp`

Reads the CORE body-temperature data out of a completed workout's .FIT file and
writes it into that workout's **Post-Activity Comments** in TrainingPeaks.

Each run:

1. Lists completed workouts in a rolling window ending today.
2. Skips any whose comment already carries the report block.
3. Downloads the newest device upload and parses it.
4. Skips workouts whose file has no CORE data — those get no comment at all.
5. Posts the block as a comment on the workout.

The block looks like this:

```
----- CORE Body Temperature -----
Core  avg 37.7 / min 36.8 / max 38.6 °C
Skin  avg 32.7 / max 34.5 °C
HSI   avg 4.0 / max 8.0
Above 38.0 °C: 6m42s (34%)
1200 samples over 19m59s · quality avg 58

Time   Core  Skin   HSI
0:00   37.0  31.4   1.0
0:05   37.5  32.3   3.0
0:10   37.9  33.2   5.0
0:15   38.4  34.1   7.0
----- end CORE Body Temperature -----
```

## Where the data comes from

A CORE sensor does not write native FIT fields. It registers **developer data
fields** — the device declares them in `field_description` messages and attaches
them to each `record`:

| Field | Units | Meaning |
| --- | --- | --- |
| `core_temperature` | °C | Estimated core body temperature |
| `skin_temperature` | °C | Skin temperature at the sensor |
| `heat_strain_index` | a.u. | CORE's 0–10 heat strain index |
| `core_data_quality` | Q | Confidence; climbs as the sensor stabilises |

`CIQ_core_temperature` and `CIQ_skin_temperature` carry the same readings in
Fahrenheit and are ignored — the tool converts from the Celsius fields instead,
so one code path covers both unit settings. Output is Fahrenheit by default;
`TP_CORE_UNITS=C` shows the sensor's own unit.

Not every `record` carries a CORE reading (the sensor samples more slowly than
the watch records), so records without one are skipped rather than interpolated.

## Which field the comment goes in

The workout's **comment thread**, via
`POST /fitness/v2/athletes/{id}/workouts/{wid}/comments`. That is the only
writable comment surface TrainingPeaks offers.

The v6 workout object carries fields that look like they should work —
`newComment` is present on every workout, and `athleteComments` is what the
mobile app appears to use. Neither does: a `PUT` containing them returns `200`
and silently discards the value. This was established by round-tripping six
candidate field names against the live API and reading each one back; none
survived. Do not "fix" the tool to write one of them.

## Why there is no "processed" database

The report block is delimited by a header and a footer, and the header *is* the
processed marker. A workout whose thread contains it is skipped — and because the
workout list response already includes `workoutComments`, an already-annotated
workout costs no extra request. In the steady state a run is a single API call.

A comment cannot be edited or replaced, only added, so a duplicate would be
permanent. The thread is therefore re-read immediately before posting rather than
trusting the list snapshot, which may be minutes old by then.

One piece of genuinely in-memory state: workouts whose upload turned out to have
no CORE data are remembered for the life of the process, so a workout recorded
without the sensor is not re-downloaded every 20 minutes. It is a cache, not
state — losing it on restart costs one extra download, and correctness never
depends on it.

## Configure

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `TP_CORE_AUTH_COOKIE` | no | calendar tool's | TrainingPeaks cookie; falls back to `TP_CALENDAR_AUTH_COOKIE`, then `TP_AUTH_COOKIE` |
| `TP_CORE_LOOKBACK_DAYS` | no | `1` | Days back to consider; `1` is today only |
| `TP_CORE_TIMEZONE` | no | `CRONKIT_TIMEZONE` | IANA zone that decides "today" |
| `TP_CORE_UNITS` | no | `F` | `F` or `C` for the reported temperatures |
| `TP_CORE_THRESHOLD` | no | `100.4` | Core temp at or above which time is counted, in the unit above |
| `TP_CORE_INTERVAL_MINUTES` | no | `5` | Bucket size for the table |
| `TP_CORE_SUMMARY_ONLY` | no | `false` | Write the summary without the table |
| `TP_CORE_INTERVAL_*_MINUTES` | no | `30` | Cadence — see the table above |

A single `TP_AUTH_COOKIE` configures both TrainingPeaks tools.

> **Lookback and midnight.** The default window is today only, which matches how
> often the tool runs. If you ride late and the upload lands after midnight, the
> workout is dated yesterday and will be missed — set `TP_CORE_LOOKBACK_DAYS=2`
> if that happens to you.

## Regenerating the test fixture

`tests/fixtures/core_ride.fit.gz` is a **synthetic** FIT file, generated by
`tests/fixtures/make_core_fit.py`. A real device upload is not committed here
because this repo is public and a watch's FIT file carries the athlete's name,
device serial numbers and GPS coordinates.

```bash
python tests/fixtures/make_core_fit.py
```

---

# Tool: `strava-rename`

Gives a Strava activity the title of the TrainingPeaks workout it was recorded
for — but only while it still has a name a machine chose. "Morning Run" becomes
"Longish Run"; "Surf City 10k", which you typed yourself, is never touched.

Each run:

1. Lists completed TrainingPeaks workouts for today, in the configured timezone.
2. Lists Strava activities around the same window.
3. Pairs them by start time and sport.
4. Renames each paired activity whose Strava name is a default and whose
   TrainingPeaks title is not.

## The sport suffix

Swims, rides and runs get their sport appended to the TrainingPeaks title, so
the Strava feed says what the session was: `Masters` becomes `Masters Swim`,
`Tempo` on a bike becomes `Tempo Ride` (always "Ride", never "Bike"), `Fartlek`
becomes `Fartlek Run`. A title that already names its sport is left as it is —
`Long Ride`, `Bike Intervals`, `Open Water Swim`, `Running Drills` — so nothing
is ever said twice. Strength, walks and other sports get no suffix. A brick
takes the sport of each Strava activity it pairs with.

## How pairing works

Both services read the start time from the same device file, so a genuine pair
starts within a second or two — observed live, the worst case was one second.
`STRAVA_RENAME_TOLERANCE_MINUTES` (default `2`) absorbs that without ever
pairing two different sessions. Sport is also checked, so the run and ride of a
brick, recorded a couple of minutes apart, cannot be crossed. Where two workouts
compete for one activity, the closer start wins.

## What counts as a default name

- Strava's own: a time of day plus a sport — `Morning Run`, `Lunch Swim`,
  `Evening Weight Training`, and so on. Anything longer (`Morning Run with the
  club`) is treated as yours.
- A device's bare sport name: `Running`, `Road Cycling`, `Lap Swimming`, …
- Anything matching `STRAVA_RENAME_DEFAULT_NAME_PATTERNS` — regexes separated
  by `;;`, e.g. `^Zwift - ` to let Zwift's names be replaced too.

The same test applies to the TrainingPeaks side. An unplanned upload lands in
TrainingPeaks titled `Running`, and copying that over `Morning Run` would be no
improvement, so those pairs are skipped.

## Why there is no "processed" database

The default-name rule *is* the processed check. Once renamed, an activity has a
real title and never qualifies again; and anything you rename by hand in Strava
always wins, on the next run and every run after. Only the `name` field is sent
— description, gear, privacy and everything else are left as they are.

## Setup

### 1. A Strava API application

At <https://www.strava.com/settings/api>, once:

1. Create an application. Name, category and website can be anything.
2. Set **Authorization Callback Domain** to `localhost`.
3. Note the **Client ID** and **Client Secret**.

### 2. A refresh token

On your laptop:

```bash
python scripts/strava_oauth_setup.py --client-id YOUR_CLIENT_ID --client-secret YOUR_CLIENT_SECRET
```

Approve access with **both** activity boxes ticked — viewing private activities
(`activity:read_all`) and editing them (`activity:write`). The script checks the
granted scopes and prints the three `STRAVA_RENAME_*` values to set on Railway.

> Strava can issue a new refresh token when it refreshes an access token. The
> tool keeps the newest one in memory and logs a warning when that happens; if
> you see it, update `STRAVA_RENAME_REFRESH_TOKEN` before the next redeploy.
> When the token is rejected outright, `/status` shows an error naming the
> script to rerun.

### 3. Configure

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `STRAVA_RENAME_CLIENT_ID` | yes | — | Strava API application Client ID |
| `STRAVA_RENAME_CLIENT_SECRET` | yes | — | Strava API application Client Secret |
| `STRAVA_RENAME_REFRESH_TOKEN` | yes | — | From the setup script |
| `STRAVA_RENAME_TIMEZONE` | no | `CRONKIT_TIMEZONE` | IANA zone that decides "today" |
| `STRAVA_RENAME_TP_AUTH_COOKIE` | no | shared | TrainingPeaks cookie; falls back to the other tools' |
| `STRAVA_RENAME_LOOKBACK_DAYS` | no | `1` | Days back to consider; `1` is today only |
| `STRAVA_RENAME_TOLERANCE_MINUTES` | no | `2` | Maximum start-time gap for a pair |
| `STRAVA_RENAME_DEFAULT_NAME_PATTERNS` | no | — | Extra default-name regexes, `;;`-separated |
| `STRAVA_RENAME_INTERVAL_*_MINUTES` | no | `30` | Cadence — see the table above |

Strava allows 100 read requests per 15 minutes. A run costs one token refresh,
one list call and one call per rename, so any cadence down to a few minutes is
comfortably inside that.

---

## Deploying to Railway

```bash
railway init      # or: railway link, for an existing project
railway up
```

`railway.json` builds the Dockerfile and starts `cronkit serve`, with `/health`
as the healthcheck. Set the environment variables above in the service settings.

Endpoints:

| Endpoint | Auth | Purpose |
| --- | --- | --- |
| `GET /` | password | The dashboard, or the sign-in form |
| `GET /api/logs` | either | The daemon's in-memory log tail |
| `GET /health` | none | Liveness only — returns `{"status":"ok"}` and nothing else |
| `GET /status` | token | Every tool: its schedule, config, and last run |
| `GET /tools` | token | The loaded tools and their cadences |
| `GET /tools/{name}` | token | One tool's state |
| `POST /tools/{name}/run` | token | Run one tool now (`?dry_run=true` to preview) |
| `POST /sync` | token | Deprecated alias for `POST /tools/trainingpeaks-calendar/run` |

Authenticate with either header:

```bash
curl -H "Authorization: Bearer $CRONKIT_API_TOKEN" https://your-app.up.railway.app/status
curl -X POST -H "X-Cronkit-Token: $CRONKIT_API_TOKEN" \
  https://your-app.up.railway.app/tools/trainingpeaks-calendar/run
```

`/health` stays `200` even after a failed run, so an expired TrainingPeaks
cookie does not make Railway restart-loop a container that cannot fix itself.
Check `/status` to see whether runs are actually succeeding.

## Security

This repo is public; the deployment is not. Things worth knowing:

- **Every credential comes from the environment.** Nothing is read from or
  written to disk at runtime, and `.env` is gitignored. Never commit real values
  — put them in Railway's service variables.
- **A Railway service is reachable from the public internet.** Only `/health` is
  anonymous, and it returns nothing but liveness — no tool names, no calendar
  address, no workout titles, no error text. Everything else requires
  `CRONKIT_API_TOKEN`, compared in constant time, and **fails closed**: if the
  token is unset those endpoints refuse every request rather than falling open.
  Authorization is checked before the tool is looked up, so an anonymous caller
  cannot even learn which tools exist.
- **Upstream error text is never returned over HTTP.** A failed run responds with
  a fixed message and logs the detail for the operator, so API error bodies
  cannot leak to a caller. A tool's `status()` is served to authorized callers,
  so it must return settings only — never a credential.
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

Tests mirror the source layout: `tests/core/` for the framework, `tests/tools/`
for individual tools. `tests/conftest.py` provides a `fake_tool_cls` fixture for
exercising the framework without touching a real upstream API.
