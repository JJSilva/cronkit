"""Build a synthetic .FIT file carrying CORE developer fields.

A real device upload cannot be committed here: this repo is public, and a FIT
file off a watch contains the athlete's name, device serial numbers and GPS
coordinates. So the fixture is generated instead — same on-the-wire structure a
CORE sensor produces, none of the personal data.

Run it to regenerate the fixture::

    python tests/fixtures/make_core_fit.py

The encoding follows the FIT file format: a 14-byte header, a stream of
definition and data messages, and a trailing CRC. Developer fields are declared
by a ``developer_data_id`` message plus one ``field_description`` per field, and
are then attached to the ``record`` definition by index.
"""

import gzip
import struct
from datetime import UTC, datetime, timedelta
from pathlib import Path

# FIT timestamps count seconds from this epoch, not the Unix one.
FIT_EPOCH = datetime(1989, 12, 31, tzinfo=UTC)

# Base type ids, as the FIT spec numbers them.
UINT8 = 0x02
UINT16 = 0x84
UINT32 = 0x86
UINT32Z = 0x8C
SINT16 = 0x83
FLOAT32 = 0x88
STRING = 0x07
BYTE = 0x0D
ENUM = 0x00

CRC_TABLE = [
    0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
    0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400,
]  # fmt: skip

# The developer fields a CORE sensor writes, as (field number, name, units, type).
CORE_FIELDS = [
    (0, "core_temperature", "°C", FLOAT32),
    (10, "skin_temperature", "°C", FLOAT32),
    (19, "core_data_quality", "Q", SINT16),
    (95, "heat_strain_index", "a.u.", FLOAT32),
]


def crc16(data: bytes) -> int:
    """The FIT spec's nibble-table CRC."""
    crc = 0
    for byte in data:
        for nibble in (byte & 0x0F, (byte >> 4) & 0x0F):
            tmp = CRC_TABLE[crc & 0x0F]
            crc = (crc >> 4) & 0x0FFF
            crc = crc ^ tmp ^ CRC_TABLE[nibble]
    return crc


def definition(local: int, global_num: int, fields, dev_fields=()) -> bytes:
    """A definition message: what the following data messages will contain."""
    header = 0x40 | local | (0x20 if dev_fields else 0)
    out = struct.pack("<BBBHB", header, 0, 0, global_num, len(fields))
    for num, size, base in fields:
        out += struct.pack("<BBB", num, size, base)
    if dev_fields:
        out += struct.pack("<B", len(dev_fields))
        for num, size, dev_index in dev_fields:
            out += struct.pack("<BBB", num, size, dev_index)
    return out


def data(local: int, payload: bytes) -> bytes:
    return struct.pack("<B", local) + payload


def fit_string(value: str, size: int) -> bytes:
    """Null-terminated, fixed-width UTF-8, as FIT stores strings."""
    raw = value.encode("utf-8")[: size - 1]
    return raw + b"\x00" * (size - len(raw))


def build(samples: list[tuple[float, float, int, float]], start: datetime) -> bytes:
    """Encode ``(core_c, skin_c, quality, heat_strain)`` samples into a FIT file."""
    body = b""

    # file_id — every FIT file must open with one.
    body += definition(0, 0, [(0, 1, ENUM), (1, 2, UINT16), (2, 2, UINT16), (3, 4, UINT32Z), (4, 4, UINT32)])
    created = int((start - FIT_EPOCH).total_seconds())
    body += data(0, struct.pack("<BHHII", 4, 1, 0, 0, created))

    # developer_data_id — declares application index 0.
    body += definition(1, 207, [(1, 16, BYTE), (3, 1, UINT8)])
    body += data(1, b"\x00" * 16 + struct.pack("<B", 0))

    # field_description — one per developer field.
    body += definition(2, 206, [(0, 1, UINT8), (1, 1, UINT8), (2, 1, UINT8), (3, 32, STRING), (8, 16, STRING)])
    for num, name, units, base in CORE_FIELDS:
        body += data(2, struct.pack("<BBB", 0, num, base) + fit_string(name, 32) + fit_string(units, 16))

    # record — timestamp, heart rate, and the four developer fields.
    body += definition(
        3,
        20,
        [(253, 4, UINT32), (3, 1, UINT8)],
        dev_fields=[(0, 4, 0), (10, 4, 0), (19, 2, 0), (95, 4, 0)],
    )
    for index, (core, skin, quality, strain) in enumerate(samples):
        stamp = int((start + timedelta(seconds=index) - FIT_EPOCH).total_seconds())
        body += data(3, struct.pack("<IBffhf", stamp, 120, core, skin, quality, strain))

    header = struct.pack("<BBHI4s", 14, 0x20, 2140, len(body), b".FIT")
    header += struct.pack("<H", crc16(header))
    return header + body + struct.pack("<H", crc16(header + body))


def sample_ride() -> list[tuple[float, float, int, float]]:
    """Twenty minutes of a warming athlete, one reading per second.

    Core climbs 36.8 -> 38.6 °C so the fixture exercises both sides of the
    default 38.0 °C threshold.
    """
    samples = []
    for second in range(20 * 60):
        progress = second / (20 * 60 - 1)
        samples.append(
            (
                round(36.8 + 1.8 * progress, 2),
                round(31.0 + 3.5 * progress, 2),
                min(100, int(progress * 120)),
                round(8.0 * progress, 2),
            )
        )
    return samples


if __name__ == "__main__":
    out = Path(__file__).parent / "core_ride.fit.gz"
    raw = build(sample_ride(), datetime(2026, 9, 22, 13, 0, tzinfo=UTC))
    out.write_bytes(gzip.compress(raw))
    print(f"wrote {out} ({out.stat().st_size} bytes gzipped, {len(raw)} raw)")
