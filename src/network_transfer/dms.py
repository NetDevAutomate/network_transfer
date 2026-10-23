"""AWS DMS replication-instance classes and their network allowances.

A DMS replication instance is an EC2 instance, and sizes of ``4xlarge`` and below have a
*baseline* network bandwidth well below their advertised "up to" burst. Burst is a
credit mechanism that lasts 5-60 minutes; a full load that runs for hours settles at the
baseline. Pointing a 10 Gbps Direct Connect at a ``dms.r5.large`` therefore buys
0.75 Gbps once the credits are spent.

Figures are from the *Amazon EC2 Instance Types Guide* network-specification tables
(memory-optimized, compute-optimized and general-purpose pages), read 2026-10-23. DMS
currently supports the T3, C5, C6i, R5 and R6i classes
(``CHAP_ReplicationInstance.Types``). Instances listed as a single figure have no burst
tier; baseline and burst are reported equal.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InstanceNetwork:
    """Baseline and burst network bandwidth for one instance size, in bits per second."""

    name: str
    baseline_bps: float
    burst_bps: float

    @property
    def has_burst(self) -> bool:
        """True when the size is an "up to" size whose burst exceeds its baseline."""
        return self.burst_bps > self.baseline_bps


def _g(name: str, baseline_gbps: float, burst_gbps: float | None = None) -> InstanceNetwork:
    burst = baseline_gbps if burst_gbps is None else burst_gbps
    return InstanceNetwork(name, baseline_gbps * 1e9, burst * 1e9)


_SIZES: tuple[InstanceNetwork, ...] = (
    # T3 (burstable): baseline / burst
    _g("t3.micro", 0.064, 5.0),
    _g("t3.small", 0.128, 5.0),
    _g("t3.medium", 0.256, 5.0),
    _g("t3.large", 0.512, 5.0),
    _g("t3.xlarge", 1.024, 5.0),
    _g("t3.2xlarge", 2.048, 5.0),
    # C5
    _g("c5.large", 0.75, 10.0),
    _g("c5.xlarge", 1.25, 10.0),
    _g("c5.2xlarge", 2.5, 10.0),
    _g("c5.4xlarge", 5.0, 10.0),
    _g("c5.9xlarge", 12.0),
    _g("c5.12xlarge", 12.0),
    _g("c5.18xlarge", 25.0),
    _g("c5.24xlarge", 25.0),
    # C6i
    _g("c6i.large", 0.781, 12.5),
    _g("c6i.xlarge", 1.562, 12.5),
    _g("c6i.2xlarge", 3.125, 12.5),
    _g("c6i.4xlarge", 6.25, 12.5),
    _g("c6i.8xlarge", 12.5),
    _g("c6i.12xlarge", 18.75),
    _g("c6i.16xlarge", 25.0),
    _g("c6i.24xlarge", 37.5),
    _g("c6i.32xlarge", 50.0),
    # R5
    _g("r5.large", 0.75, 10.0),
    _g("r5.xlarge", 1.25, 10.0),
    _g("r5.2xlarge", 2.5, 10.0),
    _g("r5.4xlarge", 5.0, 10.0),
    _g("r5.8xlarge", 10.0),
    _g("r5.12xlarge", 12.0),
    _g("r5.16xlarge", 20.0),
    _g("r5.24xlarge", 25.0),
    # R6i
    _g("r6i.large", 0.781, 12.5),
    _g("r6i.xlarge", 1.562, 12.5),
    _g("r6i.2xlarge", 3.125, 12.5),
    _g("r6i.4xlarge", 6.25, 12.5),
    _g("r6i.8xlarge", 12.5),
    _g("r6i.12xlarge", 18.75),
    _g("r6i.16xlarge", 25.0),
    _g("r6i.24xlarge", 37.5),
    _g("r6i.32xlarge", 50.0),
)

INSTANCES: dict[str, InstanceNetwork] = {s.name: s for s in _SIZES}


def lookup_instance(name: str) -> InstanceNetwork:
    """Resolve ``dms.r5.large``, ``r5.large`` or ``R5.LARGE`` to its network allowance."""
    key = name.strip().lower().removeprefix("dms.")
    try:
        return INSTANCES[key]
    except KeyError:
        families = sorted({s.name.split(".")[0] for s in _SIZES})
        msg = (
            f"Unknown DMS instance class {name!r}. Known families: {', '.join(families)}"
            " (e.g. dms.r5.large); see --list-instances"
        )
        raise ValueError(msg) from None
