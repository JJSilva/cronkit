"""The sync itself: timed TrainingPeaks workouts -> Google Calendar events.

Business rules, in the order they apply:

1. Look at a rolling window starting today.
2. Keep only workouts that have a planned start time (``startTimePlanned``).
   Untimed workouts are skipped entirely — they never reach the calendar.
3. The workout title becomes the event title; the workout description becomes
   the event description.
4. The event runs for the planned duration, defaulting to an hour when
   TrainingPeaks has none.
5. Events previously created from a workout that is now untimed or deleted are
   removed again.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from schedulesync.config import Config, ConfigError
from schedulesync.gcal import CalendarClient, SyncedEvent, event_id_for
from schedulesync.trainingpeaks import TrainingPeaksClient, Workout

logger = logging.getLogger(__name__)

# Used when TrainingPeaks has no planned duration for a timed workout.
DEFAULT_DURATION = timedelta(hours=1)


@dataclass
class SyncResult:
    """What a single sync run did."""

    window_start: date
    window_end: date
    timezone: str
    total_workouts: int = 0
    timed_workouts: int = 0
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def skipped_untimed(self) -> int:
        """Workouts in the window that had no planned start time."""
        return self.total_workouts - self.timed_workouts

    def summary_line(self) -> str:
        verb = "would " if self.dry_run else ""
        return (
            f"{self.window_start}..{self.window_end} ({self.timezone}): "
            f"{self.timed_workouts}/{self.total_workouts} workouts timed; "
            f"{verb}created {len(self.created)}, "
            f"{verb}updated {len(self.updated)}, "
            f"unchanged {len(self.unchanged)}, "
            f"{verb}deleted {len(self.deleted)}"
            + (f", errors {len(self.errors)}" if self.errors else "")
        )


def event_window(workout: Workout) -> tuple[datetime, datetime]:
    """Return the naive local start and end for a timed workout."""
    assert workout.planned_start is not None, "caller must filter to timed workouts"
    start = workout.planned_start
    duration = timedelta(hours=workout.planned_hours) if workout.planned_hours else DEFAULT_DURATION
    return start, start + duration


def _local_wall_clock(rfc3339: str | None, tz: ZoneInfo) -> datetime | None:
    """Convert an event timestamp into naive wall-clock time in ``tz``."""
    if not rfc3339:
        return None
    try:
        parsed = datetime.fromisoformat(rfc3339)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(tz).replace(tzinfo=None)


def needs_update(existing: SyncedEvent, workout: Workout, tz: ZoneInfo) -> bool:
    """Whether the calendar event differs from what the workout now says.

    Skipping unchanged events keeps runs cheap and, more importantly, avoids
    re-notifying the calendar for workouts that did not actually move.
    """
    start, end = event_window(workout)
    if existing.summary != workout.title:
        return True
    if (existing.description or "") != (workout.description or ""):
        return True
    if _local_wall_clock(existing.start, tz) != start:
        return True
    return _local_wall_clock(existing.end, tz) != end


def _resolve_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"Unknown timezone {name!r}: {exc}") from exc


async def run_sync(config: Config, *, dry_run: bool = False, today: date | None = None) -> SyncResult:
    """Run one full sync pass and return what happened."""
    start_day = today or date.today()
    end_day = start_day + timedelta(days=config.sync_days)

    async with (
        TrainingPeaksClient(config.tp_auth_cookie) as tp,
        CalendarClient(
            config.google_client_id,
            config.google_client_secret,
            config.google_refresh_token,
            config.calendar_id,
        ) as gcal,
    ):
        tz_name = config.timezone or await gcal.calendar_timezone()
        tz = _resolve_timezone(tz_name)
        result = SyncResult(window_start=start_day, window_end=end_day, timezone=tz_name, dry_run=dry_run)

        workouts = await tp.workouts(start_day, end_day)
        result.total_workouts = len(workouts)

        timed = [w for w in workouts if w.has_planned_time]
        result.timed_workouts = len(timed)

        existing_events = await gcal.list_synced_events(start_day, end_day, tz)
        by_event_id = {e.event_id: e for e in existing_events}

        wanted_event_ids: set[str] = set()

        for workout in timed:
            event_id = event_id_for(workout.id)
            wanted_event_ids.add(event_id)
            label = f"{workout.day} {workout.title}"

            existing = by_event_id.get(event_id)
            if existing is not None and not needs_update(existing, workout, tz):
                result.unchanged.append(label)
                continue

            start, end = event_window(workout)
            body = gcal.build_event_body(
                workout_id=workout.id,
                summary=workout.title,
                description=workout.description,
                start=start,
                end=end,
                timezone=tz_name,
            )

            if dry_run:
                (result.updated if existing is not None else result.created).append(label)
                continue

            try:
                action = await gcal.upsert_event(event_id, body)
            except Exception as exc:
                logger.exception("Failed to sync workout %s", workout.id)
                result.errors.append(f"{label}: {exc}")
                continue

            (result.created if action == "created" else result.updated).append(label)

        if config.prune:
            # Anything we previously created in this window that no longer
            # corresponds to a timed workout — the time was cleared, or the
            # workout was deleted or moved out of the window.
            for event in existing_events:
                if event.event_id in wanted_event_ids:
                    continue
                label = f"{event.start or '?'} {event.summary}"
                if dry_run:
                    result.deleted.append(label)
                    continue
                try:
                    await gcal.delete_event(event.event_id)
                except Exception as exc:
                    logger.exception("Failed to delete event %s", event.event_id)
                    result.errors.append(f"delete {label}: {exc}")
                    continue
                result.deleted.append(label)

        return result
