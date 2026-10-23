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
    assert "Transfer time" in out
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
