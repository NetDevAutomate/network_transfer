import math

import pytest

from network_transfer.model import (
    FIBRE_KM_PER_MS,
    PATH_DEFAULTS,
    PROFILES,
    PathType,
    TransferInputs,
    cubic_bound_bps,
    estimate_latency,
    estimate_transfer,
    reno_bound_bps,
)

LONDON_DUBLIN_KM = 464.0
LONDON_SYDNEY_KM = 16_990.0


def make_inputs(**overrides: object) -> TransferInputs:
    base: dict[str, object] = {
        "data_bytes": 2e12,
        "distance_km": LONDON_DUBLIN_KM,
        "link_bps": 1e9,
        "profile": PROFILES["full"],
        "loss_fraction": 0.0,
        "path": PathType.INTERNET,
    }
    base.update(overrides)
    return TransferInputs(**base)  # type: ignore[arg-type]


# --- profiles ---------------------------------------------------------------------------


def test_full_profile_efficiency_and_mss() -> None:
    full = PROFILES["full"]
    assert full.mss_bytes == 1448
    assert full.efficiency == pytest.approx(1448 / 1538, rel=1e-6)


def test_jumbo_profile_is_more_efficient_than_full() -> None:
    jumbo = PROFILES["jumbo"]
    assert jumbo.mss_bytes == 8949
    assert jumbo.efficiency == pytest.approx(8949 / 9039, rel=1e-6)
    assert jumbo.efficiency > PROFILES["full"].efficiency


def test_imix_profile_pays_for_small_frames() -> None:
    imix = PROFILES["imix"]
    # 64-byte frames carry no TCP payload (header-only), 570 -> 500, 1518 -> 1448.
    expected_payload = (7 * 0 + 4 * 500 + 1 * 1448) / 12
    expected_wire = (7 * 84 + 4 * 590 + 1 * 1538) / 12
    assert imix.payload_bytes_per_frame == pytest.approx(expected_payload)
    assert imix.wire_bytes_per_frame == pytest.approx(expected_wire)
    assert imix.efficiency == pytest.approx(expected_payload / expected_wire)
    assert imix.efficiency < 0.8
    assert imix.mss_bytes == 1448  # window growth still happens in full segments


# --- latency ----------------------------------------------------------------------------


def test_latency_internet_london_dublin() -> None:
    lat = estimate_latency(make_inputs())
    fibre = LONDON_DUBLIN_KM * 1.4
    assert lat.fibre_km == pytest.approx(fibre)
    assert lat.propagation_one_way_ms == pytest.approx(fibre / FIBRE_KM_PER_MS)
    assert lat.rtt_ms == pytest.approx(2 * fibre / FIBRE_KM_PER_MS + 3.0)
    assert lat.source == "estimated"
    assert 8 < lat.rtt_ms < 11


def test_latency_dx_has_lower_fixed_overhead() -> None:
    inet = estimate_latency(make_inputs(path=PathType.INTERNET)).rtt_ms
    dx = estimate_latency(make_inputs(path=PathType.DIRECT_CONNECT)).rtt_ms
    assert inet - dx == pytest.approx(
        PATH_DEFAULTS[PathType.INTERNET].fixed_overhead_ms
        - PATH_DEFAULTS[PathType.DIRECT_CONNECT].fixed_overhead_ms
    )


def test_latency_same_metro_is_just_overhead() -> None:
    lat = estimate_latency(make_inputs(distance_km=0.0))
    assert lat.rtt_ms == PATH_DEFAULTS[PathType.INTERNET].fixed_overhead_ms


def test_latency_route_factor_scales_propagation() -> None:
    lat = estimate_latency(make_inputs(route_factor=2.0))
    assert lat.fibre_km == pytest.approx(LONDON_DUBLIN_KM * 2.0)


def test_latency_override_wins() -> None:
    lat = estimate_latency(make_inputs(rtt_ms_override=12.5))
    assert lat.rtt_ms == 12.5
    assert lat.source == "measured (--rtt)"
    # the distance-derived fields are still reported for context
    assert lat.fibre_km > 0


# --- congestion-control bounds ----------------------------------------------------------


def test_reno_bound_matches_mathis() -> None:
    assert reno_bound_bps(1448, 0.1, 1e-4) == pytest.approx(1.22 * 1448 * 8 / (0.1 * 0.01))


def test_cubic_bound_matches_rfc8312_table2() -> None:
    # RFC 8312 Table 2: RTT = 0.01 s, p = 1e-7, C = 0.4 -> average window 5926 segments.
    mss = 1448
    rtt = 0.01
    bps = cubic_bound_bps(mss, rtt, 1e-7)
    window_segments = bps * rtt / (mss * 8)
    assert window_segments == pytest.approx(5926, rel=0.001)


def test_zero_loss_bounds_are_infinite() -> None:
    assert reno_bound_bps(1448, 0.1, 0.0) == math.inf
    assert cubic_bound_bps(1448, 0.1, 0.0) == math.inf


def test_cubic_beats_reno_on_long_fat_paths_and_not_on_short_ones() -> None:
    # Long RTT, low loss: CUBIC's RTT^-0.25 scaling wins.
    assert cubic_bound_bps(1448, 0.2, 1e-6) > reno_bound_bps(1448, 0.2, 1e-6)
    # Short RTT, high loss: the TCP-friendly region, Reno rate is higher.
    assert reno_bound_bps(1448, 0.005, 1e-2) > cubic_bound_bps(1448, 0.005, 1e-2)


# --- ceilings ---------------------------------------------------------------------------


def test_link_binds_when_short_path_and_no_loss() -> None:
    est = estimate_transfer(make_inputs())
    tp = est.throughput
    assert tp.aggregate_bps == pytest.approx(1e9 * PROFILES["full"].efficiency)
    assert tp.bottleneck.startswith("link")
    assert est.bytes_including_retransmits == est.inputs.data_bytes
    # 2 TB over ~941 Mbps is about 4.7 hours
    assert 4.5 * 3600 < est.seconds < 5 * 3600


def test_window_binds_single_stream_long_path() -> None:
    inputs = make_inputs(
        distance_km=LONDON_SYDNEY_KM, streams=1, tcp_window_bytes=6 * 2**20, loss_fraction=0.0
    )
    est = estimate_transfer(inputs)
    rtt_s = est.latency.rtt_ms / 1000
    assert est.throughput.aggregate_bps == pytest.approx(6 * 2**20 * 8 / rtt_s)
    assert est.throughput.bottleneck.startswith("TCP receive window")
    assert est.throughput.aggregate_bps < est.throughput.link_goodput_bps


def test_streams_multiply_window_bound_until_link_caps_it() -> None:
    one = estimate_transfer(make_inputs(distance_km=LONDON_SYDNEY_KM, streams=1))
    four = estimate_transfer(make_inputs(distance_km=LONDON_SYDNEY_KM, streams=4))
    many = estimate_transfer(make_inputs(distance_km=LONDON_SYDNEY_KM, streams=64))
    assert four.throughput.aggregate_bps == pytest.approx(4 * one.throughput.aggregate_bps)
    assert many.throughput.aggregate_bps == pytest.approx(many.throughput.link_goodput_bps)
    assert many.throughput.bottleneck.startswith("link")


def test_loss_binds_on_long_lossy_internet_path() -> None:
    est = estimate_transfer(make_inputs(distance_km=LONDON_SYDNEY_KM, loss_fraction=0.001))
    tp = est.throughput
    assert tp.bottleneck.startswith("TCP loss")
    assert tp.per_flow_bps == pytest.approx(
        max(tp.per_flow_reno_bound_bps, tp.per_flow_cubic_bound_bps)
    )
    assert tp.aggregate_bps == pytest.approx(8 * tp.per_flow_bps)
    assert tp.aggregate_bps < 0.05 * tp.link_goodput_bps
    assert est.tcp_penalty_factor > 10
    # retransmissions inflate the bytes on the wire by 1/(1-p)
    assert est.bytes_including_retransmits == pytest.approx(2e12 / 0.999)


def test_loss_bound_uses_larger_of_reno_and_cubic() -> None:
    # Short RTT + heavy loss puts us in the TCP-friendly region: Reno should be chosen.
    est = estimate_transfer(
        make_inputs(distance_km=50, loss_fraction=0.02, link_bps=100e9, streams=1)
    )
    tp = est.throughput
    assert tp.per_flow_reno_bound_bps > tp.per_flow_cubic_bound_bps
    assert tp.per_flow_bps == pytest.approx(tp.per_flow_reno_bound_bps)


def test_jumbo_raises_loss_bound_sixfold() -> None:
    full = estimate_transfer(
        make_inputs(distance_km=LONDON_SYDNEY_KM, loss_fraction=0.001, profile=PROFILES["full"])
    )
    jumbo = estimate_transfer(
        make_inputs(distance_km=LONDON_SYDNEY_KM, loss_fraction=0.001, profile=PROFILES["jumbo"])
    )
    ratio = jumbo.throughput.per_flow_bps / full.throughput.per_flow_bps
    assert ratio == pytest.approx(8949 / 1448, rel=1e-3)


def test_offline_transfer_hint_triggers_past_a_week() -> None:
    short = estimate_transfer(make_inputs(data_bytes=2e12))
    long = estimate_transfer(make_inputs(data_bytes=2e15))
    assert not short.consider_offline_transfer
    assert long.consider_offline_transfer
    assert long.seconds > 7 * 86400


def test_line_rate_seconds_ignores_everything_but_the_link() -> None:
    est = estimate_transfer(make_inputs(data_bytes=1e9, link_bps=1e9))
    assert est.line_rate_seconds == pytest.approx(8.0)


# --- validation -------------------------------------------------------------------------


def test_inputs_reject_bad_streams_and_loss() -> None:
    with pytest.raises(ValueError, match="streams"):
        make_inputs(streams=0)
    with pytest.raises(ValueError, match="loss_fraction"):
        make_inputs(loss_fraction=1.0)
