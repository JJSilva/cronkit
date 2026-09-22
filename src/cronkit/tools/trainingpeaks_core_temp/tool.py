"""The CORE body-temperature tool.

Each run looks at recently completed TrainingPeaks workouts, and for any that
this tool has not already annotated, pulls the device's .FIT file, extracts the
CORE sensor's readings, and writes them into the workout's post-activity
comment.

Business rules, in the order they apply:

1. Look at completed workouts in a rolling window ending today.
2. Skip any whose comment already carries the report block — that is the whole
   "has this been processed" check, and it is why there is no database.
3. Skip any with no device upload.
4. Parse the upload. A file with no CORE fields gets no comment at all; the
   workout is simply left alone.
5. Post the block as a comment on the workout. The comment thread is the only
   writable comment surface TrainingPeaks offers — the v6 workout object accepts
   comment-looking fields on a PUT and silently discards them.
"""

import argparse
import logging
from datetime import date, timedelta
from typing import Any

from cronkit.core.tool import Tool, ToolResult
from cronkit.integrations.trainingpeaks import TrainingPeaksClient, Workout
from cronkit.tools.trainingpeaks_core_temp.config import CoreTempConfig
from cronkit.tools.trainingpeaks_core_temp.fit import parse_core_series
from cronkit.tools.trainingpeaks_core_temp.report import build_report, has_report

logger = logging.getLogger(__name__)


class TrainingPeaksCoreTempTool(Tool):
    """Post CORE body-temperature data into TrainingPeaks post-activity comments."""

    name = "trainingpeaks-core-temp"
    summary = "Post CORE body-temperature data into TrainingPeaks workout comments."
    env_prefix = "TP_CORE"
    default_interval_minutes = 30.0

    def __init__(self, config: CoreTempConfig):
        self.config = config
        # Workout uploads we have already looked at and found no CORE data in.
        #
        # Without this, every run would re-download the same multi-megabyte file
        # for every workout recorded without the sensor. It is a cache, not
        # state: losing it on restart costs one extra download per workout, and
        # correctness never depends on it. Keyed by upload, so a re-upload is
        # examined again.
        self._no_core_data: set[tuple[str, str]] = set()

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--days",
            dest="lookback_days",
            type=int,
            default=None,
            help="Override how many days back to look (default: TP_CORE_LOOKBACK_DAYS, or 1).",
        )
        parser.add_argument(
            "--summary-only",
            dest="summary_only",
            action="store_true",
            default=None,
            help="Write the summary without the per-interval table.",
        )

    @classmethod
    def from_env(cls, **overrides: Any) -> "TrainingPeaksCoreTempTool":
        return cls(CoreTempConfig.from_env(**overrides))

    def status(self) -> dict[str, Any]:
        return self.config.status()

    async def run(self, *, dry_run: bool = False) -> ToolResult:
        end_day = date.today()
        start_day = end_day - timedelta(days=max(self.config.lookback_days - 1, 0))

        annotated: list[str] = []
        already_done: list[str] = []
        no_core: list[str] = []
        errors: list[str] = []

        async with TrainingPeaksClient(self.config.tp_auth_cookie) as tp:
            workouts = await tp.workouts(start_day, end_day)
            candidates = [w for w in workouts if w.is_completed]

            for workout in candidates:
                label = f"{workout.day} {workout.title}"

                # The list response carries the comment thread, so an
                # already-annotated workout costs nothing further — which is the
                # steady state.
                if has_report(workout.comments):
                    already_done.append(label)
                    continue

                try:
                    outcome = await self._process(tp, workout, dry_run=dry_run)
                except Exception as exc:
                    logger.exception("Failed to process workout %s", workout.id)
                    errors.append(f"{label}: {exc}")
                    continue

                if outcome == "annotated":
                    annotated.append(label)
                elif outcome == "no_core_data":
                    no_core.append(label)
                elif outcome == "already_done":
                    already_done.append(label)

        verb = "would annotate" if dry_run else "annotated"
        summary = (
            f"{start_day}..{end_day}: {len(candidates)} completed workout(s); "
            f"{verb} {len(annotated)}, already done {len(already_done)}, "
            f"no CORE data {len(no_core)}"
            + (f", errors {len(errors)}" if errors else "")
        )

        return ToolResult(
            summary=summary,
            details={
                "window": {"start": start_day.isoformat(), "end": end_day.isoformat()},
                "completed_workouts": len(candidates),
                "annotated": annotated,
                "already_annotated": already_done,
                "no_core_data": no_core,
            },
            errors=errors,
            dry_run=dry_run,
        )

    async def _process(self, tp: TrainingPeaksClient, workout: Workout, *, dry_run: bool) -> str:
        """Handle one workout. Returns what happened, for the caller to tally."""
        files = await tp.device_files(workout.id)
        if not files:
            return "no_core_data"

        # Newest upload wins: a re-upload supersedes whatever came before it.
        upload = files[-1]
        if (workout.id, upload.file_id) in self._no_core_data:
            return "no_core_data"

        raw = await tp.download_file(workout.id, upload.file_id)
        series = parse_core_series(raw)
        if not series:
            self._no_core_data.add((workout.id, upload.file_id))
            return "no_core_data"

        report = build_report(series, self.config.report_options)
        if dry_run:
            return "annotated"

        # Re-read the thread rather than trusting the list snapshot, which may be
        # minutes old by the time we get here. A comment cannot be edited, only
        # added, so a duplicate would be permanent.
        if has_report(await tp.workout_comments(workout.id)):
            return "already_done"

        await tp.add_comment(workout.id, report)
        return "annotated"
