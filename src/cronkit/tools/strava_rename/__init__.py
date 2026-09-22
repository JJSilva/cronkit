"""Rename Strava activities after the TrainingPeaks workouts they were recorded for."""

from cronkit.tools.strava_rename.config import StravaRenameConfig
from cronkit.tools.strava_rename.tool import StravaRenameTool

__all__ = ["StravaRenameConfig", "StravaRenameTool"]
