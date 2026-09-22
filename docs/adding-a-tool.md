# Adding a tool

A tool is one job cronkit runs on a schedule. The daemon handles the timing, the
HTTP API, the status reporting, and the failure isolation; a tool supplies the
configuration and the work.

There are four steps. The whole thing is usually one module plus one line.

## 1. Make a package under `src/cronkit/tools/`

```
src/cronkit/tools/my_tool/
    __init__.py     re-export the tool class
    config.py       what it needs from the environment
    tool.py         the Tool subclass
    <clients>.py    whatever it talks to
```

Put the upstream API client in the tool's own package. When a *second* tool
needs the same client, move it to `src/cronkit/integrations/` — that is what
`integrations/trainingpeaks.py` is, shared by all three TrainingPeaks-reading tools.
Nothing in `cronkit/core/` should ever import from `cronkit/tools/`.

## 2. Write the config

Namespace every variable under the tool's prefix so two tools can never collide:

```python
from dataclasses import dataclass
from cronkit.core import env


@dataclass(frozen=True)
class MyToolConfig:
    api_key: str
    threshold: int = 10

    @classmethod
    def from_env(cls, **overrides):
        values = {
            "api_key": env.require("MY_TOOL_API_KEY"),
            "threshold": env.integer("MY_TOOL_THRESHOLD", default=10),
        }
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**values)

    def status(self):
        """Non-secret settings only — this is served over HTTP."""
        return {"threshold": self.threshold}
```

`env.require` raises `ConfigError`, which is the signal the daemon uses to mark
a tool *unconfigured*: it stays listed, it never gets scheduled, and the other
tools carry on. That is deliberate — one missing credential must not take down
a deployment running several jobs.

Every `env.*` accessor takes more than one name and uses the first one set, which
is how you rename a variable without breaking a live deployment:

```python
env.require("MY_TOOL_API_KEY", "OLD_API_KEY")
```

## 3. Write the tool

```python
from cronkit.core.tool import Tool, ToolResult


class MyTool(Tool):
    name = "my-tool"                      # the slug used everywhere
    summary = "One line for `cronkit list`."
    env_prefix = "MY_TOOL"                # -> MY_TOOL_INTERVAL_MINUTES, MY_TOOL_ENABLED
    default_interval_minutes = 30.0

    def __init__(self, config):
        self.config = config

    @classmethod
    def add_arguments(cls, parser):       # optional: flags for `cronkit run my-tool`
        parser.add_argument("--threshold", type=int, default=None)

    @classmethod
    def from_env(cls, **overrides):
        return cls(MyToolConfig.from_env(**overrides))

    async def run(self, *, dry_run: bool = False) -> ToolResult:
        ...
        return ToolResult(summary="did the thing", details={"changed": [...]})

    def status(self):
        return self.config.status()
```

Notes:

- `run` is async, and is called with `dry_run=True` from `--dry-run` and from
  `POST /tools/my-tool/run?dry_run=1`. Honour it: a dry run must change nothing.
- `ToolResult.details` is JSON-serialised straight into the API response. No
  secrets, and no raw upstream error bodies.
- Per-item failures go in `ToolResult.errors` — the run still counts as
  completed, and the CLI exits non-zero. Raise only when the whole run failed.
- Whatever `add_arguments` parses is passed to `from_env` as keyword overrides,
  so the flag's `dest` must match a config field. Default flags to `None`;
  `from_env` drops those.

## 4. Register it

In `src/cronkit/tools/__init__.py`:

```python
from cronkit.tools.my_tool import MyTool

register(MyTool)
```

That is the only wiring. The CLI grows `cronkit run my-tool`, the daemon starts
scheduling it, and `/tools`, `/tools/my-tool` and `/tools/my-tool/run` start
working.

## 5. Check it

```bash
cronkit list                      # is it registered? is it configured?
cronkit run my-tool --dry-run     # does it do the right nothing?
cronkit run my-tool
```

Add tests under `tests/tools/`. `tests/conftest.py` has a `fake_tool_cls`
fixture if you need to exercise the framework rather than the tool.

If your tool parses a binary format, generate the fixture rather than committing
a real file — this repo is public, and a device upload carries names, serial
numbers and GPS traces. `tests/fixtures/make_core_fit.py` is the pattern: a
committed generator plus its committed output.

## Schedules, for free

Every tool gets these, derived from `env_prefix`:

| Variable | Meaning |
| --- | --- |
| `MY_TOOL_INTERVAL_MINUTES` | Fixed cadence; sets both bounds. |
| `MY_TOOL_INTERVAL_MIN_MINUTES` | Lower bound of a randomised cadence. |
| `MY_TOOL_INTERVAL_MAX_MINUTES` | Upper bound. |
| `MY_TOOL_ENABLED` | `false` registers the tool but never schedules it — still runnable by hand and over the API. |

Each wait is drawn fresh from `[min, max]`, so repeated calls do not settle onto
an exact, fingerprintable rhythm. Equal bounds give a fixed interval.
