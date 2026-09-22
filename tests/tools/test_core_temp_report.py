"""The comment block: what it says, and how it merges into an existing comment."""

from datetime import UTC, datetime, timedelta

import pytest

from cronkit.tools.trainingpeaks_core_temp.fit import CoreSample, CoreSeries
from cronkit.tools.trainingpeaks_core_temp.report import (
    FOOTER,
    HEADER,
    ReportOptions,
    build_report,
    has_report,
    merge_report,
)


def series(*cores: float, step_seconds: int = 60) -> CoreSeries:
    start = datetime(2026, 9, 22, 13, 0, tzinfo=UTC)
    return CoreSeries(
        samples=[
            CoreSample(
                timestamp=start + timedelta(seconds=i * step_seconds),
                core_c=c,
                skin_c=32.0,
                heat_strain=2.0,
                quality=80,
            )
            for i, c in enumerate(cores)
        ]
    )


# --- the block -------------------------------------------------------------


def test_the_block_is_delimited_at_both_ends():
    report = build_report(series(37.0, 38.0))
    assert report.startswith(HEADER)
    assert report.endswith(FOOTER)


def test_the_summary_reports_every_channel():
    report = build_report(series(36.0, 38.0))
    assert "Core  avg 37.0 / min 36.0 / max 38.0 °C" in report
    assert "Skin  avg 32.0 / max 32.0 °C" in report
    assert "HSI   avg 2.0 / max 2.0" in report


def test_time_above_the_threshold_is_reported_with_a_share():
    report = build_report(series(39.0, 39.0, 36.0), ReportOptions(threshold_c=38.0))
    assert "Above 38.0 °C: 2m00s (100%)" in report


def test_no_time_above_the_threshold_says_so_plainly():
    assert "Above 38.0 °C: none" in build_report(series(36.0, 36.5))


def test_fahrenheit_converts_the_summary_and_the_threshold():
    report = build_report(series(37.0), ReportOptions(fahrenheit=True))
    assert "98.6 °F" in report
    assert "Above 100.4 °F" in report


def test_summary_only_leaves_out_the_table():
    report = build_report(series(37.0, 38.0), ReportOptions(include_series=False))
    assert "Time" not in report
    assert "Core  avg" in report


def test_the_table_is_bucketed_at_the_configured_interval():
    report = build_report(series(*[37.0] * 20, step_seconds=60), ReportOptions(interval=timedelta(minutes=5)))
    rows = [line for line in report.splitlines() if line.startswith("0:")]
    assert [row.split()[0] for row in rows] == ["0:00", "0:05", "0:10", "0:15"]


def test_sub_minute_buckets_get_a_seconds_column():
    """Without this every row would render as the same 0:00."""
    report = build_report(series(*[37.0] * 4, step_seconds=10), ReportOptions(interval=timedelta(seconds=20)))
    rows = [line.split()[0] for line in report.splitlines() if line.startswith("0:")]
    assert rows == ["0:00:00", "0:00:20"]


def test_an_empty_series_is_refused():
    """A workout with no CORE data gets no comment, not a block saying so."""
    with pytest.raises(ValueError, match="at least one"):
        build_report(CoreSeries(samples=[]))


# --- the processed marker --------------------------------------------------


def test_a_block_is_recognised_as_already_processed():
    assert has_report(build_report(series(37.0)))


@pytest.mark.parametrize("comment", [None, "", "Felt great today."])
def test_an_unannotated_comment_is_not_marked_processed(comment):
    assert not has_report(comment)


# --- merging ---------------------------------------------------------------


def test_an_empty_comment_becomes_just_the_block():
    report = build_report(series(37.0))
    assert merge_report(None, report) == report
    assert merge_report("   ", report) == report


def test_the_athletes_own_words_are_kept_above_the_block():
    report = build_report(series(37.0))
    merged = merge_report("Legs felt heavy.", report)
    assert merged.startswith("Legs felt heavy.")
    assert merged.endswith(FOOTER)


def test_re_running_replaces_the_block_instead_of_stacking_them():
    """A re-uploaded file must rewrite the block, not append a second one."""
    first = build_report(series(37.0))
    second = build_report(series(39.0))
    merged = merge_report(merge_report("Notes.", first), second)

    assert merged.count(HEADER) == 1
    assert "39.0" in merged and "37.0" not in merged
    assert merged.startswith("Notes.")


def test_text_written_after_the_block_survives_a_rewrite():
    report = build_report(series(37.0))
    existing = f"Before.\n\n{report}\n\nAfter the block."
    merged = merge_report(existing, build_report(series(39.0)))

    assert merged.startswith("Before.")
    assert merged.endswith("After the block.")
    assert merged.count(HEADER) == 1


def test_a_block_with_no_footer_is_replaced_through_the_end():
    """Hand-edited or truncated blocks still must not accumulate."""
    existing = f"Notes.\n\n{HEADER}\nsome older text with no footer"
    merged = merge_report(existing, build_report(series(37.0)))

    assert merged.count(HEADER) == 1
    assert "some older text" not in merged
    assert merged.startswith("Notes.")
