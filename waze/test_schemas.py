"""Offline tests for the /waze/* request schemas.

    python -m pytest waze/test_schemas.py -q
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from marshmallow import ValidationError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from waze import schemas  # noqa: E402


def _err(schema, data):
    with pytest.raises(ValidationError) as e:
        schema().load(data)
    return e.value.normalized_messages()


# ---- alerts / jams ----------------------------------------------------------------------

def test_alerts_schema_point_defaults():
    assert schemas.AlertsSearchSchema().load({"location": "40.758,-73.985"}) == \
        {"location": {"lat": 40.758, "lng": -73.985}, "radius_km": 5.0, "types": None, "limit": 200}


def test_alerts_schema_bbox_types_limit():
    loaded = schemas.AlertsSearchSchema().load(
        {"location": "40.70,-74.05,40.80,-73.90", "types": "road_closed, hazard", "limit": "50", "radius_km": "2"})
    assert loaded["location"] == {"south": 40.70, "west": -74.05, "north": 40.80, "east": -73.90}
    assert loaded["types"] == ["ROAD_CLOSED", "HAZARD"] and loaded["limit"] == 50 and loaded["radius_km"] == 2.0


def test_alerts_schema_link():
    loaded = schemas.AlertsSearchSchema().load({"location": "https://www.waze.com/live-map/?latlng=51.5,-0.12&zoom=14"})
    assert loaded["location"] == {"lat": 51.5, "lng": -0.12}


@pytest.mark.parametrize("data,field", [
    ({}, "location"),
    ({"location": "times square"}, "location"),
    ({"location": "40.758,-73.985", "radius_km": "0"}, "radius_km"),
    ({"location": "40.758,-73.985", "radius_km": "51"}, "radius_km"),
    ({"location": "40.758,-73.985", "types": "police,ufo"}, "types"),
    ({"location": "40.758,-73.985", "limit": "1001"}, "limit"),
    ({"location": "40.758,-73.985", "lat": "40"}, "lat"),                      # unknown param
])
def test_alerts_schema_rejects(data, field):
    assert field in _err(schemas.AlertsSearchSchema, data)


def test_alerts_schema_error_message_names_the_forms():
    msg = _err(schemas.AlertsSearchSchema, {"location": "nope"})["location"][0]
    assert "lat,lng" in msg and "south,west,north,east" in msg


def test_jams_schema():
    loaded = schemas.JamsSearchSchema().load({"location": "32.08,34.78", "min_level": "4"})
    assert loaded == {"location": {"lat": 32.08, "lng": 34.78}, "radius_km": 5.0, "min_level": 4, "limit": 200}
    assert "min_level" in _err(schemas.JamsSearchSchema, {"location": "32.08,34.78", "min_level": "6"})


# ---- routes -----------------------------------------------------------------------------

def test_routes_schema_points_and_ids():
    loaded = schemas.RoutesSearchSchema().load({"from": "40.758,-73.9855", "to": "40.6413,-73.7781"})
    assert loaded == {"from_": {"lat": 40.758, "lng": -73.9855}, "to": {"lat": 40.6413, "lng": -73.7781},
                      "depart_at": None, "arrive_at": None, "max_routes": 3}
    loaded = schemas.RoutesSearchSchema().load({"from": "187433368.1874464748.1166515",
                                                "to": "https://ul.waze.com/ul?place=ChIJmQJIxlVYwokRLgeuocVOGVU",
                                                "max_routes": "1"})
    assert loaded["from_"] == {"venue_id": "187433368.1874464748.1166515"}
    assert loaded["to"] == {"google_place_id": "ChIJmQJIxlVYwokRLgeuocVOGVU"} and loaded["max_routes"] == 1


def test_routes_schema_times():
    soon = (datetime.now(timezone.utc) + timedelta(hours=2)).replace(microsecond=0)
    loaded = schemas.RoutesSearchSchema().load({"from": "1,2", "to": "3,4", "depart_at": soon.strftime("%Y-%m-%dT%H:%M:%SZ")})
    assert loaded["depart_at"] == int(soon.timestamp() * 1000)
    naive = soon.strftime("%Y-%m-%dT%H:%M")            # no zone = UTC
    loaded = schemas.RoutesSearchSchema().load({"from": "1,2", "to": "3,4", "arrive_at": naive})
    assert loaded["arrive_at"] == int(soon.replace(second=0).timestamp() * 1000)
    offset = (soon.astimezone(timezone(timedelta(hours=2)))).isoformat()
    loaded = schemas.RoutesSearchSchema().load({"from": "1,2", "to": "3,4", "depart_at": offset})
    assert loaded["depart_at"] == int(soon.timestamp() * 1000)


@pytest.mark.parametrize("data,field", [
    ({"to": "3,4"}, "from"),
    ({"from": "1,2"}, "to"),
    ({"from": "nowhere", "to": "3,4"}, "from"),
    ({"from": "1,2", "to": "3,4", "max_routes": "4"}, "max_routes"),
    ({"from": "1,2", "to": "3,4", "depart_at": "tomorrow"}, "depart_at"),
    ({"from": "1,2", "to": "3,4", "depart_at": "2020-01-01T10:00"}, "depart_at"),
    ({"from": "1,2", "to": "3,4", "depart_at": "2099-01-01T10:00"}, "depart_at"),
    ({"from": "1,2", "to": "3,4", "depart_at": "2026-12-01T10:00", "arrive_at": "2026-12-01T12:00"}, "arrive_at"),
    ({"from": "1,2", "to": "3,4", "url": "x"}, "url"),
])
def test_routes_schema_rejects(data, field):
    assert field in _err(schemas.RoutesSearchSchema, data)


# ---- places -----------------------------------------------------------------------------

def test_places_search_schema():
    loaded = schemas.PlacesSearchSchema().load({"query": " pizza "})
    assert loaded == {"query": "pizza", "near": None, "language": None, "limit": 10}
    loaded = schemas.PlacesSearchSchema().load({"query": "pizza", "near": "40.758,-73.985", "language": "FR", "limit": "20"})
    assert loaded["near"] == {"lat": 40.758, "lng": -73.985} and loaded["language"] == "fr" and loaded["limit"] == 20
    assert "query" in _err(schemas.PlacesSearchSchema, {})
    assert "near" in _err(schemas.PlacesSearchSchema, {"query": "x", "near": "here"})
    assert "language" in _err(schemas.PlacesSearchSchema, {"query": "x", "language": "en-US"})
    assert "limit" in _err(schemas.PlacesSearchSchema, {"query": "x", "limit": "21"})


def test_place_details_schema():
    assert schemas.PlaceDetailsSchema().load({"place": "venues.187433368.1874464748.1166515"}) == \
        {"place": {"venue_id": "187433368.1874464748.1166515"}, "language": None}
    assert schemas.PlaceDetailsSchema().load({"place": "ChIJmQJIxlVYwokRLgeuocVOGVU", "language": "he"}) == \
        {"place": {"google_place_id": "ChIJmQJIxlVYwokRLgeuocVOGVU"}, "language": "he"}
    loaded = schemas.PlaceDetailsSchema().load(
        {"place": "https://www.waze.com/live-map/directions?to=place.w.187433368.1874464748.1166515"})
    assert loaded["place"] == {"venue_id": "187433368.1874464748.1166515"}
    assert "place" in _err(schemas.PlaceDetailsSchema, {"place": "40.758,-73.985"})
    assert "place" in _err(schemas.PlaceDetailsSchema, {})
    assert "id" in _err(schemas.PlaceDetailsSchema, {"place": "ChIJmQJIxlVYwokRLgeuocVOGVU", "id": "x"})


def test_place_details_error_message():
    msg = _err(schemas.PlaceDetailsSchema, {"place": "times square"})["place"][0]
    assert "venue id" in msg and "Google place id" in msg


# ---- reverse ----------------------------------------------------------------------------

def test_reverse_schema():
    assert schemas.ReverseGeocodeSchema().load({"location": "40.758,-73.985"}) == {"location": {"lat": 40.758, "lng": -73.985}}
    assert "location" in _err(schemas.ReverseGeocodeSchema, {})
    assert "location" in _err(schemas.ReverseGeocodeSchema, {"location": "manhattan"})
