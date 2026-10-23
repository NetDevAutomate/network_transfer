"""Offline city gazetteer and great-circle distance.

A bundled table keeps the tool deterministic and usable without network access. The
coordinates are city centres to roughly two decimal places, which is far finer than the
model needs: a 10 km error on a 500 km path is a 0.1 ms error on the RTT.
"""

from __future__ import annotations

import difflib
import math
from dataclasses import dataclass

EARTH_RADIUS_KM = 6371.0088


@dataclass(frozen=True, slots=True)
class City:
    """A named point on the globe."""

    name: str
    lat: float
    lon: float


# name -> (lat, lon). Includes every city hosting a public AWS region as of 2026 plus
# common enterprise hubs. Keys are lowercase; see ALIASES for alternative spellings.
_CITIES: dict[str, tuple[float, float]] = {
    # UK & Ireland
    "london": (51.5074, -0.1278),
    "manchester": (53.4808, -2.2426),
    "birmingham": (52.4862, -1.8904),
    "edinburgh": (55.9533, -3.1883),
    "glasgow": (55.8642, -4.2518),
    "cardiff": (51.4816, -3.1791),
    "belfast": (54.5973, -5.9301),
    "dublin": (53.3498, -6.2603),
    "cork": (51.8985, -8.4756),
    # Europe
    "paris": (48.8566, 2.3522),
    "frankfurt": (50.1109, 8.6821),
    "amsterdam": (52.3676, 4.9041),
    "brussels": (50.8503, 4.3517),
    "madrid": (40.4168, -3.7038),
    "barcelona": (41.3874, 2.1686),
    "lisbon": (38.7223, -9.1393),
    "milan": (45.4642, 9.1900),
    "rome": (41.9028, 12.4964),
    "zurich": (47.3769, 8.5417),
    "geneva": (46.2044, 6.1432),
    "vienna": (48.2082, 16.3738),
    "berlin": (52.5200, 13.4050),
    "munich": (48.1351, 11.5820),
    "hamburg": (53.5511, 9.9937),
    "stockholm": (59.3293, 18.0686),
    "oslo": (59.9139, 10.7522),
    "copenhagen": (55.6761, 12.5683),
    "helsinki": (60.1699, 24.9384),
    "warsaw": (52.2297, 21.0122),
    "prague": (50.0755, 14.4378),
    "budapest": (47.4979, 19.0402),
    "athens": (37.9838, 23.7275),
    "istanbul": (41.0082, 28.9784),
    "moscow": (55.7558, 37.6173),
    # Middle East & Africa
    "tel aviv": (32.0853, 34.7818),
    "dubai": (25.2048, 55.2708),
    "manama": (26.2285, 50.5860),
    "riyadh": (24.7136, 46.6753),
    "cape town": (-33.9249, 18.4241),
    "johannesburg": (-26.2041, 28.0473),
    "lagos": (6.5244, 3.3792),
    "nairobi": (-1.2921, 36.8219),
    # Asia Pacific
    "mumbai": (19.0760, 72.8777),
    "hyderabad": (17.3850, 78.4867),
    "bangalore": (12.9716, 77.5946),
    "delhi": (28.6139, 77.2090),
    "singapore": (1.3521, 103.8198),
    "jakarta": (-6.2088, 106.8456),
    "kuala lumpur": (3.1390, 101.6869),
    "bangkok": (13.7563, 100.5018),
    "hong kong": (22.3193, 114.1694),
    "shanghai": (31.2304, 121.4737),
    "beijing": (39.9042, 116.4074),
    "taipei": (25.0330, 121.5654),
    "seoul": (37.5665, 126.9780),
    "tokyo": (35.6762, 139.6503),
    "osaka": (34.6937, 135.5023),
    "sydney": (-33.8688, 151.2093),
    "melbourne": (-37.8136, 144.9631),
    "perth": (-31.9505, 115.8605),
    "auckland": (-36.8485, 174.7633),
    # North America
    "new york": (40.7128, -74.0060),
    "boston": (42.3601, -71.0589),
    "washington": (38.9072, -77.0369),
    "ashburn": (39.0438, -77.4874),
    "atlanta": (33.7490, -84.3880),
    "miami": (25.7617, -80.1918),
    "chicago": (41.8781, -87.6298),
    "dallas": (32.7767, -96.7970),
    "columbus": (39.9612, -82.9988),
    "denver": (39.7392, -104.9903),
    "phoenix": (33.4484, -112.0740),
    "los angeles": (34.0522, -118.2437),
    "san francisco": (37.7749, -122.4194),
    "seattle": (47.6062, -122.3321),
    "portland": (45.5152, -122.6784),
    "toronto": (43.6532, -79.3832),
    "montreal": (45.5017, -73.5673),
    "calgary": (51.0447, -114.0719),
    "mexico city": (19.4326, -99.1332),
    # South America
    "sao paulo": (-23.5505, -46.6333),
    "rio de janeiro": (-22.9068, -43.1729),
    "buenos aires": (-34.6037, -58.3816),
    "santiago": (-33.4489, -70.6693),
    "bogota": (4.7110, -74.0721),
}

ALIASES: dict[str, str] = {
    "nyc": "new york",
    "new york city": "new york",
    "washington dc": "washington",
    "dc": "washington",
    "la": "los angeles",
    "sf": "san francisco",
    "bahrain": "manama",
    "bengaluru": "bangalore",
    "new delhi": "delhi",
    "são paulo": "sao paulo",
    "bogotá": "bogota",
    "zürich": "zurich",
    "münchen": "munich",
    "frankfurt am main": "frankfurt",
    "northern virginia": "ashburn",
    "n. virginia": "ashburn",
    "us-east-1": "ashburn",
    "us-east-2": "columbus",
    "us-west-1": "san francisco",
    "us-west-2": "portland",
    "eu-west-1": "dublin",
    "eu-west-2": "london",
    "eu-west-3": "paris",
    "eu-central-1": "frankfurt",
    "eu-central-2": "zurich",
    "eu-north-1": "stockholm",
    "eu-south-1": "milan",
    "eu-south-2": "madrid",
    "ap-southeast-1": "singapore",
    "ap-southeast-2": "sydney",
    "ap-southeast-3": "jakarta",
    "ap-southeast-4": "melbourne",
    "ap-northeast-1": "tokyo",
    "ap-northeast-2": "seoul",
    "ap-northeast-3": "osaka",
    "ap-south-1": "mumbai",
    "ap-south-2": "hyderabad",
    "ap-east-1": "hong kong",
    "me-south-1": "manama",
    "me-central-1": "dubai",
    "il-central-1": "tel aviv",
    "af-south-1": "cape town",
    "sa-east-1": "sao paulo",
    "ca-central-1": "montreal",
    "ca-west-1": "calgary",
}


def _normalise(name: str) -> str:
    return " ".join(name.strip().lower().replace("_", " ").replace("-", " ").split())


class UnknownCityError(ValueError):
    """Raised when a city name is not in the gazetteer; carries close matches."""

    def __init__(self, name: str, suggestions: list[str]) -> None:
        self.name = name
        self.suggestions = suggestions
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        super().__init__(f"Unknown city {name!r}.{hint} Use --list-cities to see all.")


def lookup_city(name: str) -> City:
    """Resolve a city name or alias (including AWS region codes) to a :class:`City`."""
    key = _normalise(name)
    # AWS region codes contain hyphens that _normalise turns into spaces; try both.
    raw_key = name.strip().lower()
    key = ALIASES.get(raw_key, ALIASES.get(key, key))
    coords = _CITIES.get(key)
    if coords is None:
        suggestions = difflib.get_close_matches(key, list(_CITIES) + list(ALIASES), n=3)
        raise UnknownCityError(name, suggestions)
    return City(name=key.title(), lat=coords[0], lon=coords[1])


def all_city_names() -> list[str]:
    """Return every known city name, sorted."""
    return sorted(_CITIES)


def haversine_km(a: City, b: City) -> float:
    """Great-circle distance between two cities in kilometres."""
    lat1, lon1 = math.radians(a.lat), math.radians(a.lon)
    lat2, lon2 = math.radians(b.lat), math.radians(b.lon)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))
