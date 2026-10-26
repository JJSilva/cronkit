"""Reading Tymewear developer fields out of a FIT file."""

import gzip
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cronkit.tools.trainingpeaks_tymewear.fit import TymeSample, TymeSeries, parse_tyme_series

FIXTURE = Path(__file__).parent.parent / "fixtures" / "tymewear_run.fit.gz"
START = datetime(2026, 10, 26, 22, 40, tzinfo=UTC)


@pytest.fixture(scope="module")
def run() -> TymeSeries:
    return parse_tyme_series(FIXTURE.read_bytes())


def series(*rates: float, step_seconds: int = 1) -> TymeSeries:
    return TymeSeries(
        samples=[
            TymeSample(timestamp=START + timedelta(seconds=i * step_seconds), breath_rate=r, minute_volume_l=r * 2)
            for i, r in enumerate(rates)
        ]
    )


# --- parsing ---------------------------------------------------------------


def test_every_reading_is_read(run):
    assert len(run.samples) == 1200
    assert run.duration == timedelta(seconds=1199)


def test_all_three_channels_are_captured(run):
    first = run.samples[0]
    assert first.breath_rate == pytest.approx(24.0)
    assert first.minute_volume_l == pytest.approx(36.0)


def test_tidal_volume_is_converted_from_centilitres_to_litres(run):
    """The strap records 150 for a 1.5 L breath."""
    assert run.samples[0].tidal_volume_l == pytest.approx(1.5)
    assert run.samples[-1].tidal_volume_l == pytest.approx(2.5)


def test_a_plain_uncompressed_file_also_works(run):
    plain = parse_tyme_series(gzip.decompress(FIXTURE.read_bytes()))
    assert len(plain.samples) == len(run.samples)


def test_a_file_without_tymewear_fields_yields_an_empty_series():
    """The normal case for a workout recorded without the strap — e.g. a CORE-only ride."""
    core_only = Path(__file__).parent.parent / "fixtures" / "core_ride.fit.gz"
    assert not parse_tyme_series(core_only.read_bytes())


def test_a_zero_breath_rate_is_no_signal_not_a_reading():
    """The strap reports zero before it locks on; that must not drag the average down."""
    from tests.fixtures.make_tymewear_fit import build

    raw = build([(0.0, 0, 0), (30.0, 200, 60), (500.0, 200, 60)], START)
    assert [s.breath_rate for s in parse_tyme_series(raw).samples] == [pytest.approx(30.0)]


# --- aggregates ------------------------------------------------------------


def test_stats_returns_min_mean_max():
    low, mean, high = series(20.0, 30.0, 40.0).stats("breath_rate")
    assert (low, high) == (20.0, 40.0)
    assert mean == pytest.approx(30.0)


def test_stats_is_none_for_a_channel_the_strap_did_not_write():
    assert series(30.0).stats("tidal_volume_l") is None


def test_downsampling_averages_within_each_bucket():
    rows = series(20.0, 30.0, 40.0, 50.0).downsampled(timedelta(seconds=2))
    assert [offset for offset, _ in rows] == [timedelta(0), timedelta(seconds=2)]
    assert [s.breath_rate for _, s in rows] == [pytest.approx(25.0), pytest.approx(45.0)]
    assert [s.minute_volume_l for _, s in rows] == [pytest.approx(50.0), pytest.approx(90.0)]


def test_downsampling_a_real_run_is_short_enough_to_paste(run):
    assert len(run.downsampled(timedelta(minutes=5))) == 4


def test_a_zero_interval_is_refused_rather_than_dividing_by_zero():
    assert series(30.0).downsampled(timedelta(0)) == []
