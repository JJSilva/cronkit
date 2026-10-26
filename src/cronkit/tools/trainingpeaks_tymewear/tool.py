"""The Tymewear breathing tool.

Each run looks at recently completed TrainingPeaks workouts, and for any that
this tool has not already annotated, pulls the device's .FIT file, extracts the
Tymewear strap's readings, and posts them as a comment on the workout.

The rules are the CORE tool's, in the same order:

1. Look at completed workouts in a rolling window ending today, in the
   configured timezone.
2. Skip any whose comment thread already carries the report block.
3. Skip any with no device upload.
4. Parse the upload. A file with no Tymewear fields gets no comment at all.
5. Post the block as a comment on the workout.

Tymewear's own analysis (ventilatory thresholds, breathing zones) lives on their
servers, not in the file. Reporting it would mean talking to the Tymewear API;
until then this reports the raw channels.
"""

import argparse
import logging
from datetime import timedelta
from typing import Any

from cronkit.core import clock
from cronkit.core.tool import Tool, ToolResult
from cronkit.integrations.trainingpeaks import TrainingPeaksClient, Workout
from cronkit.tools.trainingpeaks_tymewear.config import TymewearConfig
from cronkit.tools.trainingpeaks_tymewear.fit import parse_tyme_series
from cronkit.tools.trainingpeaks_tymewear.report import build_report, has_report

logger = logging.getLogger(__name__)


class TrainingPeaksTymewearTool(Tool):
    """Post Tymewear breathing data into TrainingPeaks workout comments."""

    name = "trainingpeaks-tymewear"
    summary = "Post Tymewear breathing data into TrainingPeaks workout comments."
    env_prefix = "TP_TYMEWEAR"
    default_interval_minutes = 30.0

    def __init__(self, config: TymewearConfig):
        self.config = config
        # Uploads already found to have no Tymewear data, so a workout recorded
        # without the strap is not re-downloaded every run. A cache, not state:
        # keyed by upload, so a re-upload is examined again.
        self._no_tyme_data: set[tuple[str, str]] = set()

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--days",
            dest="lookback_days",
            type=int,
            default=None,
            help="Override how many days back to look (default: TP_TYMEWEAR_LOOKBACK_DAYS, or 1).",
        )
        parser.add_argument(
            "--summary-only",
            dest="summary_only",
            action="store_true",
            default=None,
            help="Write the summary without the per-interval table.",
        )

    @classmethod
    def from_env(cls, **overrides: Any) -> "TrainingPeaksTymewearTool":
        return cls(TymewearConfig.from_env(**overrides))

    def status(self) -> dict[str, Any]:
        return self.config.status()

    async def run(self, *, dry_run: bool = False) -> ToolResult:
        end_day = clock.today(self.config.timezone)
        start_day = end_day - timedelta(days=max(self.config.lookback_days - 1, 0))

        annotated: list[str] = []
        already_done: list[str] = []
        no_tyme: list[str] = []
        errors: list[str] = []

        async with TrainingPeaksClient(self.config.tp_auth_cookie) as tp:
            workouts = await tp.workouts(start_day, end_day)
            candidates = [w for w in workouts if w.is_completed]

            for workout in candidates:
                label = f"{workout.day} {workout.title}"

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
                elif outcome == "no_tyme_data":
                    no_tyme.append(label)
                elif outcome == "already_done":
                    already_done.append(label)

        verb = "would annotate" if dry_run else "annotated"
        summary = (
            f"{start_day}..{end_day}: {len(candidates)} completed workout(s); "
            f"{verb} {len(annotated)}, already done {len(already_done)}, "
            f"no Tymewear data {len(no_tyme)}"
            + (f", errors {len(errors)}" if errors else "")
        )

        return ToolResult(
            summary=summary,
            details={
                "window": {"start": start_day.isoformat(), "end": end_day.isoformat()},
                "completed_workouts": len(candidates),
                "annotated": annotated,
                "already_annotated": already_done,
                "no_tymewear_data": no_tyme,
            },
            errors=errors,
            dry_run=dry_run,
        )

    async def _process(self, tp: TrainingPeaksClient, workout: Workout, *, dry_run: bool) -> str:
        """Handle one workout. Returns what happened, for the caller to tally."""
        files = await tp.device_files(workout.id)
        if not files:
            return "no_tyme_data"

        # Newest upload wins: a re-upload supersedes whatever came before it.
        upload = files[-1]
        if (workout.id, upload.file_id) in self._no_tyme_data:
            return "no_tyme_data"

        raw = await tp.download_file(workout.id, upload.file_id)
        series = parse_tyme_series(raw)
        if not series:
            self._no_tyme_data.add((workout.id, upload.file_id))
            return "no_tyme_data"

        report = build_report(series, self.config.report_options)
        if dry_run:
            return "annotated"

        # Re-read the thread rather than trusting the list snapshot: a comment
        # cannot be edited, only added, so a duplicate would be permanent.
        if has_report(await tp.workout_comments(workout.id)):
            return "already_done"

        await tp.add_comment(workout.id, report)
        return "annotated"
