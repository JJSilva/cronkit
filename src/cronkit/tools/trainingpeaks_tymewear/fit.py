"""Pulling Tymewear breathing data out of a .FIT file.

The Tymewear strap pairs with the watch through a Connect IQ data field, which
writes its readings as *developer data fields* on each ``record`` — the same
mechanism a CORE sensor uses. fitdecode resolves the declarations, so the fields
appear on a record under the names Tymewear chose.

The fields it writes, as observed on a Garmin upload:

======================= ======= ===========================================
Field                   Units   Meaning
======================= ======= ===========================================
``tyme_breath_rate``    brpm    Breaths per minute
``tyme_tidal_volume``   vol/br  Air per breath, in centilitres
``tyme_minute_volume``  vol/min Ventilation (VE), in litres per minute
``tyme_time_stamp``     —       The strap's own clock; ignored
``tyme_developer_data`` —       Five unlabelled raw values; ignored
======================= ======= ===========================================

The units are declared only as "vol", so the scale was inferred: on a real run
``minute_volume ≈ breath_rate × tidal_volume / 100`` held to within 1% on
average, which makes tidal volume centilitres and minute volume litres. Tidal
volume is converted to litres here so both volumes read in the same unit.

Ventilatory thresholds and breathing zones are *not* in the file — Tymewear
computes those server-side — so this tool reports the raw channels only.
"""

import io
import logging
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

import fitdecode

from cronkit.integrations.fit import decompress, number

logger = logging.getLogger(__name__)

BREATH_RATE_FIELD = "tyme_breath_rate"
TIDAL_VOLUME_FIELD = "tyme_tidal_volume"
MINUTE_VOLUME_FIELD = "tyme_minute_volume"

# Centilitres per breath, as the strap records it.
TIDAL_VOLUME_SCALE = 100

# A breath rate of zero is the strap reporting no signal (loose fit, or not yet
# paired), not an athlete holding their breath. Anything above this is a
# decoding problem.
MAX_PLAUSIBLE_BRPM = 120.0


@dataclass(frozen=True)
class TymeSample:
    """One Tymewear reading."""

    timestamp: datetime
    breath_rate: float
    tidal_volume_l: float | None = None
    minute_volume_l: float | None = None


@dataclass(frozen=True)
class TymeSeries:
    """Every Tymewear reading in one file, in recording order."""

    samples: list[TymeSample]

    def __bool__(self) -> bool:
        return bool(self.samples)

    @property
    def start(self) -> datetime:
        return self.samples[0].timestamp

    @property
    def duration(self) -> timedelta:
        return self.samples[-1].timestamp - self.samples[0].timestamp

    def elapsed(self, sample: TymeSample) -> timedelta:
        return sample.timestamp - self.start

    def stats(self, attr: str) -> tuple[float, float, float] | None:
        """``(min, mean, max)`` for one channel, or None when it is absent."""
        values = [v for v in (getattr(s, attr) for s in self.samples) if v is not None]
        if not values:
            return None
        return min(values), statistics.fmean(values), max(values)

    def downsampled(self, interval: timedelta) -> list[tuple[timedelta, TymeSample]]:
        """Average the series into ``interval``-sized buckets.

        Returns ``(elapsed, sample)`` pairs where elapsed is the bucket's start
        offset, so a one-reading-per-second series stays short enough to paste
        into a comment.
        """
        if not self.samples or interval <= timedelta(0):
            return []

        buckets: dict[int, list[TymeSample]] = {}
        for sample in self.samples:
            buckets.setdefault(int(self.elapsed(sample) / interval), []).append(sample)

        def mean_of(group: list[TymeSample], attr: str) -> float | None:
            values = [v for v in (getattr(s, attr) for s in group) if v is not None]
            return statistics.fmean(values) if values else None

        return [
            (
                interval * index,
                TymeSample(
                    timestamp=buckets[index][0].timestamp,
                    breath_rate=statistics.fmean(s.breath_rate for s in buckets[index]),
                    tidal_volume_l=mean_of(buckets[index], "tidal_volume_l"),
                    minute_volume_l=mean_of(buckets[index], "minute_volume_l"),
                ),
            )
            for index in sorted(buckets)
        ]


def parse_tyme_series(raw: bytes) -> TymeSeries:
    """Read every Tymewear reading out of a (optionally gzipped) FIT file.

    Returns an empty series when the file has no Tymewear fields at all, which
    is the normal case for a workout recorded without the strap.
    """
    samples: list[TymeSample] = []

    with fitdecode.FitReader(io.BytesIO(decompress(raw))) as fit:
        for frame in fit:
            if frame.frame_type != fitdecode.FIT_FRAME_DATA or frame.name != "record":
                continue

            fields = {f.name: f.value for f in frame.fields}
            breath_rate = number(fields.get(BREATH_RATE_FIELD))
            timestamp = fields.get("timestamp")
            if breath_rate is None or not isinstance(timestamp, datetime):
                continue
            if not 0 < breath_rate <= MAX_PLAUSIBLE_BRPM:
                logger.debug("Discarding breath rate %.1f brpm", breath_rate)
                continue

            tidal = number(fields.get(TIDAL_VOLUME_FIELD))
            samples.append(
                TymeSample(
                    timestamp=timestamp,
                    breath_rate=breath_rate,
                    tidal_volume_l=tidal / TIDAL_VOLUME_SCALE if tidal else None,
                    minute_volume_l=number(fields.get(MINUTE_VOLUME_FIELD)) or None,
                )
            )

    samples.sort(key=lambda s: s.timestamp)
    return TymeSeries(samples=samples)
