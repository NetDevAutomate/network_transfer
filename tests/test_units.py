import pytest

from network_transfer.units import (
    format_bps,
    format_bytes,
    format_duration,
    parse_data_rate,
    parse_data_size,
    parse_distance_km,
    parse_link_rate,
    parse_percent,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("500G", 500e9),
        ("2T", 2e12),
        ("2tb", 2e12),
        ("1.5P", 1.5e15),
        ("100GiB", 100 * 2**30),
        ("2 TiB", 2 * 2**40),
        ("6MiB", 6 * 2**20),
        ("1024", 1024.0),
    ],
)
def test_parse_data_size(text: str, expected: float) -> None:
    assert parse_data_size(text) == pytest.approx(expected)


@pytest.mark.parametrize("bad", ["", "abc", "2X", "-1G", "0G"])
def test_parse_data_size_rejects(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_data_size(bad)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("10G", 10e9), ("1g", 1e9), ("500M", 500e6), ("1Gbps", 1e9), ("10", 10e9), ("100kbps", 1e5)],
)
def test_parse_link_rate(text: str, expected: float) -> None:
    assert parse_link_rate(text) == pytest.approx(expected)


@pytest.mark.parametrize("bad", ["10X", "0G", "fast"])
def test_parse_link_rate_rejects(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_link_rate(bad)


@pytest.mark.parametrize(
    ("text", "expected"), [("0.1", 0.001), ("0.1%", 0.001), (" 1.5 % ", 0.015), ("0", 0.0)]
)
def test_parse_percent(text: str, expected: float) -> None:
    assert parse_percent(text) == pytest.approx(expected)


@pytest.mark.parametrize("bad", ["100", "-1", "x%"])
def test_parse_percent_rejects(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_percent(bad)


def test_parse_distance() -> None:
    assert parse_distance_km("450") == 450
    assert parse_distance_km("450km") == 450
    assert parse_distance_km("100mi") == pytest.approx(160.9344)
    with pytest.raises(ValueError):
        parse_distance_km("5 leagues")
    with pytest.raises(ValueError):
        parse_distance_km("-5km")


def test_format_helpers() -> None:
    assert format_bytes(2e12) == "2.00 TB"
    assert format_bytes(512) == "512 B"
    assert format_bps(940e6) == "940.0 Mbps"
    assert format_bps(10e9) == "10.0 Gbps"
    assert format_bps(12) == "12 bps"


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0.25, "250 ms"),
        (0, "0s"),
        (59, "59s"),
        (3661, "1h 1m"),
        (90000, "1d 1h"),
        (8 * 86400, "1w 1d"),
        (float("inf"), "never (zero throughput)"),
    ],
)
def test_format_duration(seconds: float, expected: str) -> None:
    assert format_duration(seconds) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("86.4G/day", 1e6),
        ("40G/day", 40e9 / 86400),
        ("2GB/h", 2e9 / 3600),
        ("500MB/hour", 500e6 / 3600),
        ("1T/week", 1e12 / (7 * 86400)),
        ("1.5GB/s", 1.5e9),
        ("600M/min", 1e7),
        ("1GiB/hr", 2**30 / 3600),
        ("0/day", 0.0),
    ],
)
def test_parse_data_rate(text: str, expected: float) -> None:
    assert parse_data_rate(text) == pytest.approx(expected)


@pytest.mark.parametrize("bad", ["40G", "40G/fortnight", "40X/day", "fast/day"])
def test_parse_data_rate_rejects(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_data_rate(bad)
