"""Transfer-time model for bulk TCP data moves over a WAN.

The model is deliberately simple and every constant is named, documented and overridable.
It answers one question: *given this link, this distance and this loss, how long will a
bulk TCP transfer (such as an AWS DMS full load) take?*

Three ceilings are computed and the lowest wins:

1. **Line rate x profile efficiency** -- what the pipe can carry once Ethernet/IP/TCP
   headers are paid for.
2. **Per-flow window bound** -- ``window / RTT``. A single TCP connection can never move
   more than one receive window per round trip, whatever the link speed.
3. **Per-flow loss bound** -- the steady-state throughput TCP congestion control settles
   at under random loss. Modelled as the larger of Standard TCP (Mathis et al. 1997,
   ``1.22 * MSS / (RTT * sqrt(p))``) and CUBIC (RFC 8312 Eq. 7,
   ``1.054 * MSS * RTT^-0.25 * p^-0.75``), which is how CUBIC itself behaves: it falls
   back to the Reno rate in the TCP-friendly region.

Per-flow bounds are multiplied by the number of parallel streams, then capped by (1).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

# --- Physical constants ---------------------------------------------------------------

#: Signal speed in single-mode fibre (c / refractive index 1.468), km per millisecond.
FIBRE_KM_PER_MS = 204.0

#: Fibre routes are not great circles: cable landings, right-of-way, ring topologies.
#: 1.3-1.5x is the usual engineering range; 1.4 is the middle.
DEFAULT_ROUTE_FACTOR = 1.4

# --- Ethernet / IP / TCP framing ------------------------------------------------------

ETH_PREAMBLE_SFD_IFG = 20  # 7 preamble + 1 SFD + 12 inter-frame gap (never in the frame)
ETH_HEADER_FCS = 18  # 14 header + 4 FCS
IP_HEADER = 20
TCP_HEADER_WITH_OPTIONS = 32  # 20 base + 12 for timestamps/SACK, present on every segment


class PathType(StrEnum):
    """Which kind of WAN path carries the transfer; sets the loss and overhead defaults."""

    INTERNET = "internet"
    DIRECT_CONNECT = "dx"


@dataclass(frozen=True, slots=True)
class PathDefaults:
    """Per-path defaults, documented so they can be argued with."""

    loss_fraction: float
    fixed_overhead_ms: float
    rationale: str


PATH_DEFAULTS: dict[PathType, PathDefaults] = {
    # 0.1 % is a healthy long-haul Internet path; congested or poorly peered paths run
    # 0.5-2 %. AWS documents no figure (performance over the Internet is "best effort",
    # Hybrid Connectivity whitepaper), so this is an engineering default, not an SLA.
    PathType.INTERNET: PathDefaults(
        loss_fraction=0.001,
        fixed_overhead_ms=3.0,
        rationale="healthy long-haul Internet path; 0.5-2 % when congested",
    ),
    # Direct Connect is a dedicated circuit onto the AWS backbone; loss is normally
    # unmeasurable. 0.001 % keeps the loss model live so a long path still shows the
    # per-flow ceiling; pass --loss 0 to see the pure window bound.
    PathType.DIRECT_CONNECT: PathDefaults(
        loss_fraction=0.00001,
        fixed_overhead_ms=1.0,
        rationale="dedicated circuit onto the AWS backbone; AWS publishes no loss SLA",
    ),
}


# --- Packet profiles ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Profile:
    """A traffic mix expressed as (frame_size_bytes, weight) pairs.

    ``frame_size`` includes the Ethernet header and FCS but not preamble/IFG.
    """

    name: str
    description: str
    frames: tuple[tuple[int, int], ...]

    @property
    def payload_bytes_per_frame(self) -> float:
        """Average TCP payload carried per frame."""
        total_weight = sum(w for _, w in self.frames)
        payload = 0.0
        for size, weight in self.frames:
            payload += max(0, size - ETH_HEADER_FCS - IP_HEADER - TCP_HEADER_WITH_OPTIONS) * weight
        return payload / total_weight

    @property
    def wire_bytes_per_frame(self) -> float:
        """Average bytes occupied on the wire per frame, including preamble and IFG."""
        total_weight = sum(w for _, w in self.frames)
        return sum((size + ETH_PREAMBLE_SFD_IFG) * w for size, w in self.frames) / total_weight

    @property
    def efficiency(self) -> float:
        """Fraction of line rate that is TCP payload (goodput / line rate)."""
        return self.payload_bytes_per_frame / self.wire_bytes_per_frame

    @property
    def mss_bytes(self) -> int:
        """Largest TCP payload in the mix -- what congestion control grows the window in."""
        largest = max(size for size, _ in self.frames)
        return largest - ETH_HEADER_FCS - IP_HEADER - TCP_HEADER_WITH_OPTIONS


PROFILES: dict[str, Profile] = {
    "full": Profile(
        name="full",
        description="1500-byte MTU, every frame full (bulk TCP transfer, e.g. DMS full load)",
        frames=((1518, 1),),
    ),
    "jumbo": Profile(
        name="jumbo",
        description="9001-byte MTU jumbo frames (Direct Connect private/transit VIF)",
        frames=((9019, 1),),
    ),
    "imix": Profile(
        name="imix",
        # Simple IMIX: 7 x 64, 4 x 570, 1 x 1518. A router benchmark mix, included for
        # the pessimistic case of sharing the link with interactive/transactional traffic.
        # It is NOT what a bulk load looks like on the wire.
        description="simple IMIX 7:4:1 of 64/570/1518-byte frames (shared or chatty link)",
        frames=((64, 7), (570, 4), (1518, 1)),
    ),
}

#: Linux default tcp_rmem[2] (the receive-side autotuning ceiling) is 6 MiB; the DMS
#: replication instance and most untuned source hosts sit here. 16-64 MiB is typical
#: after tuning.
DEFAULT_TCP_WINDOW_BYTES = 6 * 1024 * 1024

#: AWS DMS runs up to ``MaxFullLoadSubTasks`` (default 8) table loads in parallel; each is
#: its own connection to the source.
DEFAULT_STREAMS = 8

#: AWS's long-standing rule of thumb: if the network transfer takes more than about a
#: week, an offline transfer (Snowball) is worth pricing.
OFFLINE_TRANSFER_HINT_SECONDS = 7 * 86400


# --- Inputs / outputs -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TransferInputs:
    """Everything the model needs. Construct via the CLI or directly in tests."""

    data_bytes: float
    distance_km: float
    link_bps: float
    profile: Profile
    loss_fraction: float
    path: PathType
    streams: int = DEFAULT_STREAMS
    tcp_window_bytes: int = DEFAULT_TCP_WINDOW_BYTES
    route_factor: float = DEFAULT_ROUTE_FACTOR
    rtt_ms_override: float | None = None

    def __post_init__(self) -> None:
        if self.streams < 1:
            msg = "streams must be >= 1"
            raise ValueError(msg)
        if not 0 <= self.loss_fraction < 1:
            msg = "loss_fraction must be in [0, 1)"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class LatencyEstimate:
    """How the RTT was arrived at."""

    great_circle_km: float
    fibre_km: float
    propagation_one_way_ms: float
    fixed_overhead_ms: float
    rtt_ms: float
    source: str  # "estimated" or "measured (--rtt)"


@dataclass(frozen=True, slots=True)
class ThroughputEstimate:
    """The ceilings and which one binds."""

    line_rate_bps: float
    link_goodput_bps: float
    per_flow_window_bound_bps: float
    per_flow_reno_bound_bps: float
    per_flow_cubic_bound_bps: float
    per_flow_bps: float
    streams: int
    aggregate_bps: float
    bottleneck: str


@dataclass(frozen=True, slots=True)
class TransferEstimate:
    """Full result."""

    inputs: TransferInputs
    latency: LatencyEstimate
    throughput: ThroughputEstimate
    bytes_including_retransmits: float
    line_rate_seconds: float
    seconds: float

    @property
    def consider_offline_transfer(self) -> bool:
        """True when the transfer is long enough that Snowball is worth pricing."""
        return self.seconds > OFFLINE_TRANSFER_HINT_SECONDS

    @property
    def tcp_penalty_factor(self) -> float:
        """How many times slower than naive line rate the modelled transfer is."""
        return self.seconds / self.line_rate_seconds if self.line_rate_seconds else math.inf


# --- Model ----------------------------------------------------------------------------


def estimate_latency(inputs: TransferInputs) -> LatencyEstimate:
    """Estimate RTT from great-circle distance, or carry through a measured value."""
    fibre_km = inputs.distance_km * inputs.route_factor
    one_way_ms = fibre_km / FIBRE_KM_PER_MS
    overhead_ms = PATH_DEFAULTS[inputs.path].fixed_overhead_ms
    if inputs.rtt_ms_override is not None:
        rtt_ms = inputs.rtt_ms_override
        source = "measured (--rtt)"
    else:
        rtt_ms = 2 * one_way_ms + overhead_ms  # overhead > 0, so same-metro RTT stays > 0
        source = "estimated"
    return LatencyEstimate(
        great_circle_km=inputs.distance_km,
        fibre_km=fibre_km,
        propagation_one_way_ms=one_way_ms,
        fixed_overhead_ms=overhead_ms,
        rtt_ms=rtt_ms,
        source=source,
    )


def reno_bound_bps(mss_bytes: int, rtt_s: float, loss: float) -> float:
    """Mathis et al. (1997) steady-state throughput for Standard TCP."""
    if loss <= 0:
        return math.inf
    return 1.22 * mss_bytes * 8 / (rtt_s * math.sqrt(loss))


def cubic_bound_bps(mss_bytes: int, rtt_s: float, loss: float) -> float:
    """RFC 8312 Eq. 7 average window, converted to bits per second."""
    if loss <= 0:
        return math.inf
    avg_window_segments = 1.054 * (rtt_s**0.75) / (loss**0.75)
    return avg_window_segments * mss_bytes * 8 / rtt_s


def estimate_throughput(inputs: TransferInputs, latency: LatencyEstimate) -> ThroughputEstimate:
    """Compute every ceiling and pick the binding one."""
    rtt_s = latency.rtt_ms / 1000.0
    profile = inputs.profile
    link_goodput = inputs.link_bps * profile.efficiency
    window_bound = inputs.tcp_window_bytes * 8 / rtt_s
    reno = reno_bound_bps(profile.mss_bytes, rtt_s, inputs.loss_fraction)
    cubic = cubic_bound_bps(profile.mss_bytes, rtt_s, inputs.loss_fraction)
    loss_bound = max(reno, cubic)
    per_flow = min(window_bound, loss_bound, link_goodput)
    aggregate_flows = per_flow * inputs.streams
    aggregate = min(aggregate_flows, link_goodput)

    if aggregate >= link_goodput:
        bottleneck = "link (line rate x profile efficiency)"
    elif window_bound <= loss_bound:
        bottleneck = f"TCP receive window ({inputs.streams} stream(s) x window/RTT)"
    else:
        bottleneck = f"TCP loss recovery ({inputs.streams} stream(s), CUBIC/Reno under loss)"

    return ThroughputEstimate(
        line_rate_bps=inputs.link_bps,
        link_goodput_bps=link_goodput,
        per_flow_window_bound_bps=window_bound,
        per_flow_reno_bound_bps=reno,
        per_flow_cubic_bound_bps=cubic,
        per_flow_bps=per_flow,
        streams=inputs.streams,
        aggregate_bps=aggregate,
        bottleneck=bottleneck,
    )


def estimate_transfer(inputs: TransferInputs) -> TransferEstimate:
    """Run the whole model."""
    latency = estimate_latency(inputs)
    throughput = estimate_throughput(inputs, latency)
    # Lost segments are resent; the bytes that cross the wire grow by 1/(1-p).
    bytes_on_wire = inputs.data_bytes / (1 - inputs.loss_fraction)
    line_rate_seconds = inputs.data_bytes * 8 / inputs.link_bps
    seconds = bytes_on_wire * 8 / throughput.aggregate_bps if throughput.aggregate_bps else math.inf
    return TransferEstimate(
        inputs=inputs,
        latency=latency,
        throughput=throughput,
        bytes_including_retransmits=bytes_on_wire,
        line_rate_seconds=line_rate_seconds,
        seconds=seconds,
    )
