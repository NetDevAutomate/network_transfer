import json
from pathlib import Path

import pytest

from network_transfer.cities import (
    City,
    UnknownCityError,
    UserCities,
    all_city_names,
    default_store_path,
    haversine_km,
    lookup_city,
)


def test_london_dublin_distance_is_about_460km() -> None:
    km = haversine_km(lookup_city("london"), lookup_city("dublin"))
    assert 455 < km < 475


def test_london_sydney_distance_is_about_17000km() -> None:
    km = haversine_km(lookup_city("London"), lookup_city("sydney"))
    assert 16_900 < km < 17_100


def test_same_city_is_zero() -> None:
    city = lookup_city("paris")
    assert haversine_km(city, city) == 0


def test_lookup_is_case_and_whitespace_insensitive() -> None:
    assert lookup_city("  New   YORK ").name == "New York"


def test_aliases_and_aws_regions_resolve() -> None:
    assert lookup_city("nyc").name == "New York"
    assert lookup_city("eu-west-1").name == "Dublin"
    assert lookup_city("us-east-1").name == "Ashburn"
    assert lookup_city("bahrain").name == "Manama"


def test_unknown_city_suggests_close_matches() -> None:
    with pytest.raises(UnknownCityError) as excinfo:
        lookup_city("Lodnon")
    assert "london" in excinfo.value.suggestions
    assert "Did you mean" in str(excinfo.value)


def test_unknown_city_without_suggestions() -> None:
    with pytest.raises(UnknownCityError) as excinfo:
        lookup_city("zzzzqqqq")
    assert excinfo.value.suggestions == []
    assert "Did you mean" not in str(excinfo.value)


def test_all_city_names_sorted_and_contains_core_hubs() -> None:
    names = all_city_names()
    assert names == sorted(names)
    assert {"london", "dublin", "frankfurt", "ashburn", "sydney"} <= set(names)


def test_haversine_symmetric() -> None:
    a = City("A", 10.0, 20.0)
    b = City("B", -30.0, 150.0)
    assert haversine_km(a, b) == pytest.approx(haversine_km(b, a))


# --- user store -------------------------------------------------------------------------


def test_user_store_round_trip(tmp_path: Path) -> None:
    store = UserCities(tmp_path / "cities.json")
    assert store.get("leeds") is None
    assert store.names() == []

    replaced = store.add("Leeds, UK", City("Leeds", 53.7974, -1.5438), source="test")
    assert replaced is False
    assert store.names() == ["leeds, uk"]

    fresh = UserCities(tmp_path / "cities.json")  # re-read from disk
    assert fresh.get("leeds, uk") == City("Leeds", 53.7974, -1.5438)
    data = json.loads((tmp_path / "cities.json").read_text())
    assert data["leeds, uk"]["source"] == "test"


def test_user_store_takes_precedence_over_builtin(tmp_path: Path) -> None:
    store = UserCities(tmp_path / "c.json")
    replaced = store.add("portland", City("Portland (Dorset)", 50.55, -2.44), source="test")
    assert replaced is True
    assert lookup_city("Portland", store).name == "Portland (Dorset)"
    assert lookup_city("Portland").name == "Portland"  # no store -> built-in


def test_lookup_uses_store_and_suggests_from_it(tmp_path: Path) -> None:
    store = UserCities(tmp_path / "c.json")
    store.add("Leeds", City("Leeds", 53.8, -1.5), source="test")
    assert lookup_city("leeds", store).lat == 53.8
    with pytest.raises(UnknownCityError) as excinfo:
        lookup_city("Leedz", store)
    assert "leeds" in excinfo.value.suggestions
    assert "--add-city 'Leedz'" in str(excinfo.value)


def test_all_city_names_marks_added(tmp_path: Path) -> None:
    store = UserCities(tmp_path / "c.json")
    store.add("Leeds", City("Leeds", 53.8, -1.5), source="test")
    names = all_city_names(store)
    assert "leeds (added)" in names
    assert "london" in names
    assert names == sorted(names)


def test_user_store_missing_file_is_empty_and_corrupt_file_errors(tmp_path: Path) -> None:
    assert UserCities(tmp_path / "nope.json").names() == []
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(ValueError, match="not valid JSON"):
        UserCities(bad).names()
    # non-dict entries are ignored rather than crashing
    odd = tmp_path / "odd.json"
    odd.write_text(json.dumps({"x": 1, "leeds": {"name": "Leeds", "lat": 1, "lon": 2}}))
    assert UserCities(odd).names() == ["leeds"]


def test_default_store_path_is_under_config() -> None:
    assert default_store_path().parts[-3:] == (".config", "nettransfer", "cities.json")
