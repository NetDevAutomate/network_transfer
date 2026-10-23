import json
import re
from pathlib import Path

import pytest

from network_transfer.cities import City, UserCities
from network_transfer.cli import add_cities, build_parser, main
from network_transfer.geocode import GeocodeResult


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    code = main(list(argv))
    return code, capsys.readouterr().out


def row(out: str, label: str, value: str) -> bool:
    """True if a report line reads ``<label>  <padding>  <value>...``."""
    return re.search(rf"^{re.escape(label)}\s+{re.escape(value)}", out, re.M) is not None


def test_basic_text_report(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "--size", "2T", "--from", "london", "--to", "dublin", "--link", "1G")
    assert code == 0
    assert row(out, "Source", "London")
    assert row(out, "Destination", "Dublin")
    assert row(out, "Link size", "1.0 Gbps")
    assert row(out, "Profile", "full --")
    assert row(out, "Packet loss", "0.1000%")
    assert row(out, "Data size", "2.00 TB (2.00 TB incl. retransmits)")
    assert "km great circle" in out
    assert "Full-load time" in out
    assert "network ceiling only" in out


def test_verbose_shows_ceilings(capsys: pytest.CaptureFixture[str]) -> None:
    _, out = run(capsys, "--size", "2T", "--from", "london", "--to", "dublin", "--link", "1G", "-v")
    assert "per-flow CUBIC bound" in out
    assert "one-way propagation" in out


def test_json_output(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(
        capsys, "--size", "2T", "--from", "london", "--to", "dublin", "--link", "1G", "--json"
    )
    assert code == 0
    data = json.loads(out)
    assert data["source"] == "London"
    assert data["destination"] == "Dublin"
    assert data["inputs"]["path"] == "internet"
    assert data["inputs"]["profile"]["name"] == "full"
    assert data["seconds"] > 0
    assert "transfer_time_human" in data


def test_compare_mode_text_and_json(capsys: pytest.CaptureFixture[str]) -> None:
    _, out = run(
        capsys, "--size", "2T", "--from", "london", "--to", "sydney", "--link", "1G", "--compare"
    )
    assert row(out, "Path", "internet")
    assert row(out, "Path", "dx")
    assert "=" * 78 in out

    _, out = run(
        capsys,
        "--size", "2T", "--from", "london", "--to", "sydney", "--link", "1G",
        "--compare", "--json",
    )  # fmt: skip
    data = json.loads(out)
    assert set(data) == {"internet", "dx"}
    assert data["dx"]["seconds"] < data["internet"]["seconds"]


def test_explicit_distance_without_cities(capsys: pytest.CaptureFixture[str]) -> None:
    _, out = run(capsys, "--size", "500G", "--distance", "450km", "--link", "500M")
    assert row(out, "Source", "A")
    assert row(out, "Destination", "B")
    assert "450 km great circle" in out


def test_dx_default_loss_and_jumbo_hint(capsys: pytest.CaptureFixture[str]) -> None:
    _, out = run(
        capsys,
        "--size",
        "2T",
        "--from",
        "london",
        "--to",
        "dublin",
        "--link",
        "10G",
        "--path",
        "dx",
    )
    assert "0.0010%" in out
    assert "--profile jumbo" in out


def test_imix_note_and_snowball_hint(capsys: pytest.CaptureFixture[str]) -> None:
    _, out = run(
        capsys,
        "--size", "2P", "--from", "london", "--to", "dublin", "--link", "1G", "--profile", "imix",
    )  # fmt: skip
    assert "IMIX models a link shared" in out
    assert "Snowball" in out


def test_window_and_loss_advice(capsys: pytest.CaptureFixture[str]) -> None:
    _, out = run(
        capsys,
        "--size", "2T", "--from", "london", "--to", "sydney", "--link", "1G",
        "--streams", "1", "--loss", "0",
    )  # fmt: skip
    assert "Bound by window/RTT" in out
    _, out = run(capsys, "--size", "2T", "--from", "london", "--to", "sydney", "--link", "1G")
    assert "Bound by loss recovery" in out


def test_measured_rtt_is_reported(capsys: pytest.CaptureFixture[str]) -> None:
    _, out = run(
        capsys, "--size", "2T", "--from", "london", "--to", "dublin", "--link", "1G", "--rtt", "12"
    )
    assert "12.0 ms (measured (--rtt))" in out


def test_list_flags(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "--list-cities")
    assert code == 0
    assert "london" in out.splitlines()
    code, out = run(capsys, "--list-profiles")
    assert code == 0
    assert out.startswith("full")
    assert "jumbo" in out


def test_missing_required_args_errors(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--from", "london", "--to", "dublin"])
    assert excinfo.value.code == 2
    assert "--size and --link are required" in capsys.readouterr().err


def test_missing_endpoints_errors(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--size", "2T", "--link", "1G", "--from", "london"])
    assert "give both --from and --to" in capsys.readouterr().err


def test_unknown_city_errors_with_suggestion(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--size", "2T", "--link", "1G", "--from", "Lodnon", "--to", "dublin"])
    err = capsys.readouterr().err
    assert "Unknown city 'Lodnon'" in err
    assert "london" in err


def test_bad_unit_errors(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--size", "2X", "--link", "1G", "--from", "london", "--to", "dublin"])
    assert "Unknown data-size unit" in capsys.readouterr().err


def test_parser_defaults() -> None:
    args = build_parser().parse_args(["--size", "1T", "--link", "1G"])
    assert args.path == "internet"
    assert args.profile == "full"
    assert args.streams == 8
    assert args.tcp_window == "6MiB"


# --- --add-city -------------------------------------------------------------------------

LEEDS = GeocodeResult(
    name="Leeds",
    display_name="Leeds, West Yorkshire, England, United Kingdom",
    lat=53.7974185,
    lon=-1.5437941,
)


def patch_geocoder(
    monkeypatch: pytest.MonkeyPatch, result: GeocodeResult | None, *, error: str | None = None
) -> list[str]:
    """Replace cli.geocode with a stub; returns the list of queries it received."""
    calls: list[str] = []

    def fake(query: str, fetch: object = None) -> GeocodeResult:
        calls.append(query)
        if error is not None:
            raise ValueError(error)
        assert result is not None
        return result

    monkeypatch.setattr("network_transfer.cli.geocode", fake)
    return calls


def test_add_city_then_use_it(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = patch_geocoder(monkeypatch, LEEDS)
    cities_file = tmp_path / "cities.json"

    code, out = run(capsys, "--cities-file", str(cities_file), "--add-city", "Leeds")
    assert code == 0
    assert "added 'Leeds' -> Leeds, West Yorkshire" in out
    assert f"saved to {cities_file}" in out
    assert calls == ["Leeds"]
    assert "leeds" in json.loads(cities_file.read_text())

    # The estimator run is offline: no further geocoder calls; the city resolves from file.
    code, out = run(
        capsys,
        "--cities-file", str(cities_file),
        "--size", "1T", "--from", "leeds", "--to", "eu-west-2", "--link", "1G",
    )  # fmt: skip
    assert code == 0
    assert row(out, "Source", "Leeds")
    assert row(out, "Destination", "London")
    assert calls == ["Leeds"]
    km = int(out.split("Approx. distance")[1].split("km")[0].strip().replace(",", ""))
    assert 260 < km < 285  # Leeds-London

    _, out = run(capsys, "--cities-file", str(cities_file), "--list-cities")
    assert "leeds (added)" in out.splitlines()


def test_add_city_override_is_flagged(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dorset = GeocodeResult("Portland", "Portland, Dorset, England", 50.55, -2.44)
    patch_geocoder(monkeypatch, dorset)
    _, out = run(
        capsys, "--cities-file", str(tmp_path / "c.json"), "--add-city", "Portland, Dorset"
    )
    assert "added 'Portland' -> Portland, Dorset, England" in out
    assert "[overrides built-in]" in out


def test_add_city_failure_is_a_usage_error(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_geocoder(monkeypatch, None, error="No match for 'Xyzzy'")
    with pytest.raises(SystemExit) as excinfo:
        main(["--cities-file", str(tmp_path / "c.json"), "--add-city", "Xyzzy"])
    assert excinfo.value.code == 2
    assert "No match for 'Xyzzy'" in capsys.readouterr().err


def test_add_cities_helper_with_explicit_fetcher(tmp_path: Path) -> None:
    raw = json.dumps(
        [{"name": "Leeds", "display_name": "Leeds, UK", "lat": "53.8", "lon": "-1.5"}]
    ).encode()
    store = UserCities(tmp_path / "c.json")
    lines = add_cities(["Leeds"], store, fetch=lambda _u: raw)
    assert lines[0].startswith("added 'Leeds' -> Leeds, UK")
    assert store.get("leeds") == City("Leeds", 53.8, -1.5)


def test_unknown_city_error_mentions_add_city(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--size", "2T", "--link", "1G", "--from", "Xyzzyville", "--to", "dublin"])
    assert "--add-city 'Xyzzyville'" in capsys.readouterr().err


# --- DMS realism flags ------------------------------------------------------------------

DMS_ARGS = (
    "--size", "2T", "--largest-table", "1.2T", "--change-rate", "40G/day",
    "--dms-instance", "dms.r5.large", "--from", "london", "--to", "eu-west-1",
    "--link", "10G", "--path", "dx",
)  # fmt: skip


def test_dms_realism_report(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, *DMS_ARGS)
    assert code == 0
    assert row(out, "DMS instance", "750.0 Mbps baseline")
    assert "bottleneck: DMS replication instance network baseline" in out
    assert row(out, "Largest-table tail", "1.09 TB on 1 stream")
    assert "CDC backlog" in out
    assert "CDC catch-up" in out
    assert "Total to cut-over" in out
    assert "Bound by the replication instance's network baseline" in out
    assert "single-stream tail on the largest table" in out


def test_dms_json_shape(capsys: pytest.CaptureFixture[str]) -> None:
    _, out = run(capsys, *DMS_ARGS, "--json")
    data = json.loads(out)
    assert data["inputs"]["instance_bps"] == pytest.approx(0.75e9)
    assert data["tail"]["tail_bytes"] > 0
    assert data["cdc"]["converges"] is True
    assert data["total_seconds"] > data["seconds"]
    assert "total_time_human" in data


def test_cdc_never_converges_warning(capsys: pytest.CaptureFixture[str]) -> None:
    _, out = run(
        capsys,
        "--size", "2T", "--from", "london", "--to", "dublin", "--link", "100M",
        "--change-rate", "2TB/day",  # ~185 Mbps of change over a 100M link
    )  # fmt: skip
    assert "NEVER -- change rate >= goodput" in out
    assert "CDC will never catch up" in out
    assert row(out, "Total to cut-over", "never")


def test_cdc_low_headroom_warning(capsys: pytest.CaptureFixture[str]) -> None:
    _, out = run(
        capsys,
        "--size", "2T", "--from", "london", "--to", "dublin", "--link", "1G",
        "--change-rate", "6TB/day", "--loss", "0",  # ~555 Mbps vs 941 Mbps goodput
    )  # fmt: skip
    assert "headroom" in out
    assert "catch-up is slow" in out


def test_instance_burst_note_for_short_loads(capsys: pytest.CaptureFixture[str]) -> None:
    _, out = run(
        capsys,
        "--size", "50G", "--from", "london", "--to", "dublin", "--link", "10G",
        "--dms-instance", "r5.large",
    )  # fmt: skip
    assert "run on burst credits" in out


def test_unknown_instance_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--size", "2T", "--link", "1G", "--distance", "100km", "--dms-instance", "m5.large"])
    assert excinfo.value.code == 2
    assert "Unknown DMS instance class" in capsys.readouterr().err


def test_bad_change_rate_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--size", "2T", "--link", "1G", "--distance", "100km", "--change-rate", "40G"])
    assert "SIZE/PERIOD" in capsys.readouterr().err


def test_list_instances(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "--list-instances")
    assert code == 0
    assert re.search(r"^dms\.r5\.large\s+baseline\s+750\.0 Mbps\s+burst 10\.0 Gbps", out, re.M)
    assert re.search(r"^dms\.r5\.8xlarge\s+baseline\s+10\.0 Gbps\s+no burst tier", out, re.M)


# --- --sweep ----------------------------------------------------------------------------


def test_sweep_loss_text_table(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(
        capsys,
        "--size", "2T", "--from", "london", "--to", "sydney", "--link", "1G",
        "--sweep", "loss=0.01,0.1,0.5,1",
    )  # fmt: skip
    assert code == 0
    lines = out.strip().splitlines()
    assert lines[0] == "London -> Sydney, 2T over 1G, sweeping loss"
    assert lines[2].split() == [
        "loss",
        "Goodput",
        "Bottleneck",
        "Full",
        "load",
        "Total",
        "to",
        "cut-over",
    ]
    body = lines[4:]
    assert [ln.split()[0] for ln in body] == ["0.01", "0.1", "0.5", "1"]
    assert all("TCP loss recovery" in ln for ln in body)
    assert all(ln.rstrip().endswith("--") for ln in body)  # no CDC -> no total


def test_sweep_streams_and_compare_adds_path_column(capsys: pytest.CaptureFixture[str]) -> None:
    _, out = run(
        capsys,
        "--size", "2T", "--from", "london", "--to", "sydney", "--link", "1G",
        "--sweep", "streams=1,8", "--compare", "--loss", "0",
    )  # fmt: skip
    lines = out.strip().splitlines()
    assert lines[2].split()[:2] == ["streams", "path"]
    body = lines[4:]
    assert len(body) == 4
    assert [ln.split()[:2] for ln in body] == [
        ["1", "internet"], ["1", "dx"], ["8", "internet"], ["8", "dx"],
    ]  # fmt: skip


def test_sweep_markdown_and_json(capsys: pytest.CaptureFixture[str]) -> None:
    base = (
        "--size", "2T", "--from", "london", "--to", "dublin", "--link", "1G",
        "--change-rate", "40G/day", "--sweep", "link=100M,1G,10G",
    )  # fmt: skip
    _, out = run(capsys, *base, "--format", "md")
    lines = out.strip().splitlines()
    assert lines[0].startswith("### London -> Dublin")
    assert lines[2] == "| link | Goodput | Bottleneck | Full load | Total to cut-over |"
    assert lines[3] == "|---|---|---|---|---|"
    assert lines[4].startswith("| 100M |")

    _, out = run(capsys, *base, "--json")
    data = json.loads(out)
    assert [d["sweep"]["link"] for d in data] == ["100M", "1G", "10G"]
    assert data[0]["seconds"] > data[2]["seconds"]


@pytest.mark.parametrize("sweep", ["loss", "loss=0.1", "colour=1,2", "rtt=10,abc"])
def test_sweep_rejects_bad_specs(capsys: pytest.CaptureFixture[str], sweep: str) -> None:
    with pytest.raises(SystemExit):
        main(["--size", "2T", "--link", "1G", "--distance", "100km", "--sweep", sweep])
    assert capsys.readouterr().err


def test_sweep_rtt_and_window_and_size(capsys: pytest.CaptureFixture[str]) -> None:
    _, out = run(
        capsys, "--size", "2T", "--link", "1G", "--distance", "100km",
        "--sweep", "rtt=10,100", "--json",
    )  # fmt: skip
    data = json.loads(out)
    assert data[0]["latency"]["rtt_ms"] == 10.0
    assert data[1]["latency"]["rtt_ms"] == 100.0
    _, out = run(
        capsys, "--size", "2T", "--link", "1G", "--distance", "100km",
        "--sweep", "tcp-window=1MiB,64MiB", "--json",
    )  # fmt: skip
    data = json.loads(out)
    assert data[0]["inputs"]["tcp_window_bytes"] == 2**20
    _, out = run(
        capsys, "--size", "2T", "--link", "1G", "--distance", "100km",
        "--sweep", "size=1T,4T", "--json",
    )  # fmt: skip
    data = json.loads(out)
    assert data[1]["seconds"] == pytest.approx(4 * data[0]["seconds"])


# --- --format md ------------------------------------------------------------------------


def test_markdown_report(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(
        capsys, "--size", "2T", "--from", "london", "--to", "dublin", "--link", "1G",
        "--format", "md",
    )  # fmt: skip
    assert code == 0
    lines = out.splitlines()
    assert lines[0] == "| Item | Value |"
    assert lines[1] == "|---|---|"
    assert "| Source | London |" in lines
    assert any(ln.startswith("| Full-load time |") for ln in lines)
    assert any(ln.startswith("- ! Bound by loss recovery") for ln in lines)
    assert "=" * 78 not in out


def test_markdown_compare_uses_rule_separator(capsys: pytest.CaptureFixture[str]) -> None:
    _, out = run(
        capsys, "--size", "2T", "--from", "london", "--to", "dublin", "--link", "1G",
        "--format", "md", "--compare", "-v",
    )  # fmt: skip
    assert "\n---\n" in out
    assert out.count("| Item | Value |") == 2
    assert "| per-flow CUBIC bound |" in out  # verbose rows are stripped of indent


def test_format_json_equals_json_flag(capsys: pytest.CaptureFixture[str]) -> None:
    args = ("--size", "2T", "--from", "london", "--to", "dublin", "--link", "1G")
    _, a = run(capsys, *args, "--format", "json")
    _, b = run(capsys, *args, "--json")
    assert json.loads(a) == json.loads(b)
