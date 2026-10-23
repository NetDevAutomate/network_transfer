"""Parse pasted ``ping`` and ``mtr`` output into a measured RTT and loss.

The distance model is a ~20 % estimate; a real measurement on the real path beats it.
Rather than retype numbers into ``--rtt`` and ``--loss``, feed the tool's output straight
in with ``--measure FILE`` (or ``-`` for stdin). Accepted:

- ``ping`` summaries from macOS/BSD (``round-trip min/avg/max/stddev = ...``) and Linux
  (``rtt min/avg/max/mdev = ...``), with the ``N packets transmitted, M received, X% packet
  loss`` line.
- ``mtr`` report mode (``mtr -r``, ``-rw``, ``-rwc N``, ``-rz``): the *last responding hop*
  supplies RTT (``Avg``) and loss. Loss on intermediate hops is ignored on purpose --
  routers rate-limit ICMP TTL-exceeded replies, so mid-path loss is usually an artefact,
  while loss at the final hop is end-to-end.

Loss resolution is ``1 / packets``: 0 % over 10 packets only proves loss is under 10 %.
The CLI says so when the sample is small.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Below this many probes a 0 % loss reading is not evidence of a clean path.
SMALL_SAMPLE_PACKETS = 100

_PING_RTT_RE = re.compile(
    r"(?:round-trip|rtt)\s+min/avg/max(?:/(?:stddev|mdev))?\s*=\s*"
    r"(?P<min>[\d.]+)/(?P<avg>[\d.]+)/(?P<max>[\d.]+)(?:/(?P<dev>[\d.]+))?\s*ms",
    re.I,
)
_PING_LOSS_RE = re.compile(
    r"(?P<sent>\d+)\s+packets?\s+transmitted,\s+(?P<recv>\d+)\s+(?:packets\s+)?received"
    r".*?(?P<loss>[\d.]+)%\s+packet\s+loss",
    re.I | re.S,
)
# mtr report row:  "  7.|-- host-or-ip   0.0%    10   12.1  12.4  11.9  13.0   0.3"
# With -z the "|--" becomes an "AS12345" column; unresponsive "???" rows print "100.0" with
# no percent sign. We anchor on the hop index at the start and seven numeric columns at the end.
_MTR_ROW_RE = re.compile(
    r"^\s*(?P<hop>\d+)\.\s*(?:\|--\s+)?(?P<host>.+?)\s+"
    r"(?P<loss>[\d.]+)%?\s+(?P<sent>\d+)\s+"
    r"(?P<last>[\d.]+)\s+(?P<avg>[\d.]+)\s+(?P<best>[\d.]+)\s+(?P<worst>[\d.]+)\s+(?P<stdev>[\d.]+)\s*$"
)


@dataclass(frozen=True, slots=True)
class Measurement:
    """What was read from the pasted output."""

    tool: str  # "ping" or "mtr"
    rtt_ms: float
    loss_fraction: float
    packets: int
    target: str
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def label(self) -> str:
        """Short provenance tag for reports, e.g. ``ping, 20 pkts``."""
        return f"{self.tool}, {self.packets} pkts"


def parse_measurement(text: str) -> Measurement:
    """Return a :class:`Measurement` from ``ping`` or ``mtr`` output, or raise ValueError."""
    mtr_rows = [m for line in text.splitlines() if (m := _MTR_ROW_RE.match(line))]
    if mtr_rows:
        return _from_mtr(mtr_rows)
    rtt = _PING_RTT_RE.search(text)
    loss = _PING_LOSS_RE.search(text)
    if rtt and loss:
        return _from_ping(rtt, loss, text)
    if rtt or loss:
        missing = "the 'packets transmitted ... packet loss' line" if rtt else "the rtt summary"
        msg = f"ping output is missing {missing}; paste the whole summary"
        raise ValueError(msg)
    msg = (
        "Could not recognise ping or mtr output. Expected a ping summary"
        " ('N packets transmitted ... round-trip min/avg/max') or an mtr report"
        " ('mtr -rwc 100 <host>')."
    )
    raise ValueError(msg)


def _from_ping(rtt: re.Match[str], loss: re.Match[str], text: str) -> Measurement:
    sent = int(loss.group("sent"))
    recv = int(loss.group("recv"))
    if sent <= 0:
        msg = "ping reports zero packets transmitted"
        raise ValueError(msg)
    # Prefer the counts over the printed percentage: the latter is rounded.
    loss_fraction = (sent - recv) / sent
    target_match = re.search(r"---\s+(\S+)\s+ping statistics", text, re.I) or re.search(
        r"^PING\s+(\S+)", text, re.I | re.M
    )
    target = target_match.group(1) if target_match else "target"
    notes = _sample_notes(sent, loss_fraction)
    return Measurement(
        tool="ping",
        rtt_ms=float(rtt.group("avg")),
        loss_fraction=loss_fraction,
        packets=sent,
        target=target,
        notes=notes,
    )


def _from_mtr(rows: list[re.Match[str]]) -> Measurement:
    responding = [r for r in rows if r.group("host").split()[-1] != "???"]
    if not responding:
        msg = "mtr report has no responding hops (every hop is ???)"
        raise ValueError(msg)
    final = rows[-1]
    used = responding[-1]
    notes: list[str] = []
    if used is not final:
        notes.append(
            f"final hop {final.group('hop')} did not respond (ICMP filtered at the"
            f" destination?); using hop {used.group('hop')} {used.group('host').strip()}"
            " -- RTT and loss are to that hop, not end-to-end"
        )
    if len(rows) > 1:
        # ??? hops are filtered routers, not loss; count only responding mid-path hops.
        mid_lossy = [r for r in responding[:-1] if float(r.group("loss")) > 0]
        if mid_lossy:
            notes.append(
                f"{len(mid_lossy)} intermediate hop(s) show loss; ignored (routers rate-limit"
                " ICMP replies, so mid-path loss is rarely end-to-end loss)"
            )
    sent = int(used.group("sent"))
    loss_fraction = float(used.group("loss")) / 100.0
    notes.extend(_sample_notes(sent, loss_fraction))
    return Measurement(
        tool="mtr",
        rtt_ms=float(used.group("avg")),
        loss_fraction=loss_fraction,
        packets=sent,
        target=used.group("host").strip().split()[-1],
        notes=tuple(notes),
    )


def _sample_notes(sent: int, loss_fraction: float) -> tuple[str, ...]:
    if loss_fraction == 0 and sent < SMALL_SAMPLE_PACKETS:
        return (
            f"0 % loss over {sent} probes only proves loss < {1 / sent:.0%}; the model uses 0."
            f" Re-measure with at least {SMALL_SAMPLE_PACKETS} (mtr -rwc {SMALL_SAMPLE_PACKETS}"
            f" / ping -c {SMALL_SAMPLE_PACKETS}) before trusting it.",
        )
    return ()
