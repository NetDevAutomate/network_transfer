"""Command-line interface for ``nettransfer``."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any, TextIO

from network_transfer import __version__
from network_transfer.cities import (
    City,
    UserCities,
    all_city_names,
    default_store_path,
    haversine_km,
    lookup_city,
)
from network_transfer.dms import INSTANCES, lookup_instance
from network_transfer.geocode import Fetcher, geocode
from network_transfer.measure import Measurement, parse_measurement
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
    parse_data_rate,
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
  nettransfer --size 1T --from leeds --to eu-west-2 --link 1G

DMS realism:
  nettransfer --size 2T --largest-table 1.2T --change-rate 40G/day --dms-instance dms.r5.large \\
              --from london --to eu-west-1 --link 10G --path dx

sensitivity and reporting:
  nettransfer --size 2T --from london --to sydney --link 1G --sweep loss=0.01,0.1,0.5,1
  nettransfer --size 2T --from london --to dublin --link 1G --compare --format md

real paths and real measurements:
  nettransfer --size 5T --from manchester --via london --to eu-west-1 --link 10G --path dx
  mtr -rwc 100 10.0.0.5 > mtr.txt && nettransfer --size 2T --from leeds --to eu-west-2 \\
              --link 1G --measure mtr.txt
  ping -c 200 10.0.0.5 | nettransfer --size 2T --distance 300km --link 1G --measure -

Measure before you trust the estimate: `ping`/`mtr` for RTT and loss, `iperf3 -P 8` for
achievable throughput. Pass the real numbers back in with --rtt/--loss or --measure.
"""

SWEEP_PARAMS = ("loss", "link", "streams", "rtt", "tcp-window", "size")


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
        "--via",
        action="append",
        metavar="CITY",
        default=None,
        help=(
            "route through this city (repeatable, in order), e.g. the Direct Connect location:"
            " --from manchester --via london --to eu-west-1. Distance is the sum of the legs"
        ),
    )
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
        "--measure",
        metavar="FILE",
        help=(
            "read RTT and loss from pasted `ping` or `mtr -r` output (FILE, or - for stdin)."
            " mtr uses the last responding hop. Explicit --rtt/--loss still win"
        ),
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

    dms = parser.add_argument_group("DMS realism (all optional)")
    dms.add_argument(
        "--largest-table",
        metavar="SIZE",
        help=(
            "size of the biggest single table (e.g. 1.2T). DMS parallelism is per table, so"
            " its remainder loads on one stream after the others finish"
        ),
    )
    dms.add_argument(
        "--change-rate",
        metavar="SIZE/PERIOD",
        help=(
            "rate the source generates changes while the load runs, e.g. 40G/day or 2GB/h;"
            " adds CDC catch-up time and warns if it never converges"
        ),
    )
    dms.add_argument(
        "--dms-instance",
        metavar="CLASS",
        help=(
            "replication instance class, e.g. dms.r5.large; its EC2 network *baseline*"
            " becomes a ceiling (burst lasts minutes, loads last hours). See --list-instances"
        ),
    )
    parser.add_argument(
        "--list-instances",
        action="store_true",
        help="print known DMS instance classes with baseline/burst bandwidth and exit",
    )

    out = parser.add_argument_group("output")
    out.add_argument("--compare", action="store_true", help="show internet and dx side by side")
    out.add_argument(
        "--sweep",
        metavar="PARAM=V1,V2,...",
        help=(
            f"re-run across values of one parameter ({', '.join(SWEEP_PARAMS)}) and tabulate,"
            " e.g. loss=0.01,0.1,0.5,1 or streams=1,4,8,16"
        ),
    )
    out.add_argument(
        "--format",
        choices=("text", "md", "json"),
        default="text",
        help="text (default), md (Markdown tables, paste into a doc) or json",
    )
    out.add_argument(
        "--json", action="store_true", help="machine-readable output (same as --format json)"
    )
    out.add_argument("--verbose", "-v", action="store_true", help="show every ceiling computed")
    return parser


@dataclass(frozen=True, slots=True)
class Route:
    """Resolved endpoints: labels for the report and the summed great-circle distance."""

    source: str
    destination: str
    via: tuple[str, ...]
    km: float

    @property
    def label(self) -> str:
        """``London -> Dublin`` or ``Manchester -> London -> Dublin``."""
        return " -> ".join((self.source, *self.via, self.destination))


def _resolve_endpoints(args: argparse.Namespace, store: UserCities) -> Route:
    """Resolve ``--from/--via/--to`` (or ``--distance``) into a :class:`Route`."""
    via_names: list[str] = args.via or []
    if args.distance is not None:
        if via_names:
            msg = "--via cannot be combined with --distance; give the full distance instead"
            raise ValueError(msg)
        km = parse_distance_km(args.distance)
        return Route(args.src or "A", args.dst or "B", (), km)
    if not (args.src and args.dst):
        msg = "give both --from and --to, or --distance"
        raise ValueError(msg)
    points: list[City] = [lookup_city(name, store) for name in (args.src, *via_names, args.dst)]
    km = sum(haversine_km(a, b) for a, b in pairwise(points))
    return Route(points[0].name, points[-1].name, tuple(c.name for c in points[1:-1]), km)


def read_measurement(spec: str, stdin: TextIO | None = None) -> Measurement:
    """Load ``--measure FILE`` (``-`` = stdin) and parse it."""
    text = (stdin or sys.stdin).read() if spec == "-" else Path(spec).read_text(encoding="utf-8")
    return parse_measurement(text)


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


def _build_inputs(
    args: argparse.Namespace,
    path: PathType,
    distance_km: float,
    measured: Measurement | None = None,
) -> TransferInputs:
    if args.loss is not None:
        loss, loss_source = parse_percent(args.loss), "--loss"
    elif measured is not None:
        loss, loss_source = measured.loss_fraction, f"measured, {measured.label}"
    else:
        loss, loss_source = PATH_DEFAULTS[path].loss_fraction, "default"
    if args.rtt is not None:
        rtt, rtt_source = args.rtt, "--rtt"
    elif measured is not None:
        rtt, rtt_source = measured.rtt_ms, measured.label
    else:
        rtt, rtt_source = None, "--rtt"
    instance_bps = (
        lookup_instance(args.dms_instance).baseline_bps if args.dms_instance is not None else None
    )
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
        rtt_ms_override=rtt,
        rtt_source=rtt_source,
        loss_source=loss_source,
        instance_bps=instance_bps,
        largest_table_bytes=(
            parse_data_size(args.largest_table) if args.largest_table is not None else None
        ),
        change_bps_bytes=(
            parse_data_rate(args.change_rate) if args.change_rate is not None else None
        ),
    )


# --- sweep ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Sweep:
    """One parameter varied across several values."""

    param: str
    values: tuple[str, ...]


def parse_sweep(text: str) -> Sweep:
    """Parse ``loss=0.01,0.1,0.5`` into a :class:`Sweep`."""
    param, sep, raw_values = text.partition("=")
    param = param.strip().lower()
    values = tuple(v.strip() for v in raw_values.split(",") if v.strip())
    if not sep or param not in SWEEP_PARAMS or len(values) < 2:
        msg = (
            f"Cannot parse --sweep {text!r}: expected PARAM=V1,V2,... with PARAM one of"
            f" {', '.join(SWEEP_PARAMS)} and at least two values"
        )
        raise ValueError(msg)
    return Sweep(param, values)


def _with_sweep_value(args: argparse.Namespace, sweep: Sweep, value: str) -> argparse.Namespace:
    """Copy ``args`` with one parameter replaced; the parsers validate the value later."""
    clone = argparse.Namespace(**vars(args))
    attr = sweep.param.replace("-", "_")
    if sweep.param == "streams":
        setattr(clone, attr, int(value))
    elif sweep.param == "rtt":
        setattr(clone, attr, float(value))
    else:
        setattr(clone, attr, value)
    return clone


# --- rendering ------------------------------------------------------------------------

Rows = list[tuple[str, str]]


def _report_rows(
    route: Route, est: TransferEstimate, *, verbose: bool, measured: Measurement | None = None
) -> Rows:
    i, lat, tp = est.inputs, est.latency, est.throughput
    rows: Rows = [
        ("Source", route.source),
        ("Destination", route.destination),
    ]
    if route.via:
        rows.append(("Via", " -> ".join(route.via)))
    loss_source = "" if i.loss_source == "default" else f" ({i.loss_source})"
    rows += [
        (
            "Approx. distance",
            f"{lat.great_circle_km:,.0f} km great circle"
            + (" over " + str(len(route.via) + 1) + " legs" if route.via else "")
            + f" (~{lat.fibre_km:,.0f} km fibre @ x{i.route_factor})",
        ),
        ("Path", f"{i.path.value} -- {PATH_DEFAULTS[i.path].rationale}"),
        ("Link size", format_bps(i.link_bps)),
        (
            "Profile",
            f"{i.profile.name} -- {i.profile.description}"
            f" (efficiency {i.profile.efficiency:.1%}, MSS {i.profile.mss_bytes} B)",
        ),
        ("Packet loss", f"{i.loss_fraction:.4%}{loss_source}"),
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
    ]
    if tp.instance_bound_bps is not None and i.instance_bps is not None:
        rows.append(
            (
                "DMS instance",
                f"{format_bps(i.instance_bps)} baseline"
                f" ({format_bps(tp.instance_bound_bps)} goodput after framing)",
            )
        )
    rows.append(
        ("Goodput (modelled)", f"{format_bps(tp.aggregate_bps)} -- bottleneck: {tp.bottleneck}")
    )
    if est.tail is not None and est.tail.has_tail:
        rows.append(
            (
                "Largest-table tail",
                f"{format_bytes(est.tail.tail_bytes)} on 1 stream @"
                f" {format_bps(est.tail.tail_bps)} = {format_duration(est.tail.tail_seconds)}"
                f" (after {format_duration(est.tail.parallel_seconds)} parallel)",
            )
        )
    rows.append(
        (
            "Full-load time",
            f"{format_duration(est.seconds)}"
            f"  (line-rate ideal: {format_duration(est.line_rate_seconds)},"
            f" x{est.tcp_penalty_factor:.1f} slower)",
        )
    )
    if est.cdc is not None:
        c = est.cdc
        rows.append(
            (
                "CDC backlog",
                f"{format_bytes(c.backlog_bytes)} accrued during full load"
                f" @ {format_bytes(c.change_rate_bytes_per_s)}/s"
                f" ({format_bps(c.change_rate_bytes_per_s * 8)})",
            )
        )
        catch_up = (
            format_duration(c.catch_up_seconds)
            if c.converges
            else "NEVER -- change rate >= goodput"
        )
        rows.append(("CDC catch-up", f"{catch_up} (headroom {c.headroom_fraction:.0%})"))
        rows.append(("Total to cut-over", format_duration(est.total_seconds)))
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
    return rows


def render_text(
    route: Route, est: TransferEstimate, *, verbose: bool, measured: Measurement | None = None
) -> str:
    """Human-readable report."""
    rows = _report_rows(route, est, verbose=verbose, measured=measured)
    width = max(len(k) for k, _ in rows)
    lines = [f"{k:<{width}}  {v}" for k, v in rows]
    lines.append("")
    lines.extend(_advice(est, measured))
    return "\n".join(lines)


def render_markdown(
    route: Route, est: TransferEstimate, *, verbose: bool, measured: Measurement | None = None
) -> str:
    """Markdown two-column table plus the advice as a bullet list."""
    rows = _report_rows(route, est, verbose=verbose, measured=measured)
    lines = ["| Item | Value |", "|---|---|"]
    lines += [f"| {k.strip()} | {v.replace('|', '/')} |" for k, v in rows]
    lines.append("")
    lines += [f"- {note}" for note in _advice(est, measured)]
    return "\n".join(lines)


def _advice(est: TransferEstimate, measured: Measurement | None = None) -> list[str]:
    """Actionable notes derived from which ceiling binds."""
    notes: list[str] = []
    tp, i = est.throughput, est.inputs
    if measured is not None:
        notes.extend(f"~ {measured.tool}: {note}" for note in measured.notes)
    if est.cdc is not None and not est.cdc.converges:
        notes.append(
            "! CDC will never catch up: the source changes faster than this path can carry."
            " Reduce the change rate (quiet period, batch jobs off), raise goodput, or"
            " ship the initial load offline so CDC starts from a shorter backlog."
        )
    elif est.cdc is not None and est.cdc.headroom_fraction < 0.5:
        notes.append(
            f"! CDC has only {est.cdc.headroom_fraction:.0%} headroom: catch-up is slow and any"
            " loss or source burst extends it. Plan the cut-over window accordingly."
        )
    if est.consider_offline_transfer:
        notes.append(
            "! Transfer exceeds ~1 week: price AWS Snowball (or ship an initial load offline and"
            " use DMS CDC to catch up)."
        )
    if tp.bottleneck.startswith("DMS replication instance"):
        notes.append(
            "! Bound by the replication instance's network baseline, not the link: pick a"
            " larger class (8xlarge+ sizes have no burst tier) or split across instances."
        )
    elif i.instance_bps is not None and est.seconds < 3600:
        notes.append(
            "~ Load is under an hour: the instance may run on burst credits rather than its"
            " baseline, so this estimate is pessimistic."
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
    if est.tail is not None and est.tail.has_tail:
        share = est.tail.tail_seconds / est.seconds if est.seconds else 0.0
        notes.append(
            f"! {share:.0%} of the load is a single-stream tail on the largest table: use DMS"
            " parallel-load (partitions or range segmentation) on that table, or give it its"
            " own task."
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


def _to_json(
    route: Route, est: TransferEstimate, measured: Measurement | None = None
) -> dict[str, Any]:
    data = asdict(est)
    data["inputs"]["profile"] = {
        "name": est.inputs.profile.name,
        "efficiency": est.inputs.profile.efficiency,
        "mss_bytes": est.inputs.profile.mss_bytes,
    }
    data["inputs"]["path"] = est.inputs.path.value
    data["source"] = route.source
    data["destination"] = route.destination
    data["via"] = list(route.via)
    if measured is not None:
        data["measurement"] = asdict(measured)
    data["transfer_time_human"] = format_duration(est.seconds)
    data["total_seconds"] = est.total_seconds
    data["total_time_human"] = format_duration(est.total_seconds)
    data["consider_offline_transfer"] = est.consider_offline_transfer
    data["tcp_penalty_factor"] = est.tcp_penalty_factor
    return data


SWEEP_HEADERS = ("Goodput", "Bottleneck", "Full load", "Total to cut-over")


def _sweep_row(est: TransferEstimate) -> tuple[str, ...]:
    total = format_duration(est.total_seconds) if est.cdc is not None else "--"
    return (
        format_bps(est.throughput.aggregate_bps),
        est.throughput.bottleneck.split(" (")[0],
        format_duration(est.seconds),
        total,
    )


def render_sweep(
    sweep: Sweep, results: list[tuple[str, PathType, TransferEstimate]], *, markdown: bool
) -> str:
    """Tabulate one row per (sweep value, path)."""
    show_path = len({p for _, p, _ in results}) > 1
    headers = (sweep.param, *(("path",) if show_path else ()), *SWEEP_HEADERS)
    body = [
        (value, *((path.value,) if show_path else ()), *_sweep_row(est))
        for value, path, est in results
    ]
    if markdown:
        lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
        lines += ["| " + " | ".join(r) + " |" for r in body]
        return "\n".join(lines)
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *body, strict=True)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    lines = [fmt.format(*headers), fmt.format(*("-" * w for w in widths))]
    lines += [fmt.format(*r) for r in body]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    store = UserCities(args.cities_file)
    output_format = "json" if args.json else args.format

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
    if args.list_instances:
        for inst in INSTANCES.values():
            burst = f"burst {format_bps(inst.burst_bps)}" if inst.has_burst else "no burst tier"
            print(f"dms.{inst.name:<13} baseline {format_bps(inst.baseline_bps):>10}  {burst}")
        return 0
    if not (args.size and args.link):
        parser.error("--size and --link are required")

    try:
        route = _resolve_endpoints(args, store)
        measured = read_measurement(args.measure) if args.measure is not None else None
        paths = list(PathType) if args.compare else [PathType(args.path)]
        if args.sweep is not None:
            sweep = parse_sweep(args.sweep)
            swept = [
                (
                    value,
                    p,
                    estimate_transfer(
                        _build_inputs(_with_sweep_value(args, sweep, value), p, route.km, measured)
                    ),
                )
                for value in sweep.values
                for p in paths
            ]
            results = [(p, e) for _, p, e in swept]
        else:
            sweep = None
            swept = []
            results = [
                (p, estimate_transfer(_build_inputs(args, p, route.km, measured))) for p in paths
            ]
    except (ValueError, OSError) as exc:
        parser.error(str(exc))

    if output_format == "json":
        if sweep is not None:
            payload: Any = [
                {"sweep": {sweep.param: value}, **_to_json(route, e, measured)}
                for value, _, e in swept
            ]
        elif args.compare:
            payload = {p.value: _to_json(route, e, measured) for p, e in results}
        else:
            payload = _to_json(route, results[0][1], measured)
        print(json.dumps(payload, indent=2, default=str))
        return 0

    if sweep is not None:
        header = f"{route.label}, {args.size} over {args.link}, sweeping {sweep.param}"
        print(("### " if output_format == "md" else "") + header)
        print()
        print(render_sweep(sweep, swept, markdown=output_format == "md"))
        return 0

    render = render_markdown if output_format == "md" else render_text
    reports = [render(route, e, verbose=args.verbose, measured=measured) for _, e in results]
    separator = "\n\n---\n\n" if output_format == "md" else "\n" + "=" * 78 + "\n"
    print(separator.join(reports))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
