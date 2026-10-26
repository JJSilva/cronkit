"""Small helpers for reading device .FIT files.

Shared by every tool that pulls sensor data out of a TrainingPeaks upload. The
format itself is decoded by fitdecode; this is only the glue around it.
"""

import gzip

GZIP_MAGIC = b"\x1f\x8b"


def decompress(raw: bytes) -> bytes:
    """Unwrap gzip if present. TrainingPeaks serves device uploads compressed."""
    if raw[:2] == GZIP_MAGIC:
        return gzip.decompress(raw)
    return raw


def number(value: object) -> float | None:
    """A field value as a float, or None when it is missing or not numeric."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)
