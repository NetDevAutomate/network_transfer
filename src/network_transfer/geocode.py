"""Look up a city's coordinates online (OpenStreetMap Nominatim).

Only used by ``--add-city``; the estimator itself never touches the network. The HTTP
fetch is injectable so tests run offline.

Nominatim usage policy (https://operations.osmfoundation.org/policies/nominatim/): send an
identifying User-Agent, at most one request per second, no bulk geocoding. A CLI adding one
city at a time is well within that.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from network_transfer import __version__

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = f"nettransfer/{__version__} (WAN transfer-time estimator CLI)"
TIMEOUT_SECONDS = 10

Fetcher = Callable[[str], bytes]


@dataclass(frozen=True, slots=True)
class GeocodeResult:
    """One geocoder hit."""

    name: str  # short name, e.g. "Leeds"
    display_name: str  # full hierarchy, e.g. "Leeds, West Yorkshire, England, United Kingdom"
    lat: float
    lon: float


def default_fetch(url: str) -> bytes:
    """GET ``url`` with an identifying User-Agent; network failures become ValueError."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.read()
    except urllib.error.URLError as exc:
        msg = f"Could not reach the geocoder ({exc.reason}). Check network access and retry."
        raise ValueError(msg) from exc
    except TimeoutError as exc:
        msg = f"Geocoder timed out after {TIMEOUT_SECONDS}s."
        raise ValueError(msg) from exc


def geocode(query: str, fetch: Fetcher = default_fetch) -> GeocodeResult:
    """Resolve ``query`` (e.g. ``"Leeds"`` or ``"Portland, Maine"``) to coordinates.

    Returns the geocoder's top-ranked match. The caller should show ``display_name`` so a
    wrong match (Portland, Oregon vs Maine) is visible before it is saved.
    """
    cleaned = " ".join(query.split())
    if not cleaned:
        msg = "City name is empty"
        raise ValueError(msg)
    params = urllib.parse.urlencode({"q": cleaned, "format": "jsonv2", "limit": 1})
    raw = fetch(f"{NOMINATIM_URL}?{params}")
    try:
        hits: object = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = "Geocoder returned a response that is not JSON"
        raise ValueError(msg) from exc
    if not isinstance(hits, list) or not hits:
        msg = f"No match for {cleaned!r}. Try adding a country: {cleaned!r} -> '{cleaned}, UK'"
        raise ValueError(msg)
    hit: object = cast("list[object]", hits)[0]
    if not isinstance(hit, dict):
        msg = "Geocoder returned an unexpected record shape"
        raise ValueError(msg)
    record = cast("dict[str, object]", hit)
    try:
        name = str(record.get("name") or cleaned.split(",")[0])
        display_name = str(record["display_name"])
        lat = float(str(record["lat"]))
        lon = float(str(record["lon"]))
    except (KeyError, TypeError, ValueError) as exc:
        msg = "Geocoder record is missing lat/lon/display_name"
        raise ValueError(msg) from exc
    return GeocodeResult(name=name, display_name=display_name, lat=lat, lon=lon)
