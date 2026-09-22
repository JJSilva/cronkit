"""Errors shared by the daemon and every tool."""


class CronkitError(Exception):
    """Base class for errors this daemon raises on purpose."""


class ConfigError(CronkitError):
    """Required configuration is missing or malformed.

    A tool that raises this at load time is reported as *unconfigured* and
    skipped rather than taking the whole daemon down — see
    :mod:`cronkit.core.daemon`.
    """


class ToolNotFoundError(CronkitError):
    """No tool is registered under the requested name."""
