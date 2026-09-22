"""The tools this daemon knows how to run.

Importing this package registers every tool. Adding one means writing the module
and adding a single ``register(...)`` line below — see ``docs/adding-a-tool.md``.
"""

from cronkit.core.registry import register
from cronkit.tools.trainingpeaks_calendar import TrainingPeaksCalendarTool

register(TrainingPeaksCalendarTool)

__all__ = ["TrainingPeaksCalendarTool"]
