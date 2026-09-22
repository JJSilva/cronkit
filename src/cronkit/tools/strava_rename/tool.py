"""The Strava rename tool.

Each run pairs today's completed TrainingPeaks workouts with the Strava
activities recorded from the same device file, and gives any Strava activity
still wearing a machine-chosen name the TrainingPeaks workout's title.

Business rules, in the order they apply:

1. Look at completed workouts in a rolling window ending today, in the
   configured timezone.
2. Pair each with the Strava activity whose start time is within a couple of
   minutes and whose sport agrees.
3. Skip a pair whose TrainingPeaks title is itself generic ("Running") — that is
   an unplanned upload, and its title is no better than Strava's.
4. Skip a pair whose Strava name is already the title.
5. Skip a pair whose Strava name a person chose. Only default names ("Morning
   Run", "Road Cycling") are replaced, so a rename done by hand in Strava always
   wins — and an activity this tool renamed is never touched again, which is the
   whole "has this been processed" check.
6. Rename. Only the name changes; nothing else on the activity is sent.
"""

import argparse
import logging
from datetime import date, datetime, time, timedelta
from typing import Any

from cronkit.core import clock
from cronkit.core.tool import Tool, ToolResult
from cronkit.integrations.trainingpeaks import TrainingPeaksClient, Workout
from cronkit.tools.strava_rename.config import StravaRenameConfig
from cronkit.tools.strava_rename.matching import is_default_name, pair_up
from cronkit.tools.strava_rename.strava import StravaClient

logger = logging.getLogger(__name__)

OUTCOMES = ("renamed", "already_named", "custom_name_kept", "generic_tp_title", "no_strava_match")


class StravaRenameTool(Tool):
    """Rename Strava activities after their TrainingPeaks workouts."""

    name = "strava-rename"
    summary = "Give default-named Strava activities their TrainingPeaks workout title."
    env_prefix = "STRAVA_RENAME"
    default_interval_minutes = 30.0

    def __init__(self, config: StravaRenameConfig):
        self.config = config
        # The newest Strava refresh token, carried across runs in case Strava
        # rotates it. Losing it on restart falls back to the configured one.
        self._refresh_token = config.strava_refresh_token

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--days",
            dest="lookback_days",
            type=int,
            default=None,
            help="Override how many days back to look (default: STRAVA_RENAME_LOOKBACK_DAYS, or 1).",
        )

    @classmethod
    def from_env(cls, **overrides: Any) -> "StravaRenameTool":
        return cls(StravaRenameConfig.from_env(**overrides))

    def status(self) -> dict[str, Any]:
        return self.config.status()

    def _today(self) -> date:
        return clock.today(self.config.timezone)

    async def run(self, *, dry_run: bool = False) -> ToolResult:
        end_day = self._today()
        start_day = end_day - timedelta(days=max(self.config.lookback_days - 1, 0))
        outcomes: dict[str, list[str]] = {key: [] for key in OUTCOMES}
        errors: list[str] = []

        async with TrainingPeaksClient(self.config.tp_auth_cookie) as tp:
            workouts = [w for w in await tp.workouts(start_day, end_day) if w.is_completed and w.actual_start]

        if workouts:
            await self._rename(workouts, start_day, end_day, outcomes, errors, dry_run=dry_run)

        verb = "would rename" if dry_run else "renamed"
        summary = (
            f"{start_day}..{end_day}: {len(workouts)} completed workout(s); {verb} {len(outcomes['renamed'])}, "
            f"already named {len(outcomes['already_named'])}, custom name kept {len(outcomes['custom_name_kept'])}, "
            f"generic TP title {len(outcomes['generic_tp_title'])}, no Strava match {len(outcomes['no_strava_match'])}"
            + (f", errors {len(errors)}" if errors else "")
        )
        return ToolResult(
            summary=summary,
            details={
                "window": {"start": start_day.isoformat(), "end": end_day.isoformat()},
                "completed_workouts": len(workouts),
                **outcomes,
            },
            errors=errors,
            dry_run=dry_run,
        )

    async def _rename(
        self,
        workouts: list[Workout],
        start_day: date,
        end_day: date,
        outcomes: dict[str, list[str]],
        errors: list[str],
        *,
        dry_run: bool,
    ) -> None:
        # Strava filters on UTC epochs while start times are local wall clock, so
        # the query is widened by a day on each side and pairing narrows it.
        zone = self.config.zone
        after = datetime.combine(start_day - timedelta(days=1), time.min, zone).astimezone()
        before = datetime.combine(end_day + timedelta(days=2), time.min, zone).astimezone()
        patterns = self.config.extra_default_patterns

        async with StravaClient(
            self.config.strava_client_id, self.config.strava_client_secret, self._refresh_token
        ) as strava:
            try:
                activities = await strava.activities(after, before)
                pairs = pair_up(workouts, activities, timedelta(minutes=self.config.tolerance_minutes))

                paired = {p.workout.id for p in pairs}
                outcomes["no_strava_match"].extend(f"{w.day} {w.title}" for w in workouts if w.id not in paired)

                for pair in pairs:
                    day, title, current = pair.workout.day, pair.workout.title, pair.activity.name
                    label = f"{day} {current!r} -> {title!r}"

                    if is_default_name(title, patterns):
                        outcomes["generic_tp_title"].append(f"{day} {title}")
                    elif current.strip() == title:
                        outcomes["already_named"].append(f"{day} {title}")
                    elif not is_default_name(current, patterns):
                        outcomes["custom_name_kept"].append(f"{day} {current}")
                    elif dry_run:
                        outcomes["renamed"].append(label)
                    else:
                        try:
                            await strava.rename(pair.activity.id, title)
                        except Exception as exc:
                            logger.exception("Failed to rename Strava activity %s", pair.activity.id)
                            errors.append(f"{label}: {exc}")
                        else:
                            outcomes["renamed"].append(label)
            finally:
                self._refresh_token = strava.refresh_token
