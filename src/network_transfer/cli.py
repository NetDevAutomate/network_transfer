"""Command-line interface for ``nettransfer``."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from network_transfer import __version__
from network_transfer.cities import (
    City,
    UserCities,
    all_city_names,
    default_store_path,
    haversine_km,
    lookup_city,
)
from network_transfer.geocode import Fetcher, geocode
from network_transfer.model import (
    DEFAULT_ROUTE_FACTOR,
    DEFAULT_STREAMS,
    DEFAULT_TCP_WINDOW_BYTES,
    PATH_DEFAULTS,
    PROFILES,
    PathType,
    TransferEstimate,
    TransferInputs,
    estimate_transfer,
)
from network_transfer.units import (
    format_bps,
    format_bytes,
    format_duration,
    parse_data_size,
    parse_distance_km,
    parse_link_rate,
    parse_percent,
)

EPILOG = """\
examples:
  nettransfer --size 2T --from london --to dublin --link 1G
  nettransfer --size 50T --from manchester --to eu-west-1 --link 10G --path dx --profile jumbo
  nettransfer --size 500G --distance 450km --link 500M --loss 0.5 --streams 1
  nettransfer --size 2T --from london --to sydney --link 1G --compare
  nettransfer --size 2T --from london --to dublin --link 1G --rtt 12   # use a measured RTT
  nettransfer --add-city "Leeds" --add-city "Portland, Maine"          # fetch from OpenStreetMap
  nettransfer --size 2T --from leeds --to eu-west-2 --link 1G

Measure before you trust the estimate: `ping`/`mtr` for RTT and loss, `iperf3 -P 8` for
achievable throughput. Pass the real numbers back in with --rtt and --loss.
"""


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser (kept separate so tests and docs can introspect it)."""
    parser = argparse.ArgumentParser(
        prog="nettransfer",
        description=(
            "Estimate how long a bulk TCP transfer (e.g. an AWS DMS full load) will take "
            "over a WAN, from data size, distance, link size, packet profile and loss."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--list-cities", action="store_true", help="print known cities and exit")
    parser.add_argument(
        "--list-profiles", action="store_true", help="print packet profiles and exit"
    )
    parser.add_argument(
        "--add-city",
        action="append",
        metavar="NAME",
        help=(
            "look NAME up on OpenStreetMap (e.g. 'Leeds' or 'Portland, Maine'), save it for"
            " future runs, and exit; repeatable. The only flag that uses the network."
        ),
    )
    parser.add_argument(
        "--cities-file",
        type=Path,
        default=None,
        help=f"where added cities are stored (default {default_store_path()})",
    )

    required = parser.add_argument_group("required")
    required.add_argument("--size", "-s", help="data size: 500G, 2T, 1.5P (or GiB/TiB)")
    required.add_argument("--link", "-l", help="link size in bits/s: 100M, 1G, 10G")

    where = parser.add_argument_group("distance (city pair or explicit distance)")
    where.add_argument("--from", dest="src", metavar="CITY", help="source city or AWS region")
    where.add_argument("--to", dest="dst", metavar="CITY", help="destination city or AWS region")
    where.add_argument(
        "--distance", "-d", help="explicit great-circle distance (450km, 280mi) instead of cities"
    )

    net = parser.add_argument_group("network")
    net.add_argument(
        "--path",
        choices=[p.value for p in PathType],
        default=PathType.INTERNET.value,
        help="internet (default) or dx (AWS Direct Connect); sets loss/overhead defaults",
    )
    net.add_argument(
        "--profile",
        "-p",
        choices=sorted(PROFILES),
        default="full",
        help="packet profile (default: full = 1500-byte frames)",
    )
    net.add_argument(
        "--loss",
        help=(
            "packet loss %% (default: "
            f"{PATH_DEFAULTS[PathType.INTERNET].loss_fraction * 100:g}%% internet, "
            f"{PATH_DEFAULTS[PathType.DIRECT_CONNECT].loss_fraction * 100:g}%% dx)"
        ),
    )
    net.add_argument(
        "--rtt",
        type=float,
        metavar="MS",
        help="measured round-trip time in ms (overrides estimate)",
    )
    net.add_argument(
        "--streams",
        type=int,
        default=DEFAULT_STREAMS,
        help=f"parallel TCP connections (default {DEFAULT_STREAMS} = DMS MaxFullLoadSubTasks)",
    )
    net.add_argument(
        "--tcp-window",
        default=str(DEFAULT_TCP_WINDOW_BYTES // (1024 * 1024)) + "MiB",
        help="per-connection TCP receive window (default 6MiB = Linux tcp_rmem max)",
    )
    net.add_argument(
        "--route-factor",
        type=float,
        default=DEFAULT_ROUTE_FACTOR,
        help=f"fibre path / great circle ratio (default {DEFAULT_ROUTE_FACTOR})",
    )

    out = parser.add_argument_group("output")
    out.add_argument("--compare", action="store_true", help="show internet and dx side by side")
    out.add_argument("--json", action="store_true", help="machine-readable output")
    out.add_argument("--verbose", "-v", action="store_true", help="show every ceiling computed")
    return parser


def _resolve_endpoints(args: argparse.Namespace, store: UserCities) -> tuple[str, str, float]:
    """Return (source label, destination label, great-circle km)."""
    if args.distance is not None:
        km = parse_distance_km(args.distance)
        src = args.src or "A"
        dst = args.dst or "B"
        return src, dst, km
    if not (args.src and args.dst):
        msg = "give both --from and --to, or --distance"
        raise ValueError(msg)
    a: City = lookup_city(args.src, store)
    b: City = lookup_city(args.dst, store)
    return a.name, b.name, haversine_km(a, b)


def add_cities(names: list[str], store: UserCities, fetch: Fetcher | None = None) -> list[str]:
    """Geocode each name, persist it, and return one report line per city."""
    lines: list[str] = []
    for raw in names:
        result = geocode(raw, fetch) if fetch is not None else geocode(raw)
        city = City(name=result.name, lat=result.lat, lon=result.lon)
        # "Portland, Dorset" refines the search; the stored key is what you type later.
        key = raw.split(",")[0].strip()
        replaced_builtin = store.add(key, city, source=f"nominatim: {result.display_name}")
        lines.append(
            f"added {key!r} -> {result.display_name} ({result.lat:.4f}, {result.lon:.4f})"
            + ("  [overrides built-in]" if replaced_builtin else "")
        )
    lines.append(f"saved to {store.path}")
    return lines


def _build_inputs(args: argparse.Namespace, path: PathType, distance_km: float) -> TransferInputs:
    loss = parse_percent(args.loss) if args.loss is not None else PATH_DEFAULTS[path].loss_fraction
    return TransferInputs(
        data_bytes=parse_data_size(args.size),
        distance_km=distance_km,
        link_bps=parse_link_rate(args.link),
        profile=PROFILES[args.profile],
        loss_fraction=loss,
        path=path,
        streams=args.streams,
        tcp_window_bytes=int(parse_data_size(args.tcp_window)),
        route_factor=args.route_factor,
        rtt_ms_override=args.rtt,
    )


def render_text(src: str, dst: str, est: TransferEstimate, *, verbose: bool) -> str:
    """Human-readable report."""
    i, lat, tp = est.inputs, est.latency, est.throughput
    rows: list[tuple[str, str]] = [
        ("Source", src),
        ("Destination", dst),
        (
            "Approx. distance",
            f"{lat.great_circle_km:,.0f} km great circle"
            f" (~{lat.fibre_km:,.0f} km fibre @ x{i.route_factor})",
        ),
        ("Path", f"{i.path.value} -- {PATH_DEFAULTS[i.path].rationale}"),
        ("Link size", format_bps(i.link_bps)),
        (
            "Profile",
            f"{i.profile.name} -- {i.profile.description}"
            f" (efficiency {i.profile.efficiency:.1%}, MSS {i.profile.mss_bytes} B)",
        ),
        ("Packet loss", f"{i.loss_fraction:.4%}"),
        ("Approx. RTT", f"{lat.rtt_ms:.1f} ms ({lat.source})"),
        (
            "Data size",
            format_bytes(i.data_bytes)
            + (
                f" ({format_bytes(est.bytes_including_retransmits)} incl. retransmits)"
                if i.loss_fraction > 0
                else ""
            ),
        ),
        ("Parallel streams", str(tp.streams)),
        ("Goodput (modelled)", f"{format_bps(tp.aggregate_bps)} -- bottleneck: {tp.bottleneck}"),
        (
            "Transfer time",
            f"{format_duration(est.seconds)}"
            f"  (line-rate ideal: {format_duration(est.line_rate_seconds)},"
            f" x{est.tcp_penalty_factor:.1f} slower)",
        ),
    ]
    if verbose:
        rows += [
            ("  link goodput", format_bps(tp.link_goodput_bps)),
            (
                "  per-flow window bound",
                f"{format_bps(tp.per_flow_window_bound_bps)}"
                f" ({format_bytes(i.tcp_window_bytes)} / {lat.rtt_ms:.1f} ms)",
            ),
            ("  per-flow Reno bound", format_bps(tp.per_flow_reno_bound_bps)),
            ("  per-flow CUBIC bound", format_bps(tp.per_flow_cubic_bound_bps)),
            ("  per-flow (binding)", format_bps(tp.per_flow_bps)),
            ("  one-way propagation", f"{lat.propagation_one_way_ms:.2f} ms"),
            ("  fixed overhead", f"{lat.fixed_overhead_ms:.1f} ms"),
        ]
    width = max(len(k) for k, _ in rows)
    lines = [f"{k:<{width}}  {v}" for k, v in rows]
    lines.append("")
    lines.extend(_advice(est))
    return "\n".join(lines)


def _advice(est: TransferEstimate) -> list[str]:
    """Actionable notes derived from which ceiling binds."""
    notes: list[str] = []
    tp, i = est.throughput, est.inputs
    if est.consider_offline_transfer:
        notes.append(
            "! Transfer exceeds ~1 week: price AWS Snowball (or ship an initial load offline and"
            " use DMS CDC to catch up)."
        )
    if tp.bottleneck.startswith("TCP receive window"):
        notes.append(
            "! Bound by window/RTT, not the link: raise net.ipv4.tcp_rmem/tcp_wmem max on both"
            " ends and/or add streams (DMS: MaxFullLoadSubTasks, or split tables across tasks)."
        )
    if tp.bottleneck.startswith("TCP loss"):
        notes.append(
            "! Bound by loss recovery: fix the loss before buying bandwidth -- on the Internet"
            " consider Direct Connect or an accelerated path; on DX check optics/CRC errors."
        )
    if i.profile.name == "imix":
        notes.append(
            "~ IMIX models a link shared with chatty traffic. A dedicated bulk load runs 'full';"
            " use IMIX only when the circuit carries production traffic too."
        )
    if i.path is PathType.DIRECT_CONNECT and i.profile.name == "full":
        notes.append(
            "~ On a DX private/transit VIF, enabling jumbo frames (--profile jumbo) lifts"
            " efficiency from ~94% to ~99% and grows the MSS 6x, which helps under loss."
        )
    notes.append(
        "~ This is the network ceiling only. DMS full loads are often bound by source read"
        " rate, LOB handling or target write rate -- validate with a pilot table."
    )
    return notes


def _to_json(src: str, dst: str, est: TransferEstimate) -> dict[str, Any]:
    data = asdict(est)
    data["inputs"]["profile"] = {
        "name": est.inputs.profile.name,
        "efficiency": est.inputs.profile.efficiency,
        "mss_bytes": est.inputs.profile.mss_bytes,
    }
    data["inputs"]["path"] = est.inputs.path.value
    data["source"] = src
    data["destination"] = dst
    data["transfer_time_human"] = format_duration(est.seconds)
    data["consider_offline_transfer"] = est.consider_offline_transfer
    data["tcp_penalty_factor"] = est.tcp_penalty_factor
    return data


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    store = UserCities(args.cities_file)

    if args.add_city:
        try:
            print("\n".join(add_cities(args.add_city, store)))
        except ValueError as exc:
            parser.error(str(exc))
        return 0
    if args.list_cities:
        print("\n".join(all_city_names(store)))
        return 0
    if args.list_profiles:
        for prof in PROFILES.values():
            print(
                f"{prof.name:<6} efficiency {prof.efficiency:6.1%}  MSS {prof.mss_bytes:>5} B  "
                f"{prof.description}"
            )
        return 0
    if not (args.size and args.link):
        parser.error("--size and --link are required")

    try:
        src, dst, km = _resolve_endpoints(args, store)
        paths = list(PathType) if args.compare else [PathType(args.path)]
        results = [(p, estimate_transfer(_build_inputs(args, p, km))) for p in paths]
    except ValueError as exc:
        parser.error(str(exc))

    if args.json:
        payload: Any = (
            {p.value: _to_json(src, dst, e) for p, e in results}
            if args.compare
            else _to_json(src, dst, results[0][1])
        )
        print(json.dumps(payload, indent=2, default=str))
        return 0

    reports = [render_text(src, dst, e, verbose=args.verbose) for _, e in results]
    print(("\n" + "=" * 78 + "\n").join(reports))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
