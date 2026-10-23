import pytest

from network_transfer.dms import INSTANCES, lookup_instance


def test_r5_large_matches_ec2_instance_types_guide() -> None:
    inst = lookup_instance("dms.r5.large")
    assert inst.baseline_bps == pytest.approx(0.75e9)
    assert inst.burst_bps == pytest.approx(10e9)
    assert inst.has_burst


def test_lookup_is_case_insensitive_and_prefix_optional() -> None:
    assert lookup_instance("R6I.XLARGE") is INSTANCES["r6i.xlarge"]
    assert lookup_instance("  c6i.2xlarge ") is INSTANCES["c6i.2xlarge"]


def test_large_sizes_have_no_burst_tier() -> None:
    inst = lookup_instance("r5.8xlarge")
    assert inst.baseline_bps == inst.burst_bps == pytest.approx(10e9)
    assert not inst.has_burst


def test_every_dms_supported_family_is_present() -> None:
    families = {name.split(".")[0] for name in INSTANCES}
    assert families == {"t3", "c5", "c6i", "r5", "r6i"}


def test_baselines_never_exceed_burst() -> None:
    assert all(i.baseline_bps <= i.burst_bps for i in INSTANCES.values())


def test_unknown_instance_errors_helpfully() -> None:
    with pytest.raises(ValueError, match=r"Unknown DMS instance class 'dms\.m5\.large'"):
        lookup_instance("dms.m5.large")
