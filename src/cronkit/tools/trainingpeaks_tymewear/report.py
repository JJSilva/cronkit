"""Turning a Tymewear series into the text that goes in the workout comment.

Like the CORE block, this is posted as its own entry in the workout's comment
thread, and its header doubles as the "already processed" marker. The header is
distinct from CORE's, so the two tools annotate the same workout independently.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta

from cronkit.tools.trainingpeaks_tymewear.fit import TymeSeries

HEADER = "----- Tymewear Breathing -----"
FOOTER = "----- end Tymewear Breathing -----"


def _elapsed(delta: timedelta, *, seconds: bool = False) -> str:
    """``1:05`` — hours and minutes, or ``1:05:30`` for sub-minute buckets."""
    total = int(delta.total_seconds())
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if seconds:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{hours}:{minutes:02d}"


def _duration(delta: timedelta) -> str:
    """``1h05m`` / ``42m18s`` — compact, for prose."""
    seconds = int(delta.total_seconds())
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


@dataclass(frozen=True)
class ReportOptions:
    """How to render the block."""

    interval: timedelta = timedelta(minutes=5)
    include_series: bool = True


def build_report(series: TymeSeries, options: ReportOptions | None = None) -> str:
    """Render the full block, markers included.

    Callers must not pass an empty series: a workout with no Tymewear data gets
    no comment at all rather than a block saying so.
    """
    if not series:
        raise ValueError("build_report needs at least one Tymewear sample")

    opts = options or ReportOptions()
    lines = [HEADER]
    lines.extend(_summary_lines(series))
    if opts.include_series:
        lines.append("")
        lines.extend(_series_lines(series, opts))
    lines.append(FOOTER)
    return "\n".join(lines)


def _summary_lines(series: TymeSeries) -> list[str]:
    lines = []

    rate = series.stats("breath_rate")
    if rate is not None:
        low, mean, high = rate
        lines.append(f"BR  avg {mean:.1f} / min {low:.1f} / max {high:.1f} brpm")

    ventilation = series.stats("minute_volume_l")
    if ventilation is not None:
        _, mean, high = ventilation
        lines.append(f"VE  avg {mean:.0f} / max {high:.0f} L/min")

    tidal = series.stats("tidal_volume_l")
    if tidal is not None:
        _, mean, high = tidal
        lines.append(f"VT  avg {mean:.2f} / max {high:.2f} L")

    lines.append(f"{len(series.samples)} samples over {_duration(series.duration)}")
    return lines


def _series_lines(series: TymeSeries, opts: ReportOptions) -> list[str]:
    rows = series.downsampled(opts.interval)
    if not rows:
        return []

    fine = opts.interval % timedelta(minutes=1) != timedelta(0)
    cells = [
        (
            _elapsed(elapsed, seconds=fine),
            f"{sample.breath_rate:.1f}",
            f"{sample.minute_volume_l:.0f}" if sample.minute_volume_l is not None else "-",
            f"{sample.tidal_volume_l:.2f}" if sample.tidal_volume_l is not None else "-",
        )
        for elapsed, sample in rows
    ]
    headings = ("Time", "BR", "VE", "VT")
    widths = [max(len(h), *(len(row[i]) for row in cells)) for i, h in enumerate(headings)]

    def line(values: tuple[str, ...]) -> str:
        time, *rest = values
        columns = "  ".join(v.rjust(w) for v, w in zip(rest, widths[1:], strict=True))
        return f"{time.ljust(widths[0])}  {columns}"

    return [line(headings), *(line(row) for row in cells)]


def has_report(comments: Iterable[str]) -> bool:
    """Whether a workout's comment thread already carries a block we wrote."""
    return any(HEADER in (comment or "") for comment in comments)
