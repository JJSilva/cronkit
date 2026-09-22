"""Pulling CORE body-temperature data out of a .FIT file.

A CORE sensor does not write native FIT fields. It registers *developer data
fields* — the device declares them in ``field_description`` messages and then
attaches them to each ``record``. fitdecode resolves those declarations for us,
so the fields simply appear on a record under the names the sensor chose.

The fields a CORE writes, as observed on a Garmin upload:

======================= ====== ==================================
Field                   Units  Meaning
======================= ====== ==================================
``core_temperature``    °C     Estimated core body temperature
``skin_temperature``    °C     Skin temperature at the sensor
``heat_strain_index``   a.u.   CORE's 0-10 heat strain index
``core_data_quality``   Q      Confidence, climbs as it stabilises
======================= ====== ==================================

``CIQ_core_temperature`` and ``CIQ_skin_temperature`` are the same readings in
Fahrenheit and are ignored; the conversion is done here instead so one code path
covers both unit settings.

Not every record carries the CORE fields — the sensor samples more slowly than
the watch records — so records without a core reading are skipped rather than
interpolated.
"""

import gzip
import io
import logging
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

import fitdecode

logger = logging.getLogger(__name__)

GZIP_MAGIC = b"\x1f\x8b"

CORE_TEMP_FIELD = "core_temperature"
SKIN_TEMP_FIELD = "skin_temperature"
HEAT_STRAIN_FIELD = "heat_strain_index"
QUALITY_FIELD = "core_data_quality"

# A CORE reports a plausible-looking constant until it has warmed up, but a
# reading outside human range is a decoding problem, not a hot athlete.
MIN_PLAUSIBLE_C = 30.0
MAX_PLAUSIBLE_C = 45.0


def to_fahrenheit(celsius: float) -> float:
    return celsius * 9 / 5 + 32


@dataclass(frozen=True)
class CoreSample:
    """One CORE reading."""

    timestamp: datetime
    core_c: float
    skin_c: float | None = None
    heat_strain: float | None = None
    quality: int | None = None


@dataclass(frozen=True)
class CoreSeries:
    """Every CORE reading in one file, in recording order."""

    samples: list[CoreSample]

    def __bool__(self) -> bool:
        return bool(self.samples)

    @property
    def start(self) -> datetime:
        return self.samples[0].timestamp

    @property
    def duration(self) -> timedelta:
        return self.samples[-1].timestamp - self.samples[0].timestamp

    def elapsed(self, sample: CoreSample) -> timedelta:
        return sample.timestamp - self.start

    # --- aggregates --------------------------------------------------------

    def _values(self, attr: str) -> list[float]:
        return [v for v in (getattr(s, attr) for s in self.samples) if v is not None]

    def stats(self, attr: str) -> tuple[float, float, float] | None:
        """``(min, mean, max)`` for one channel, or None when it is absent."""
        values = self._values(attr)
        if not values:
            return None
        return min(values), statistics.fmean(values), max(values)

    def time_above(self, threshold_c: float) -> timedelta:
        """How long core temperature sat at or above ``threshold_c``.

        Each sample is credited with the gap to the next one, so an irregular
        sampling rate does not distort the total. The final sample covers no
        span and is therefore not counted.
        """
        total = timedelta()
        for current, following in zip(self.samples, self.samples[1:], strict=False):
            if current.core_c >= threshold_c:
                total += following.timestamp - current.timestamp
        return total

    def downsampled(self, interval: timedelta) -> list[tuple[timedelta, CoreSample]]:
        """Average the series into ``interval``-sized buckets.

        Returns ``(elapsed, sample)`` pairs where elapsed is the bucket's start
        offset. A full-rate series is one reading per second, which is far too
        many to paste into a comment; bucketing keeps the shape of the curve at
        a readable length.
        """
        if not self.samples or interval <= timedelta(0):
            return []

        buckets: dict[int, list[CoreSample]] = {}
        for sample in self.samples:
            index = int(self.elapsed(sample) / interval)
            buckets.setdefault(index, []).append(sample)

        def mean_of(group: list[CoreSample], attr: str) -> float | None:
            values = [v for v in (getattr(s, attr) for s in group) if v is not None]
            return statistics.fmean(values) if values else None

        rows = []
        for index in sorted(buckets):
            group = buckets[index]
            rows.append(
                (
                    interval * index,
                    CoreSample(
                        timestamp=group[0].timestamp,
                        core_c=statistics.fmean(s.core_c for s in group),
                        skin_c=mean_of(group, "skin_c"),
                        heat_strain=mean_of(group, "heat_strain"),
                        quality=round(mean_of(group, "quality")) if mean_of(group, "quality") is not None else None,
                    ),
                )
            )
        return rows


def _decompress(raw: bytes) -> bytes:
    """Unwrap gzip if present. TrainingPeaks serves device uploads compressed."""
    if raw[:2] == GZIP_MAGIC:
        return gzip.decompress(raw)
    return raw


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def parse_core_series(raw: bytes) -> CoreSeries:
    """Read every CORE reading out of a (optionally gzipped) FIT file.

    Returns an empty series when the file has no CORE fields at all, which is
    the normal case for a workout recorded without the sensor.
    """
    samples: list[CoreSample] = []

    with fitdecode.FitReader(io.BytesIO(_decompress(raw))) as fit:
        for frame in fit:
            if frame.frame_type != fitdecode.FIT_FRAME_DATA or frame.name != "record":
                continue

            fields = {f.name: f.value for f in frame.fields}
            core = _number(fields.get(CORE_TEMP_FIELD))
            timestamp = fields.get("timestamp")
            if core is None or not isinstance(timestamp, datetime):
                continue
            if not MIN_PLAUSIBLE_C <= core <= MAX_PLAUSIBLE_C:
                logger.debug("Discarding implausible core temperature %.1f C", core)
                continue

            quality = _number(fields.get(QUALITY_FIELD))
            samples.append(
                CoreSample(
                    timestamp=timestamp,
                    core_c=core,
                    skin_c=_number(fields.get(SKIN_TEMP_FIELD)),
                    heat_strain=_number(fields.get(HEAT_STRAIN_FIELD)),
                    quality=int(quality) if quality is not None else None,
                )
            )

    samples.sort(key=lambda s: s.timestamp)
    return CoreSeries(samples=samples)
