"""Turning a CORE series into the text that goes in the workout comment.

The block is delimited by a header and a footer so that a later run can replace
it in place. Everything outside the markers is the athlete's own writing and is
never touched — which is the whole reason for having a footer rather than just
appending to the end of the field.

The header doubles as the "already processed" marker: if it is present, the
workout is skipped.
"""

from dataclasses import dataclass
from datetime import timedelta

from cronkit.tools.trainingpeaks_core_temp.fit import CoreSeries, to_fahrenheit

HEADER = "----- CORE Body Temperature -----"
FOOTER = "----- end CORE Body Temperature -----"


def _elapsed(delta: timedelta, *, seconds: bool = False) -> str:
    """``1:05`` — hours and minutes, for the elapsed column.

    Sub-minute buckets would all render as the same ``0:00``, so a caller using
    an interval finer than a minute asks for ``1:05:30`` instead.
    """
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

    fahrenheit: bool = False
    threshold_c: float = 38.0
    interval: timedelta = timedelta(minutes=5)
    include_series: bool = True

    @property
    def unit(self) -> str:
        return "°F" if self.fahrenheit else "°C"

    def temp(self, celsius: float) -> float:
        return to_fahrenheit(celsius) if self.fahrenheit else celsius


def build_report(series: CoreSeries, options: ReportOptions | None = None) -> str:
    """Render the full block, markers included.

    Callers must not pass an empty series: a workout with no CORE data gets no
    comment at all rather than a block saying so.
    """
    if not series:
        raise ValueError("build_report needs at least one CORE sample")

    opts = options or ReportOptions()
    lines = [HEADER]
    lines.extend(_summary_lines(series, opts))
    if opts.include_series:
        lines.append("")
        lines.extend(_series_lines(series, opts))
    lines.append(FOOTER)
    return "\n".join(lines)


def _summary_lines(series: CoreSeries, opts: ReportOptions) -> list[str]:
    lines = []

    core = series.stats("core_c")
    if core is not None:
        low, mean, high = (opts.temp(v) for v in core)
        lines.append(f"Core  avg {mean:.1f} / min {low:.1f} / max {high:.1f} {opts.unit}")

    skin = series.stats("skin_c")
    if skin is not None:
        _, mean, high = (opts.temp(v) for v in skin)
        lines.append(f"Skin  avg {mean:.1f} / max {high:.1f} {opts.unit}")

    strain = series.stats("heat_strain")
    if strain is not None:
        _, mean, high = strain
        lines.append(f"HSI   avg {mean:.1f} / max {high:.1f}")

    above = series.time_above(opts.threshold_c)
    total = series.duration
    if above:
        share = above / total * 100 if total else 0.0
        lines.append(f"Above {opts.temp(opts.threshold_c):.1f} {opts.unit}: {_duration(above)} ({share:.0f}%)")
    else:
        lines.append(f"Above {opts.temp(opts.threshold_c):.1f} {opts.unit}: none")

    tail = f"{len(series.samples)} samples over {_duration(total)}"
    quality = series.stats("quality")
    if quality is not None:
        tail += f" · quality avg {quality[1]:.0f}"
    lines.append(tail)

    return lines


def _series_lines(series: CoreSeries, opts: ReportOptions) -> list[str]:
    rows = series.downsampled(opts.interval)
    if not rows:
        return []

    fine = opts.interval % timedelta(minutes=1) != timedelta(0)
    width = 8 if fine else 6
    lines = [f"{'Time':<{width}} Core  Skin   HSI"]
    for elapsed, sample in rows:
        core = f"{opts.temp(sample.core_c):.1f}"
        skin = f"{opts.temp(sample.skin_c):.1f}" if sample.skin_c is not None else "-"
        strain = f"{sample.heat_strain:.1f}" if sample.heat_strain is not None else "-"
        lines.append(f"{_elapsed(elapsed, seconds=fine):<{width}} {core:>4}  {skin:>4}  {strain:>4}")
    return lines


def has_report(comment: str | None) -> bool:
    """Whether a comment already carries a block this tool wrote."""
    return bool(comment) and HEADER in comment


def merge_report(comment: str | None, report: str) -> str:
    """Put ``report`` into ``comment``, replacing any block already there.

    Replacing rather than appending is what makes a re-run safe: if a device
    re-uploads its file, the block is rewritten in place instead of stacking up.
    An existing block with no footer — hand-edited, or written before the footer
    existed — is replaced through to the end of the field, since there is no
    reliable way to tell where it was meant to stop.
    """
    existing = (comment or "").strip()
    if not existing:
        return report

    start = existing.find(HEADER)
    if start == -1:
        return f"{existing}\n\n{report}"

    end = existing.find(FOOTER, start)
    tail = existing[end + len(FOOTER) :] if end != -1 else ""
    return f"{existing[:start]}{report}{tail}".strip()
