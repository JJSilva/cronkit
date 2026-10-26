"""The tools this daemon knows how to run.

Importing this package registers every tool. Adding one means writing the module
and adding a single ``register(...)`` line below — see ``docs/adding-a-tool.md``.
"""

from cronkit.core.registry import register
from cronkit.tools.strava_rename import StravaRenameTool
from cronkit.tools.trainingpeaks_calendar import TrainingPeaksCalendarTool
from cronkit.tools.trainingpeaks_core_temp import TrainingPeaksCoreTempTool
from cronkit.tools.trainingpeaks_tymewear import TrainingPeaksTymewearTool

register(TrainingPeaksCalendarTool)
register(TrainingPeaksCoreTempTool)
register(TrainingPeaksTymewearTool)
register(StravaRenameTool)

__all__ = ["StravaRenameTool", "TrainingPeaksCalendarTool", "TrainingPeaksCoreTempTool", "TrainingPeaksTymewearTool"]
