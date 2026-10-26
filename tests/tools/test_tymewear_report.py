"""The Tymewear comment block: what it says, and how the processed marker is detected."""

from datetime import UTC, datetime, timedelta

import pytest

from cronkit.tools.trainingpeaks_core_temp.report import HEADER as CORE_HEADER
from cronkit.tools.trainingpeaks_tymewear.fit import TymeSample, TymeSeries
from cronkit.tools.trainingpeaks_tymewear.report import FOOTER, HEADER, ReportOptions, build_report, has_report


def series(*rates: float, step_seconds: int = 60) -> TymeSeries:
    start = datetime(2026, 10, 26, 22, 40, tzinfo=UTC)
    return TymeSeries(
        samples=[
            TymeSample(
                timestamp=start + timedelta(seconds=i * step_seconds),
                breath_rate=r,
                tidal_volume_l=2.0,
                minute_volume_l=r * 2.0,
            )
            for i, r in enumerate(rates)
        ]
    )


def test_the_block_is_delimited_at_both_ends():
    report = build_report(series(30.0, 40.0))
    assert report.startswith(HEADER)
    assert report.endswith(FOOTER)


def test_the_summary_reports_every_channel():
    report = build_report(series(30.0, 40.0))
    assert "BR  avg 35.0 / min 30.0 / max 40.0 brpm" in report
    assert "VE  avg 70 / max 80 L/min" in report
    assert "VT  avg 2.00 / max 2.00 L" in report
    assert "2 samples over 1m00s" in report


def test_summary_only_leaves_out_the_table():
    report = build_report(series(30.0, 40.0), ReportOptions(include_series=False))
    assert "Time" not in report
    assert "BR  avg" in report


def test_the_table_is_bucketed_at_the_configured_interval():
    report = build_report(series(*[30.0] * 20), ReportOptions(interval=timedelta(minutes=5)))
    rows = [line for line in report.splitlines() if line.startswith("0:")]
    assert [row.split()[0] for row in rows] == ["0:00", "0:05", "0:10", "0:15"]


def test_the_table_columns_line_up():
    """VE runs from two to three digits over a hard session."""
    report = build_report(series(20.0, 60.0, step_seconds=300), ReportOptions(interval=timedelta(minutes=5)))
    rows = [ln for ln in report.splitlines() if ln.startswith(("Time", "0:"))]
    assert len({len(ln) for ln in rows}) == 1, rows


def test_an_empty_series_is_refused():
    with pytest.raises(ValueError, match="at least one"):
        build_report(TymeSeries(samples=[]))


def test_a_block_in_the_thread_is_recognised_as_already_processed():
    assert has_report(["Nice one!", build_report(series(30.0))])


@pytest.mark.parametrize("thread", [[], [""], ["Felt great today."]])
def test_a_thread_without_the_block_is_not_marked_processed(thread):
    assert not has_report(thread)


def test_a_core_block_does_not_count_as_a_tymewear_block():
    """The two tools annotate the same workout; neither may suppress the other."""
    assert not has_report([f"{CORE_HEADER}\nCore  avg 98.6 °F"])
    assert HEADER not in CORE_HEADER and CORE_HEADER not in HEADER
