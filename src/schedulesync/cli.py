"""Command line entry point."""

import argparse
import asyncio
import logging
import os
import sys
from dataclasses import replace

from schedulesync.config import Config, ConfigError
from schedulesync.sync import SyncResult, run_sync


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _print_report(result: SyncResult) -> None:
    print(result.summary_line())
    for label, items in (
        ("created", result.created),
        ("updated", result.updated),
        ("deleted", result.deleted),
        ("errors", result.errors),
    ):
        for item in items:
            print(f"  {label:>8}: {item}")


def _cmd_sync(args: argparse.Namespace) -> int:
    config = Config.from_env()
    if args.days is not None:
        config = replace(config, sync_days=args.days)
    result = asyncio.run(run_sync(config, dry_run=args.dry_run))
    _print_report(result)
    # A non-zero exit lets a scheduled runner notice partial failures.
    return 1 if result.errors else 0


def _cmd_serve(_: argparse.Namespace) -> int:
    import uvicorn

    from schedulesync.server import create_app

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(create_app(), host="0.0.0.0", port=port, log_level="info")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="schedulesync",
        description="Sync timed TrainingPeaks workouts into Google Calendar.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync", help="Run one sync pass and exit.")
    sync_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without touching the calendar.",
    )
    sync_parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Override how many days ahead to sync (default: SYNC_DAYS, or 21).",
    )
    sync_parser.set_defaults(func=_cmd_sync)

    serve_parser = subparsers.add_parser("serve", help="Run the scheduled sync service with a health endpoint.")
    serve_parser.set_defaults(func=_cmd_serve)

    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        logging.getLogger("schedulesync").debug("fatal", exc_info=True)
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
