import pytest

from network_transfer.cities import (
    City,
    UnknownCityError,
    all_city_names,
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
