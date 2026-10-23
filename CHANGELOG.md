# Changelog

All notable changes to `nettransfer` are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `LICENSE` (MIT) and this changelog.
- README rewritten as a full reference: install from GitHub, output guide, CLI tables,
  measuring cookbook, JSON schema, development layout.

## [0.4.0] - 2026-10-23

### Added
- `--via CITY` (repeatable): route through intermediate cities such as the Direct Connect
  location; great-circle distance is the sum of the legs.
- `--measure FILE|-`: take RTT and loss from pasted `ping` (macOS/Linux) or `mtr -r` output.
  mtr uses the last responding hop and ignores mid-path ICMP loss; ping loss is derived from
  packet counts; small zero-loss samples are flagged (resolution `1/N`).
- Provenance on the loss and RTT rows and in JSON (`loss_source`, `rtt_source`,
  `measurement`).

## [0.3.0] - 2026-10-23

### Added
- `--dms-instance CLASS`: the replication instance's EC2 network *baseline* as a fourth
  ceiling, with figures from the EC2 Instance Types Guide for T3/C5/C6i/R5/R6i;
  `--list-instances`.
- `--largest-table SIZE`: two-phase model with a single-stream tail on the largest table
  once the other tables finish.
- `--change-rate SIZE/PERIOD`: CDC catch-up as a geometric series `rT/(g-r)`; reports
  `NEVER` and headroom when the change rate meets goodput.
- `--sweep PARAM=V1,V2,...` over `loss`, `link`, `streams`, `rtt`, `tcp-window`, `size`;
  combinable with `--compare`, `--format md` and `--json`.
- `--format text|md|json`; Markdown tables for pasting into documents.

### Changed
- Report row `Transfer time` renamed `Full-load time`; `Total to cut-over` added when a
  change rate is given.

## [0.2.0] - 2026-10-23

### Added
- `--add-city NAME` (repeatable): geocode via OpenStreetMap Nominatim, show the match, and
  persist to `~/.config/nettransfer/cities.json`; comma qualifiers (`"Portland, Dorset"`);
  user cities override built-ins. `--cities-file PATH`.
- Unknown-city errors now suggest the `--add-city` command.

## [0.1.0] - 2026-10-23

### Added
- Initial release: TCP-aware transfer-time model (link x profile efficiency, window/RTT,
  max(Reno, CUBIC) loss bound, parallel streams), Internet vs Direct Connect defaults,
  `full`/`jumbo`/`imix` profiles, offline city gazetteer with AWS region aliases,
  `--compare`, `--json`, `--verbose`, bottleneck-specific advice.

[Unreleased]: https://github.com/netdevautomate/network_transfer/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/netdevautomate/network_transfer/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/netdevautomate/network_transfer/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/netdevautomate/network_transfer/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/netdevautomate/network_transfer/releases/tag/v0.1.0
