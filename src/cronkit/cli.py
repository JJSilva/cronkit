"""Command line entry point.

::

    cronkit list                                  what is registered, and how it is configured
    cronkit run trainingpeaks-calendar --dry-run  run one tool now
    cronkit run-all                               run every configured tool once
    cronkit serve                                 the scheduled daemon plus its HTTP API

``run`` grows a subcommand per registered tool, so a tool's own flags
(``--days``, say) are discoverable through ``cronkit run <tool> --help``.
"""

import argparse
import asyncio
import logging
import sys
from typing import Any

from cronkit.core import registry
from cronkit.core.config import DaemonConfig
from cronkit.core.errors import ConfigError, CronkitError
from cronkit.core.tool import Tool, ToolResult

logger = logging.getLogger(__name__)

# Flags argparse puts on the namespace that are ours, not a tool's.
_RESERVED_ARGS = {"verbose", "command", "tool", "dry_run", "func"}


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _tool_overrides(args: argparse.Namespace) -> dict[str, Any]:
    """Whatever the tool's own ``add_arguments`` put on the namespace."""
    return {key: value for key, value in vars(args).items() if key not in _RESERVED_ARGS}


def _print_result(name: str, result: ToolResult) -> None:
    print(f"{name}: {result.summary}")
    for label, items in result.details.items():
        if isinstance(items, list):
            for item in items:
                print(f"  {label:>10}: {item}")
    for error in result.errors:
        print(f"  {'error':>10}: {error}")


def _run_tool(tool_cls: type[Tool], *, dry_run: bool, overrides: dict[str, Any]) -> int:
    tool = tool_cls.from_env(**overrides)
    result = asyncio.run(tool.run(dry_run=dry_run))
    _print_result(tool_cls.name, result)
    # A non-zero exit lets a scheduled runner notice partial failures.
    return 0 if result.ok else 1


def _cmd_list(_: argparse.Namespace) -> int:
    tools = registry.all_tools()
    if not tools:
        print("No tools are registered.")
        return 0
    for tool_cls in tools:
        try:
            schedule = tool_cls.schedule_from_env()
            tool_cls.from_env()
            state = f"ready, {schedule.label}"
        except ConfigError as exc:
            state = f"unconfigured ({exc})"
        print(f"{tool_cls.name}\n  {tool_cls.summary}\n  {state}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    return _run_tool(registry.get(args.tool), dry_run=args.dry_run, overrides=_tool_overrides(args))


def _cmd_run_all(args: argparse.Namespace) -> int:
    """Run every configured tool once, reporting the worst outcome.

    A tool that is not configured is skipped rather than failing the batch —
    the same rule the daemon applies, so ``run-all`` is a faithful dry run of
    what ``serve`` will do.
    """
    exit_code = 0
    for tool_cls in registry.all_tools():
        try:
            exit_code = max(exit_code, _run_tool(tool_cls, dry_run=args.dry_run, overrides={}))
        except ConfigError as exc:
            print(f"{tool_cls.name}: skipped, unconfigured ({exc})")
        except Exception as exc:
            logger.debug("%s failed", tool_cls.name, exc_info=True)
            print(f"{tool_cls.name}: failed ({exc})", file=sys.stderr)
            exit_code = 1
    return exit_code


def _cmd_serve(_: argparse.Namespace) -> int:
    import uvicorn

    from cronkit.core.daemon import Daemon
    from cronkit.core.server import create_app

    config = DaemonConfig.from_env()
    uvicorn.run(create_app(Daemon.from_env(config)), host="0.0.0.0", port=config.port, log_level="info")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cronkit",
        description="A small cron daemon that runs a set of personal tools on a schedule.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List registered tools and their configuration state.")
    list_parser.set_defaults(func=_cmd_list)

    run_parser = subparsers.add_parser("run", help="Run one tool once and exit.")
    tool_parsers = run_parser.add_subparsers(dest="tool", required=True)
    for tool_cls in registry.all_tools():
        tool_parser = tool_parsers.add_parser(tool_cls.name, help=tool_cls.summary)
        tool_parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without making any changes.",
        )
        tool_cls.add_arguments(tool_parser)
        tool_parser.set_defaults(func=_cmd_run)

    run_all_parser = subparsers.add_parser("run-all", help="Run every configured tool once and exit.")
    run_all_parser.add_argument("--dry-run", action="store_true", help="Report what would change, changing nothing.")
    run_all_parser.set_defaults(func=_cmd_run_all)

    serve_parser = subparsers.add_parser("serve", help="Run the scheduled daemon with its HTTP API.")
    serve_parser.set_defaults(func=_cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(args.verbose)

    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except CronkitError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        logger.debug("fatal", exc_info=True)
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
