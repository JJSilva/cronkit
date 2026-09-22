"""Reading CORE developer fields out of a FIT file."""

import gzip
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cronkit.tools.trainingpeaks_core_temp.fit import CoreSample, CoreSeries, parse_core_series, to_fahrenheit

FIXTURE = Path(__file__).parent.parent / "fixtures" / "core_ride.fit.gz"


@pytest.fixture(scope="module")
def ride() -> CoreSeries:
    return parse_core_series(FIXTURE.read_bytes())


def series(*cores: float, step_seconds: int = 1) -> CoreSeries:
    start = datetime(2026, 9, 22, 13, 0, tzinfo=UTC)
    return CoreSeries(
        samples=[
            CoreSample(timestamp=start + timedelta(seconds=i * step_seconds), core_c=c, skin_c=30.0 + i)
            for i, c in enumerate(cores)
        ]
    )


# --- parsing ---------------------------------------------------------------


def test_every_core_reading_is_read(ride):
    assert len(ride.samples) == 1200
    assert ride.duration == timedelta(seconds=1199)


def test_all_four_channels_are_captured(ride):
    first = ride.samples[0]
    assert first.core_c == pytest.approx(36.8, abs=0.01)
    assert first.skin_c == pytest.approx(31.0, abs=0.01)
    assert first.heat_strain == pytest.approx(0.0, abs=0.01)
    assert first.quality == 0


def test_a_plain_uncompressed_file_also_works(ride):
    """TrainingPeaks serves uploads gzipped, but nothing guarantees it."""
    plain = parse_core_series(gzip.decompress(FIXTURE.read_bytes()))
    assert len(plain.samples) == len(ride.samples)


def test_a_file_without_core_fields_yields_an_empty_series():
    """The normal case for a workout recorded without the sensor."""
    from tests.fixtures.make_core_fit import build

    empty = parse_core_series(build([], datetime(2026, 9, 22, 13, 0, tzinfo=UTC)))
    assert not empty
    assert empty.samples == []


def test_readings_outside_human_range_are_discarded():
    """A wild value is a decoding problem, not a hot athlete."""
    from tests.fixtures.make_core_fit import build

    raw = build([(37.0, 31.0, 50, 1.0), (412.0, 31.0, 50, 1.0)], datetime(2026, 9, 22, 13, 0, tzinfo=UTC))
    assert [s.core_c for s in parse_core_series(raw).samples] == [pytest.approx(37.0)]


# --- aggregates ------------------------------------------------------------


def test_stats_returns_min_mean_max():
    low, mean, high = series(36.0, 37.0, 38.0).stats("core_c")
    assert (low, high) == (36.0, 38.0)
    assert mean == pytest.approx(37.0)


def test_stats_is_none_for_a_channel_the_sensor_did_not_write():
    assert series(37.0).stats("heat_strain") is None


def test_time_above_credits_each_sample_with_the_gap_that_follows_it():
    """An irregular sampling rate must not distort the total."""
    hot = series(39.0, 39.0, 36.0, step_seconds=10)
    # Two hot samples, but only the first two gaps; the last sample spans nothing.
    assert hot.time_above(38.0) == timedelta(seconds=20)


def test_time_above_is_zero_when_nothing_crosses():
    assert series(36.0, 36.5).time_above(38.0) == timedelta()


def test_the_threshold_is_inclusive():
    assert series(38.0, 38.0).time_above(38.0) == timedelta(seconds=1)


# --- downsampling ----------------------------------------------------------


def test_downsampling_averages_within_each_bucket():
    rows = series(36.0, 38.0, 40.0, 42.0, step_seconds=1).downsampled(timedelta(seconds=2))
    offsets = [offset for offset, _ in rows]
    assert offsets == [timedelta(0), timedelta(seconds=2)]
    assert [s.core_c for _, s in rows] == [pytest.approx(37.0), pytest.approx(41.0)]


def test_downsampling_a_real_ride_is_short_enough_to_paste(ride):
    """The whole point: 1200 readings must not become 1200 lines."""
    assert len(ride.downsampled(timedelta(minutes=5))) == 4


def test_downsampling_an_empty_series_is_empty():
    assert CoreSeries(samples=[]).downsampled(timedelta(minutes=5)) == []


def test_a_zero_interval_is_refused_rather_than_dividing_by_zero():
    assert series(37.0).downsampled(timedelta(0)) == []


# --- units -----------------------------------------------------------------


def test_celsius_converts_to_fahrenheit():
    assert to_fahrenheit(37.0) == pytest.approx(98.6)
    assert to_fahrenheit(0.0) == pytest.approx(32.0)
