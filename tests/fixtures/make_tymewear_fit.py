"""Build a synthetic .FIT file carrying Tymewear developer fields.

The same reasoning as ``make_core_fit.py``: a real upload carries the athlete's
name, serial numbers and GPS, so the fixture is generated instead. The encoding
helpers are shared with that script.

Run it to regenerate the fixture::

    python tests/fixtures/make_tymewear_fit.py
"""

import gzip
import struct
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from make_core_fit import (  # noqa: E402
    BYTE,
    ENUM,
    FIT_EPOCH,
    FLOAT32,
    STRING,
    UINT8,
    UINT16,
    UINT32,
    UINT32Z,
    crc16,
    data,
    definition,
    fit_string,
)

# The developer fields the Tymewear Connect IQ field writes, as
# (field number, name, units, type).
TYME_FIELDS = [
    (2, "tyme_breath_rate", "brpm", FLOAT32),
    (3, "tyme_tidal_volume", "vol/br", UINT16),
    (4, "tyme_minute_volume", "vol/min", UINT16),
]


def build(samples: list[tuple[float, int, int]], start: datetime) -> bytes:
    """Encode ``(breath_rate, tidal_cl, minute_volume_l)`` samples into a FIT file."""
    body = b""

    body += definition(0, 0, [(0, 1, ENUM), (1, 2, UINT16), (2, 2, UINT16), (3, 4, UINT32Z), (4, 4, UINT32)])
    created = int((start - FIT_EPOCH).total_seconds())
    body += data(0, struct.pack("<BHHII", 4, 1, 0, 0, created))

    body += definition(1, 207, [(1, 16, BYTE), (3, 1, UINT8)])
    body += data(1, b"\x00" * 16 + struct.pack("<B", 0))

    body += definition(2, 206, [(0, 1, UINT8), (1, 1, UINT8), (2, 1, UINT8), (3, 32, STRING), (8, 16, STRING)])
    for num, name, units, base in TYME_FIELDS:
        body += data(2, struct.pack("<BBB", 0, num, base) + fit_string(name, 32) + fit_string(units, 16))

    body += definition(
        3,
        20,
        [(253, 4, UINT32), (3, 1, UINT8)],
        dev_fields=[(2, 4, 0), (3, 2, 0), (4, 2, 0)],
    )
    for index, (rate, tidal, minute) in enumerate(samples):
        stamp = int((start + timedelta(seconds=index) - FIT_EPOCH).total_seconds())
        body += data(3, struct.pack("<IBfHH", stamp, 140, rate, tidal, minute))

    header = struct.pack("<BBHI4s", 14, 0x20, 2140, len(body), b".FIT")
    header += struct.pack("<H", crc16(header))
    return header + body + struct.pack("<H", crc16(header + body))


def sample_run() -> list[tuple[float, int, int]]:
    """Twenty minutes of a building effort, one reading per second.

    Breath rate climbs 24 -> 48 brpm and tidal volume 1.50 -> 2.50 L; minute
    volume is their product, as the strap reports it.
    """
    samples = []
    for second in range(20 * 60):
        progress = second / (20 * 60 - 1)
        rate = round(24.0 + 24.0 * progress, 1)
        tidal = round(150 + 100 * progress)
        samples.append((rate, tidal, round(rate * tidal / 100)))
    return samples


if __name__ == "__main__":
    out = Path(__file__).parent / "tymewear_run.fit.gz"
    raw = build(sample_run(), datetime(2026, 10, 26, 22, 40, tzinfo=UTC))
    out.write_bytes(gzip.compress(raw))
    print(f"wrote {out} ({out.stat().st_size} bytes gzipped, {len(raw)} raw)")
