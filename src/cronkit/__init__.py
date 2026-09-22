"""cronkit — a small cron daemon that runs a set of personal tools on a schedule.

Each job is a :class:`cronkit.core.tool.Tool` with its own configuration and its
own cadence; :mod:`cronkit.core.daemon` runs them and :mod:`cronkit.core.server`
exposes them over HTTP.
"""

__version__ = "0.2.0"
