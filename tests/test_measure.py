import pytest

from network_transfer.measure import SMALL_SAMPLE_PACKETS, parse_measurement

PING_MAC = """\
PING 10.0.0.5 (10.0.0.5): 56 data bytes
64 bytes from 10.0.0.5: icmp_seq=0 ttl=57 time=12.345 ms
64 bytes from 10.0.0.5: icmp_seq=1 ttl=57 time=11.901 ms

--- 10.0.0.5 ping statistics ---
20 packets transmitted, 19 packets received, 5.0% packet loss
round-trip min/avg/max/stddev = 11.512/12.204/14.880/0.731 ms
"""

PING_LINUX = """\
PING db.example.net (192.0.2.10) 56(84) bytes of data.
64 bytes from 192.0.2.10: icmp_seq=1 ttl=54 time=9.87 ms

--- db.example.net ping statistics ---
200 packets transmitted, 200 received, 0% packet loss, time 199312ms
rtt min/avg/max/mdev = 9.512/10.044/18.221/0.912 ms
"""

MTR_CLEAN = """\
Start: 2026-10-23T12:30:01+0100
HOST: leeds-edge-01                 Loss%   Snt   Last   Avg  Best  Wrst StDev
  1.|-- 10.1.0.1                     0.0%   100    0.4   0.5   0.3   1.2   0.1
  2.|-- 185.12.4.1                  12.0%   100    2.1   2.3   1.9   9.8   0.9
  3.|-- ???                        100.0   100    0.0   0.0   0.0   0.0   0.0
  4.|-- 212.58.244.1                 0.0%   100    9.8  10.1   9.5  12.0   0.4
  5.|-- 52.93.12.7                   1.0%   100   11.9  12.3  11.7  15.1   0.5
"""

MTR_FILTERED_DEST = """\
HOST: host                          Loss%   Snt   Last   Avg  Best  Wrst StDev
  1.|-- 10.1.0.1                     0.0%    10    0.4   0.5   0.3   1.2   0.1
  2.|-- 212.58.244.1                 0.0%    10    9.8  10.1   9.5  12.0   0.4
  3.|-- ???                         100.0    10    0.0   0.0   0.0   0.0   0.0
"""

MTR_ASN = """\
HOST: host                          Loss%   Snt   Last   Avg  Best  Wrst StDev
  1. AS???    10.1.0.1               0.0%   100    0.4   0.5   0.3   1.2   0.1
  2. AS16509  52.93.12.7             0.5%   100   11.9  12.3  11.7  15.1   0.5
"""


def test_ping_macos() -> None:
    m = parse_measurement(PING_MAC)
    assert m.tool == "ping"
    assert m.rtt_ms == 12.204
    assert m.loss_fraction == pytest.approx(1 / 20)  # from counts, not the rounded 5.0%
    assert m.packets == 20
    assert m.target == "10.0.0.5"
    assert m.label == "ping, 20 pkts"
    assert m.notes == ()  # loss was observed, so no small-sample caveat


def test_ping_linux_small_zero_loss_sample_is_flagged() -> None:
    m = parse_measurement(
        PING_LINUX.replace(
            "200 packets transmitted, 200 received", "20 packets transmitted, 20 received"
        )
    )
    assert m.rtt_ms == 10.044
    assert m.loss_fraction == 0.0
    assert m.target == "db.example.net"
    assert len(m.notes) == 1
    assert "proves loss < 5%" in m.notes[0]
    assert f"mtr -rwc {SMALL_SAMPLE_PACKETS}" in m.notes[0]


def test_ping_linux_large_zero_loss_sample_is_not_flagged() -> None:
    m = parse_measurement(PING_LINUX)
    assert m.packets == 200
    assert m.notes == ()


def test_mtr_uses_last_hop_and_ignores_mid_path_loss() -> None:
    m = parse_measurement(MTR_CLEAN)
    assert m.tool == "mtr"
    assert m.rtt_ms == 12.3
    assert m.loss_fraction == pytest.approx(0.01)
    assert m.packets == 100
    assert m.target == "52.93.12.7"
    assert len(m.notes) == 1
    assert "1 intermediate hop(s) show loss; ignored" in m.notes[0]


def test_mtr_falls_back_to_last_responding_hop() -> None:
    m = parse_measurement(MTR_FILTERED_DEST)
    assert m.rtt_ms == 10.1
    assert m.target == "212.58.244.1"
    assert any("final hop 3 did not respond" in n for n in m.notes)
    assert any("using hop 2 212.58.244.1" in n for n in m.notes)
    assert any("proves loss < 10%" in n for n in m.notes)  # 10 probes, 0 % at that hop


def test_mtr_with_asn_column() -> None:
    m = parse_measurement(MTR_ASN)
    assert m.rtt_ms == 12.3
    assert m.loss_fraction == pytest.approx(0.005)
    assert m.target == "52.93.12.7"


def test_mtr_all_unresponsive_errors() -> None:
    text = (
        "HOST: h  Loss%  Snt  Last  Avg  Best  Wrst StDev\n"
        "  1.|-- ???  100.0  10  0.0  0.0  0.0  0.0  0.0\n"
    )
    with pytest.raises(ValueError, match="no responding hops"):
        parse_measurement(text)


def test_ping_missing_half_errors() -> None:
    with pytest.raises(ValueError, match="missing the 'packets transmitted"):
        parse_measurement("round-trip min/avg/max/stddev = 1.0/2.0/3.0/0.1 ms")
    with pytest.raises(ValueError, match="missing the rtt summary"):
        parse_measurement("10 packets transmitted, 10 packets received, 0.0% packet loss")


def test_ping_zero_sent_errors() -> None:
    text = (
        "0 packets transmitted, 0 packets received, 100.0% packet loss\n"
        "round-trip min/avg/max = 0/0/0 ms"
    )
    with pytest.raises(ValueError, match="zero packets"):
        parse_measurement(text)


def test_unrecognised_text_errors() -> None:
    with pytest.raises(ValueError, match="Could not recognise ping or mtr output"):
        parse_measurement("traceroute to 10.0.0.5, 30 hops max\n 1  10.1.0.1  0.4 ms")


def test_ping_without_target_lines_defaults_label() -> None:
    text = (
        "20 packets transmitted, 20 packets received, 0.0% packet loss\n"
        "rtt min/avg/max/mdev = 1/2/3/0.1 ms"
    )
    assert parse_measurement(text).target == "target"


def test_ping_loss_comes_from_counts_not_the_rounded_percentage() -> None:
    text = (
        "150 packets transmitted, 148 packets received, 1.3% packet loss\n"
        "round-trip min/avg/max/stddev = 9.0/10.0/11.0/0.5 ms"
    )
    m = parse_measurement(text)
    assert m.loss_fraction == pytest.approx(2 / 150)  # 1.333..., not 1.3
    assert m.loss_fraction != pytest.approx(0.013)
