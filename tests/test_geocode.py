import json
import urllib.parse

import pytest

from network_transfer.geocode import NOMINATIM_URL, USER_AGENT, geocode

LEEDS = [
    {
        "name": "Leeds",
        "display_name": "Leeds, West Yorkshire, England, United Kingdom",
        "lat": "53.7974185",
        "lon": "-1.5437941",
    }
]


def test_geocode_parses_top_hit_and_builds_request() -> None:
    seen: list[str] = []

    def fetch(url: str) -> bytes:
        seen.append(url)
        return json.dumps(LEEDS).encode()

    result = geocode("  Leeds   UK ", fetch)
    assert result.name == "Leeds"
    assert result.display_name.startswith("Leeds, West Yorkshire")
    assert result.lat == pytest.approx(53.7974185)
    assert result.lon == pytest.approx(-1.5437941)

    assert seen[0].startswith(NOMINATIM_URL + "?")
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(seen[0]).query)
    assert query == {"q": ["Leeds UK"], "format": ["jsonv2"], "limit": ["1"]}


def test_geocode_falls_back_to_query_for_name() -> None:
    hit = [{"display_name": "Somewhere", "lat": "1", "lon": "2"}]
    result = geocode("Somewhere, Nowhere", lambda _url: json.dumps(hit).encode())
    assert result.name == "Somewhere"


def test_geocode_no_match_suggests_country() -> None:
    with pytest.raises(ValueError, match=r"No match for 'Xyzzy'.*'Xyzzy, UK'"):
        geocode("Xyzzy", lambda _url: b"[]")


@pytest.mark.parametrize(
    "raw",
    [b"not json", b'{"a": 1}', b"[1]", b'[{"display_name": "x"}]', b'[{"lat": "a", "lon": "b"}]'],
)
def test_geocode_rejects_malformed_responses(raw: bytes) -> None:
    with pytest.raises(ValueError):
        geocode("Leeds", lambda _url: raw)


def test_geocode_rejects_empty_query() -> None:
    with pytest.raises(ValueError, match="empty"):
        geocode("   ", lambda _url: b"[]")


def test_user_agent_identifies_the_tool() -> None:
    assert USER_AGENT.startswith("nettransfer/")


# --- the real fetcher, with urlopen patched -----------------------------------------------


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def testdefault_fetch_sets_user_agent_and_returns_body(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request

    from network_transfer.geocode import default_fetch

    seen: dict[str, object] = {}

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _FakeResponse:
        seen["ua"] = request.get_header("User-agent")
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        return _FakeResponse(b"[]")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert default_fetch("https://example.invalid/x") == b"[]"
    assert seen["ua"] == USER_AGENT
    assert seen["url"] == "https://example.invalid/x"
    assert seen["timeout"] == 10


def testdefault_fetch_wraps_network_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error
    import urllib.request

    from network_transfer.geocode import default_fetch

    def raise_url_error(*_a: object, **_k: object) -> None:
        raise urllib.error.URLError("name resolution failed")

    monkeypatch.setattr(urllib.request, "urlopen", raise_url_error)
    with pytest.raises(ValueError, match="Could not reach the geocoder"):
        default_fetch("https://example.invalid/x")

    def raise_timeout(*_a: object, **_k: object) -> None:
        raise TimeoutError

    monkeypatch.setattr(urllib.request, "urlopen", raise_timeout)
    with pytest.raises(ValueError, match="timed out"):
        default_fetch("https://example.invalid/x")
