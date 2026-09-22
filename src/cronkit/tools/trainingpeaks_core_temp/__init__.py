"""Post CORE body-temperature data into TrainingPeaks post-activity comments."""

from cronkit.tools.trainingpeaks_core_temp.config import CoreTempConfig
from cronkit.tools.trainingpeaks_core_temp.tool import TrainingPeaksCoreTempTool

__all__ = ["CoreTempConfig", "TrainingPeaksCoreTempTool"]
