"""Parsing of human-friendly units into SI base values.

All functions are pure and raise :class:`ValueError` with a message that names the
offending input, so the CLI can surface it verbatim.
"""

from __future__ import annotations

import re

_SIZE_RE = re.compile(r"^\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>[a-zA-Z]*)\s*$")

# Decimal (SI) multipliers: 1 GB = 10^9 bytes. This is what a DBA quoting "2 TB" means
# often enough, and the 7-10% gap to binary units is well inside the model's error bars.
_DECIMAL_BYTES: dict[str, float] = {
    "": 1.0,
    "b": 1.0,
    "k": 1e3,
    "kb": 1e3,
    "m": 1e6,
    "mb": 1e6,
    "g": 1e9,
    "gb": 1e9,
    "t": 1e12,
    "tb": 1e12,
    "p": 1e15,
    "pb": 1e15,
}

# Binary (IEC) multipliers for when the source quotes GiB/TiB (e.g. `df`, RDS console).
_BINARY_BYTES: dict[str, float] = {
    "ki": 2**10,
    "kib": 2**10,
    "mi": 2**20,
    "mib": 2**20,
    "gi": 2**30,
    "gib": 2**30,
    "ti": 2**40,
    "tib": 2**40,
    "pi": 2**50,
    "pib": 2**50,
}

# Link rates are always decimal: 1 Gbps = 10^9 bit/s.
_BITS_PER_SECOND: dict[str, float] = {
    "": 1e9,  # bare number => gigabits, the common case for WAN links
    "bps": 1.0,
    "k": 1e3,
    "kbps": 1e3,
    "m": 1e6,
    "mbps": 1e6,
    "g": 1e9,
    "gbps": 1e9,
    "t": 1e12,
    "tbps": 1e12,
}


def _split(text: str, what: str) -> tuple[float, str]:
    match = _SIZE_RE.match(text)
    if match is None:
        msg = f"Cannot parse {what} {text!r}: expected a number with an optional unit"
        raise ValueError(msg)
    return float(match.group("num")), match.group("unit").lower()


def parse_data_size(text: str) -> float:
    """Return the number of bytes described by ``text``.

    Accepts decimal units (``500G``, ``2TB``, ``1.5P``) and binary units (``100GiB``,
    ``2TiB``). A bare number is bytes.

    >>> parse_data_size("2T")
    2000000000000.0
    >>> parse_data_size("1GiB")
    1073741824.0
    """
    value, unit = _split(text, "data size")
    multiplier = _DECIMAL_BYTES.get(unit) or _BINARY_BYTES.get(unit)
    if multiplier is None:
        msg = f"Unknown data-size unit {unit!r} in {text!r}: use g, t, p (or gib, tib, pib)"
        raise ValueError(msg)
    if value <= 0:
        msg = f"Data size must be positive, got {text!r}"
        raise ValueError(msg)
    return value * multiplier


def parse_link_rate(text: str) -> float:
    """Return bits per second for a link size such as ``10G``, ``500M`` or ``1Gbps``.

    A bare number is read as gigabits per second.

    >>> parse_link_rate("10G")
    10000000000.0
    >>> parse_link_rate("500m")
    500000000.0
    """
    value, unit = _split(text, "link rate")
    multiplier = _BITS_PER_SECOND.get(unit)
    if multiplier is None:
        msg = f"Unknown link-rate unit {unit!r} in {text!r}: use m, g, t (bps)"
        raise ValueError(msg)
    if value <= 0:
        msg = f"Link rate must be positive, got {text!r}"
        raise ValueError(msg)
    return value * multiplier


def parse_percent(text: str) -> float:
    """Return a fraction (0..1) for a percentage such as ``0.1``, ``0.1%`` or ``1.5 %``.

    The value is always read as a percentage; ``0.1`` means 0.1 %, not 10 %.

    >>> parse_percent("0.1%")
    0.001
    """
    cleaned = text.strip().rstrip("%").strip()
    try:
        value = float(cleaned)
    except ValueError as exc:
        msg = f"Cannot parse percentage {text!r}"
        raise ValueError(msg) from exc
    if not 0 <= value < 100:
        msg = f"Percentage must be in [0, 100), got {text!r}"
        raise ValueError(msg)
    return value / 100.0


def parse_distance_km(text: str) -> float:
    """Return kilometres for ``450``, ``450km`` or ``280mi``."""
    value, unit = _split(text, "distance")
    if unit in ("", "km"):
        km = value
    elif unit in ("mi", "mile", "miles"):
        km = value * 1.609344
    else:
        msg = f"Unknown distance unit {unit!r} in {text!r}: use km or mi"
        raise ValueError(msg)
    return km


def format_bytes(num_bytes: float) -> str:
    """Render bytes with a decimal unit, e.g. ``2.00 TB``."""
    for unit, size in (("PB", 1e15), ("TB", 1e12), ("GB", 1e9), ("MB", 1e6), ("kB", 1e3)):
        if num_bytes >= size:
            return f"{num_bytes / size:.2f} {unit}"
    return f"{num_bytes:.0f} B"


def format_bps(bps: float) -> str:
    """Render bits per second with a decimal unit, e.g. ``940.0 Mbps``."""
    for unit, size in (("Tbps", 1e12), ("Gbps", 1e9), ("Mbps", 1e6), ("kbps", 1e3)):
        if bps >= size:
            return f"{bps / size:.1f} {unit}"
    return f"{bps:.0f} bps"


def format_duration(seconds: float) -> str:
    """Render a duration as the two most significant of weeks/days/hours/minutes/seconds."""
    if seconds == float("inf"):
        return "never (zero throughput)"
    if 0 < seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    units = (("w", 7 * 86400), ("d", 86400), ("h", 3600), ("m", 60), ("s", 1))
    parts: list[str] = []
    remaining = seconds
    for label, size in units:
        if remaining >= size:
            count = int(remaining // size)
            remaining -= count * size
            parts.append(f"{count}{label}")
        if len(parts) == 2:
            break
    return " ".join(parts) if parts else "0s"
