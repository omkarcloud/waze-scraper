"""Offline tests for the Waze parsers, reference resolvers and area helpers.

    python -m pytest waze/test_parsers.py -q

Fixtures are real www.waze.com/live-map/api bodies captured 2026-11-07
(waze/fixtures/): the georss feed of the three clusters (NYC na, London
row, Tel Aviv il, one list at the 200 cap), a metro-sized LA box, the
wrong-cluster empty feed, three user-drive answers (a toll route with
E-ZPass pricing, London ULEZ with on-route jams, Paris), an autocomplete
list, a venue, a reverse-geocoding row, a timezone and the always-empty
/venues/nearby. No network.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from waze import api, parsers as P, refs  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture(name):
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        return json.load(f)


# ---- refs: resolvers ------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("40.758,-73.985", {"lat": 40.758, "lng": -73.985}),
    (" 51.5074 , -0.1278 ", {"lat": 51.5074, "lng": -0.1278}),
    ("40.70,-74.05,40.80,-73.90", {"south": 40.70, "west": -74.05, "north": 40.80, "east": -73.90}),
    ("https://www.waze.com/live-map/?latlng=40.758,-73.985&zoom=15", {"lat": 40.758, "lng": -73.985}),
    ("https://www.waze.com/live-map/directions?to=ll.40.758%2C-73.9855&from=ll.40.785%2C-73.968",
     {"lat": 40.758, "lng": -73.9855}),
    ("https://ul.waze.com/ul?ll=40.75,-73.98&navigate=yes", {"lat": 40.75, "lng": -73.98}),
])
def test_resolve_location(value, expected):
    assert refs.resolve_location(value) == expected


@pytest.mark.parametrize("value", [
    "", "times square", "40.758", "95,-73.985", "40.80,-74.05,40.70,-73.90",      # south > north
    "40.0,-74.0,44.0,-73.0",                                                        # 4 degrees tall
    "https://example.com/?latlng=40.758,-73.985",                                   # wrong site
    "https://www.waze.com/live-map/directions?to=place.ChIJmQJIxlVYwokRLgeuocVOGVU",  # a place, no point
])
def test_resolve_location_invalid(value):
    with pytest.raises(ValueError):
        refs.resolve_location(value)


@pytest.mark.parametrize("value,expected", [
    ("40.758,-73.985", {"lat": 40.758, "lng": -73.985}),
    ("187433368.1874464748.1166515", {"venue_id": "187433368.1874464748.1166515"}),
    ("venues.187433368.1874464748.1166515", {"venue_id": "187433368.1874464748.1166515"}),
    ("ChIJmQJIxlVYwokRLgeuocVOGVU", {"google_place_id": "ChIJmQJIxlVYwokRLgeuocVOGVU"}),
    ("googlePlaces.ChIJmQJIxlVYwokRLgeuocVOGVU", {"google_place_id": "ChIJmQJIxlVYwokRLgeuocVOGVU"}),
    ("https://www.waze.com/live-map/directions?to=place.w.187433368.1874464748.1166515",
     {"venue_id": "187433368.1874464748.1166515"}),
    ("https://www.waze.com/live-map/directions?from=ll.40.785,-73.968&to=place.ChIJmQJIxlVYwokRLgeuocVOGVU",
     {"google_place_id": "ChIJmQJIxlVYwokRLgeuocVOGVU"}),
    ("https://www.waze.com/live-map/directions?from=ll.40.785,-73.968", {"lat": 40.785, "lng": -73.968}),
    ("https://ul.waze.com/ul?place=ChIJmQJIxlVYwokRLgeuocVOGVU&ll=40.75,-73.98",
     {"google_place_id": "ChIJmQJIxlVYwokRLgeuocVOGVU"}),
    # newer venues: the middle part wraps negative (Westminster Abbey, London)
    ("235864579.-1936518114.1663094", {"venue_id": "235864579.-1936518114.1663094"}),
    ("https://www.waze.com/live-map/directions?to=place.w.235864579.-1936518114.1663094",
     {"venue_id": "235864579.-1936518114.1663094"}),
])
def test_resolve_point(value, expected):
    assert refs.resolve_point(value) == expected


@pytest.mark.parametrize("value", ["", "times square", "https://www.waze.com/live-map/", "1.2", "place.ws.na.123"])
def test_resolve_point_invalid(value):
    with pytest.raises(ValueError):
        refs.resolve_point(value)


@pytest.mark.parametrize("value,expected", [
    ("venues.187433368.1874464748.1166515", {"venue_id": "187433368.1874464748.1166515"}),
    ("187433368.1874464748.1166515", {"venue_id": "187433368.1874464748.1166515"}),
    ("ChIJmQJIxlVYwokRLgeuocVOGVU", {"google_place_id": "ChIJmQJIxlVYwokRLgeuocVOGVU"}),
    ("https://www.waze.com/live-map/directions/millennium-times-square/187433368.1874464748.1166515",
     {"venue_id": "187433368.1874464748.1166515"}),
    ("https://www.waze.com/ul?place=ChIJmQJIxlVYwokRLgeuocVOGVU", {"google_place_id": "ChIJmQJIxlVYwokRLgeuocVOGVU"}),
    ("venues.235864579.-1936518114.1663094", {"venue_id": "235864579.-1936518114.1663094"}),
    ("235864579.-1936518114.1663094", {"venue_id": "235864579.-1936518114.1663094"}),
    ("https://www.waze.com/live-map/directions/westminster-abbey/235864579.-1936518114.1663094",
     {"venue_id": "235864579.-1936518114.1663094"}),
])
def test_resolve_place(value, expected):
    assert refs.resolve_place(value) == expected


@pytest.mark.parametrize("value", ["", "40.758,-73.985", "https://www.waze.com/live-map/?latlng=40.758,-73.985",
                                   "https://google.com/maps/place/ChIJmQJIxlVYwokRLgeuocVOGVU"])
def test_resolve_place_invalid(value):
    with pytest.raises(ValueError):
        refs.resolve_place(value)


# ---- refs: regions + links ------------------------------------------------------------

@pytest.mark.parametrize("lat,lng,env", [
    (40.758, -73.985, "na"), (49.28, -123.12, "na"), (21.3, -157.8, "na"), (61.2, -149.9, "na"),
    (51.507, -0.128, "row"), (48.857, 2.352, "row"), (-33.87, 151.2, "row"), (19.43, -99.13, "row"),
    (32.085, 34.78, "il"),
])
def test_geo_env(lat, lng, env):
    assert refs.geo_env(lat, lng) == env


def test_geo_env_candidates_try_both_clusters_on_the_border():
    assert refs.geo_env_candidates(19.43, -99.13) == ("row",)          # Mexico City is ROW (probed 2026-11-07)
    assert refs.geo_env_candidates(31.7, -106.4) == ("na", "row")      # El Paso / Ciudad Juárez
    assert refs.geo_env_candidates(18.4, -66.1) == ("na", "row")       # San Juan
    assert refs.geo_env_candidates(40.758, -73.985) == ("na",)
    assert refs.geo_env_candidates(51.5, -0.1) == ("row",)


def test_route_error_message():
    from waze import fetch
    assert fetch.route_error(fixture("user_drive_error.json")) == \
        ("Waze could not route this trip: Apologies - we can only provide routes under 3000 miles at this point. "
         "We're working on increasing it (code 402)")
    assert fetch.route_error({"error": "INTERNAL: Routing Server Error [Details(client_code: \t 501)]", "code": "501"}) == \
        "Waze could not route this trip: Routing Server Error (code 501)"
    assert fetch.route_error({}) == "Waze could not route this trip: no route"


def test_links():
    assert refs.point_link(40.758, -73.985) == "https://www.waze.com/live-map/?latlng=40.758000,-73.985000&zoom=17"
    assert refs.venue_link("1.2.3") == "https://www.waze.com/live-map/directions?to=place.w.1.2.3"
    assert refs.google_place_link("ChIJx") == "https://www.waze.com/live-map/directions?to=place.ChIJx"
    assert refs.directions_link({"lat": 40.758, "lng": -73.985}, {"venue_id": "1.2.3"}) == \
        "https://www.waze.com/live-map/directions?to=ll.40.758000,-73.985000&from=place.w.1.2.3"


# ---- georss ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def nyc():
    return P.parse_georss(fixture("georss_nyc.json"))


def test_feed_counts(nyc):
    assert len(nyc["alerts"]) == 22 and len(nyc["jams"]) == 111
    assert nyc["is_capped"] == {"alerts": False, "jams": False}
    london = P.parse_georss(fixture("georss_london.json"))
    assert len(london["alerts"]) == 107 and len(london["jams"]) == 143
    telaviv = P.parse_georss(fixture("georss_telaviv.json"))
    assert len(telaviv["jams"]) == 200 and telaviv["is_capped"] == {"alerts": False, "jams": True}


def test_alert_fields(nyc):
    alert = next(a for a in nyc["alerts"] if a["id"] == "alert-1693598134/e6da48df-941f-4861-9d9c-c22f37a98c0c")
    assert alert["street"] == "Sinatra Dr"
    assert alert["type"] == "ROAD_CLOSED" and alert["subtype"] is None
    assert alert["reported_by"] == "City of Hoboken" and alert["provider"] == "City of Hoboken"
    assert alert["location"] == {"lat": 40.743405, "lng": -74.024828}
    assert alert["published_at"] == "2026-04-13T14:22:14Z"
    assert alert["thumbs_up_count"] == 1 and alert["description"] is None
    assert alert["link"] == "https://www.waze.com/live-map/?latlng=40.743405,-74.024828&zoom=17"
    # newest first
    stamps = [a["published_at"] for a in nyc["alerts"]]
    assert stamps == sorted(stamps, reverse=True)


def test_alert_description_and_subtype():
    london = P.parse_georss(fixture("georss_london.json"))
    alert = next(a for a in london["alerts"] if a["reported_by"] == "ClosureMonitor")
    assert alert["description"].startswith("Closed until") and alert["provider"] == "UK Community"
    assert any(a["subtype"] == "ROAD_CLOSED_EVENT" for a in london["alerts"])


def test_jam_fields(nyc):
    jam = next(j for j in nyc["jams"] if j["id"] == 1197597798)
    assert jam["street"] == "Stone St" and jam["city"] == "Manhattan, NY" and jam["end_node"] == "William St"
    assert jam["level"] == 5 and jam["severity"] == "standstill"
    assert jam["speed_kmh"] == 0.0 and jam["length_meters"] == 105
    assert jam["is_blocked"] is True and jam["blocked_since"] == "2026-09-11T11:11:55Z"
    assert jam["cause"] == {"type": "ROAD_CLOSED", "subtype": None, "description": None}
    assert jam["updated_at"] == "2026-11-07T08:42:03Z"
    assert jam["line"][0] == {"lat": 40.704175, "lng": -74.010692}
    assert jam["segments"] == [{"id": 510651135, "from_node": 28571730, "to_node": 28571733, "is_forward": True}]
    assert jam["link"].startswith("https://www.waze.com/live-map/?latlng=")
    moving = next(j for j in nyc["jams"] if j["id"] == 84761894)
    assert moving["street"] == "Huron St" and moving["end_node"] == "Franklin St" and moving["length_meters"] == 284
    assert moving["level"] == 3 and moving["severity"] == "heavy" and moving["speed_kmh"] == 5.3   # 1.483 m/s
    assert moving["is_blocked"] is False and moving["cause"] is None and moving["start_node"] is None
    assert moving["blocked_since"] is None and len(moving["segments"]) == 1
    # worst first
    levels = [j["level"] for j in nyc["jams"]]
    assert levels == sorted(levels, reverse=True)


def test_empty_feed():
    feed = P.parse_georss(fixture("georss_empty.json"))
    assert feed == {"alerts": [], "jams": [], "is_capped": {"alerts": False, "jams": False}}
    assert P.parse_georss({}) == feed
    assert P.parse_alert({})["id"] is None and P.parse_jam({})["level"] is None


# ---- routes ---------------------------------------------------------------------------

TO = {"lat": 40.6413, "lng": -73.7781}
FROM = {"lat": 40.758, "lng": -73.9855}


def test_toll_route():
    routes = P.parse_routes(fixture("user_drive_nyc.json"), TO, FROM)
    assert len(routes) == 3 and routes[0]["is_fastest"] is True
    r = routes[0]
    assert r["name"] == "I-495 / Qns-Midtown Tun Queens (12.03)"
    assert r["duration_seconds"] == 1862 and r["distance_meters"] == 27862
    assert r["is_toll"] is True and r["is_hov"] is False
    assert r["toll"] == {"price": 12.03, "currency": "USD", "road_name": None, "pass_name": "E-ZPass - NY",
                         "price_with_pass": 7.46, "note": "Toll prices are estimated based on your route and info added by the Waze community"}
    assert r["route_type"] == "Best route with toll" and r["traffic_status"] == "usual traffic"
    assert r["attributes"] == ["TOLL", "ETA_FROM_ML"] and r["restricted_areas"] == []
    assert len(r["polyline"]) == 390 and r["polyline"][0] == {"lat": 40.757965, "lng": -73.985549}
    assert r["link"] == "https://www.waze.com/live-map/directions?to=ll.40.641300,-73.778100&from=ll.40.758000,-73.985500"
    assert routes[1]["is_toll"] is False and routes[1]["toll"] is None
    assert "UNNATURAL" in routes[2]["attributes"]


def test_toll_prices_round_and_need_a_pass():
    # Toronto's 407 ETR with a transponder (float noise), Israel's Highway 6 (no pass, 0.0), 2026-11-10
    toronto = P.parse_toll({"tollPrice": 36.74, "tollPriceCurrencyCode": "CAD", "passBasedMissingPassName": "407ETR",
                            "passBasedPriceChangeToPrice": 31.439999999999994, "passBasedUserPassName": ""})
    assert toronto["price"] == 36.74 and toronto["pass_name"] == "407ETR" and toronto["price_with_pass"] == 31.44
    israel = P.parse_toll({"tollPrice": 61.3, "tollPriceCurrencyCode": "ILS", "passBasedMissingPassName": "",
                           "passBasedPriceChangeToPrice": 0.0, "passBasedUserPassName": "", "tollRoadName": ""})
    assert israel["price"] == 61.3 and israel["pass_name"] is None and israel["price_with_pass"] is None


def test_london_route_restricted_area_and_jams():
    routes = P.parse_routes(fixture("user_drive_london.json"), {"lat": 51.47, "lng": -0.4543})
    r = routes[0]
    assert r["restricted_areas"] == ["London ULEZ"] and "RESTRICTED_ZONE" in r["attributes"]
    assert r["jams"] == [{"id": 90559484, "severity": 0, "line": r["jams"][0]["line"]}]
    assert len(r["jams"][0]["line"]) >= 2 and r["alerts"] == []
    assert r["link"] == "https://www.waze.com/live-map/directions?to=ll.51.470000,-0.454300"


def test_paris_route_label_as_string():
    body = fixture("user_drive_paris.json")
    alt = body["alternatives"][0]
    alt["response"]["label"] = json.dumps(alt["response"]["label"])       # the string form some builds send
    alt["response"]["tollPriceInfo"] = "{}"
    r = P.parse_route(alt, {"lat": 49.0097, "lng": 2.5479})
    assert r["traffic_status"] == "usual traffic" and r["toll"] is None
    assert r["name"].startswith("Périphérique")


def test_planned_route():
    routes = P.parse_routes(fixture("user_drive_future.json"), TO, FROM)   # startTime two hours ahead
    assert len(routes) == 2 and routes[0]["is_fastest"] and routes[0]["duration_seconds"] > 0
    assert routes[0]["traffic_status"] is None                                # a planned trip carries no live status


def test_route_sparse():
    assert P.parse_routes({}, TO) == []
    r = P.parse_route({"response": {}}, TO)
    assert r["duration_seconds"] is None and r["polyline"] == [] and r["toll"] is None


# ---- places ---------------------------------------------------------------------------

def test_place_hits():
    hits = P.parse_place_hits(fixture("autocomplete_times_square.json"))
    assert len(hits) == 10
    assert hits[0] == {"id": "ChIJmQJIxlVYwokRLgeuocVOGVU", "kind": "google_place", "name": "Times Square",
                       "address": "Manhattan, NY, USA", "location": {"lat": 40.7579747, "lng": -73.9855426},
                       "link": "https://www.waze.com/live-map/directions?to=place.ChIJmQJIxlVYwokRLgeuocVOGVU"}
    venue = hits[2]
    assert venue["kind"] == "waze_venue" and venue["id"] == "187433368.1874464748.1166515"
    assert venue["link"] == "https://www.waze.com/live-map/directions?to=place.w.187433368.1874464748.1166515"
    bare = P.parse_place_hit({"name": "Somewhere", "latLng": {"lat": 1.5, "lng": 2.5}})
    assert bare["kind"] == "address" and bare["id"] is None and bare["link"].startswith("https://www.waze.com/live-map/?latlng=1.5")
    pizza = P.parse_place_hits(fixture("autocomplete_pizza.json"))          # biased to Times Square: venues + Google places
    assert len(pizza) == 10 and pizza[0]["kind"] == "waze_venue" and pizza[0]["name"] == "Pizza Slice"
    assert {h["kind"] for h in pizza} == {"waze_venue", "google_place"}
    world = P.parse_place_hits(fixture("autocomplete_eiffel_world.json"))    # no `near`: the world viewport
    assert world[0]["name"] == "Eiffel Tower" and world[0]["address"] == "Avenue Gustave Eiffel, Paris, France"


def _world_hits(slug):
    record = fixture(f"autocomplete_world_{slug}.json")
    return record["query"], {env: P.parse_place_hits(record[env]) for env in api.SEARCH_REGIONS}


@pytest.mark.parametrize("slug,region,first", [
    ("london", "row", ("London", "UK")),                       # na: London, ON
    ("paris", "row", ("Paris", "France")),                     # na: Paris, TX
    ("times_square", "na", ("Times Square", "Manhattan, NY, USA")),     # row: Berjaya Times Square, Kuala Lumpur
    ("jfk_airport", "na", ("John F. Kennedy International Airport (JFK)", "Queens")),
    ("tel_aviv", "il", ("Tel Aviv", "Israel")),               # na: a grill in Encino
    ("westminster_abbey_london", "row", ("Westminster Abbey", "Dean's Yard, London, UK")),   # na: London, ON
])
def test_world_search_puts_the_right_region_first(slug, region, first):
    query, hits = _world_hits(slug)
    best, merged = P.merge_region_hits(query, hits, api.SEARCH_REGIONS)
    assert best == region
    assert (merged[0]["name"], merged[0]["address"]) == first
    assert merged[:len(hits[region])] == hits[region]             # the best region's hits stay in Waze's order
    ids = [(h["kind"], h["id"]) for h in merged if h["id"]]
    assert len(ids) == len(set(ids))                              # a place two regions know is listed once


def test_region_confidence_parts():
    query, hits = _world_hits("london")
    assert P.region_confidence(query, hits["row"]) == 3.0         # every word, same name, three London landmarks
    assert P.region_confidence(query, hits["na"]) == 2.0          # every word, same name, scattered Londons
    assert P.region_confidence(query, []) is None
    assert P.merge_region_hits("x", {"na": [], "row": [], "il": []}, api.SEARCH_REGIONS) == (None, [])


def test_places_search_without_near_asks_every_region(monkeypatch):
    query, _ = _world_hits("london")
    record = fixture("autocomplete_world_london.json")
    calls = []

    def fake_autocomplete(q, env=None, near=None, viewport=None, language=None):
        calls.append((env, near))
        return record[env]
    monkeypatch.setattr(api, "fetch_autocomplete", fake_autocomplete)
    out = api._places_search(query, None, None, 5)
    assert sorted(calls) == [("il", None), ("na", None), ("row", None)]
    assert out["region"] == "row" and out["near"] is None
    assert [r["name"] for r in out["results"][:2]] == ["London", "London Bridge"] and len(out["results"]) == 5
    calls.clear()
    out = api._places_search(query, {"lat": 51.5308, "lng": -0.1238}, None, 5)
    assert calls == [("row", (51.5308, -0.1238))] and out["region"] == "row"     # `near` keeps one region


def test_google_place_live_fixture():
    p = P.parse_google_place(fixture("places_google.json"))
    assert p["id"] == "ChIJmQJIxlVYwokRLgeuocVOGVU" and p["name"] == "Times Square"
    assert p["link"] == "https://www.waze.com/live-map/directions?to=place.ChIJmQJIxlVYwokRLgeuocVOGVU"
    assert p["location"] == {"lat": 40.7579747, "lng": -73.9855426}
    assert p["address"]["formatted"] == "Manhattan, NY 10036, USA" and p["address"]["postal_code"] == "10036"
    assert p["address"]["city"] == "New York" and p["address"]["district"] == "Manhattan"
    assert p["address"]["state"] == "New York" and p["address"]["country"] == "United States"
    assert p["website"] == "https://www.timessquarenyc.org/" and p["phone"] is None
    assert p["types"] == ["establishment", "point_of_interest", "tourist_attraction"]
    assert p["opening_hours"] is None                                     # the field is in the request, absent for a landmark
    assert len(p["photos"]) == 8 and p["photos"][0]["reference"].startswith("AV-") and p["photos"][0]["height"] == 3599
    assert p["photos"][0]["attributions"][0].startswith("<a href=")


def test_venue():
    v = P.parse_venue(fixture("venue.json"))
    assert v["id"] == "187433368.1874464748.1166515" and v["kind"] == "waze_venue"
    assert v["name"] == "Millennium Times Square New York"
    assert v["link"] == "https://www.waze.com/live-map/directions?to=place.w.187433368.1874464748.1166515"
    assert v["location"] == {"lat": 40.757118225, "lng": -73.984863281}
    assert v["address"] == {"street": "W 44th St", "house_number": "145", "city": "Manhattan", "state": None,
                            "country_code": "US", "country": "United States",
                            "formatted": "145 W 44th St, Manhattan, United States"}
    assert v["phone"] == "(212) 768-4400" and v["website"] is None
    assert "wi_fi" in v["services"] and len(v["services"]) == 7
    assert len(v["images"]) == 9 and v["images"][0]["large"].startswith("https://venue-image.waze.com/thumbs/thumb700_")
    assert len(v["opening_hours"]) == 7
    assert v["opening_hours"][0] == {"day": "sunday", "opens": "00:00", "closes": "00:00", "is_open_all_day": True}
    assert v["google_place_id"] is None and v["region"] == "na"


def test_venue_sparse():
    v = P.parse_venue({"id": "venues.1.2.3"})
    assert v["id"] == "1.2.3" and v["name"] is None and v["opening_hours"] == [] and v["address"]["formatted"] is None
    assert P.parse_venue({})["id"] is None


def test_google_place_shapes():
    """No live capture of /places/<id> (Google Places field names, Waze's
    fixed field list): the parser must take the flat and the `result`
    wrapped shapes and never crash on an empty body."""
    body = {"result": {"place_id": "ChIJx", "name": "Times Square", "formatted_address": "Manhattan, NY 10036, USA",
                       "geometry": {"location": {"lat": 40.758, "lng": -73.9855}},
                       "address_components": [{"long_name": "Manhattan", "types": ["sublocality_level_1"]},
                                              {"long_name": "New York", "types": ["administrative_area_level_1"]},
                                              {"long_name": "United States", "types": ["country"]},
                                              {"long_name": "10036", "types": ["postal_code"]}],
                       "website": "https://www.timessquarenyc.org/", "types": ["tourist_attraction"],
                       "opening_hours": {"open_now": True, "weekday_text": ["Monday: Open 24 hours"]},
                       "photos": [{"photo_reference": "abc", "width": 1000, "height": 800, "html_attributions": ["<a>x</a>"]}]}}
    p = P.parse_google_place(body)
    assert p["id"] == "ChIJx" and p["kind"] == "google_place" and p["location"] == {"lat": 40.758, "lng": -73.9855}
    assert p["address"]["district"] == "Manhattan" and p["address"]["city"] == "Manhattan"     # no locality: the district stands in
    assert p["address"]["postal_code"] == "10036" and p["address"]["street"] is None
    assert p["opening_hours"]["is_open_now"] is True and p["photos"][0]["reference"] == "abc"
    flat = P.parse_google_place(body["result"], "ChIJy")
    assert flat["id"] == "ChIJx"
    empty = P.parse_google_place({}, "ChIJy")
    assert empty["id"] == "ChIJy" and empty["opening_hours"] is None and empty["photos"] == [] and empty["location"] is None


# ---- geocoding ------------------------------------------------------------------------

def test_reverse_and_timezone():
    assert P.parse_reverse_geocode(fixture("reverse_geocoding.json")) == \
        {"address": "7th Ave, Manhattan", "street": "7th Ave", "city": "Manhattan"}
    assert P.parse_reverse_geocode([]) is None
    assert P.parse_timezone(fixture("timezone.json")) == {"id": "America/New_York", "name": "Eastern Standard Time",
                                                         "utc_offset_seconds": -18000, "raw_offset_seconds": -18000,
                                                         "dst_offset_seconds": 0}
    assert P.parse_timezone({}) is None
    assert fixture("nearby_address.json") == {"nearbyVenues": []}


# ---- api helpers ----------------------------------------------------------------------

def test_bbox_for_point_and_box():
    box = api._bbox_for({"lat": 40.758, "lng": -73.985}, 5)
    assert round(box["north"] - box["south"], 4) == round(10 / 111.32, 4)
    assert box["east"] - box["west"] > box["north"] - box["south"]      # longitude degrees are shorter at 40N
    assert api._bbox_for({"south": 1, "west": 2, "north": 3, "east": 4}, 99) == {"south": 1, "west": 2, "north": 3, "east": 4}
    area = api._area_out(box)
    assert area["center"] == {"lat": 40.758, "lng": -73.985} and area["region"] == "na"
    assert api._bbox_key(box).count(",") == 3


def test_reverse_keeps_the_time_zone_when_waze_fails_the_street(monkeypatch):
    """Waze answers 500 for some points every time: the time zone still comes back, uncached."""
    from cache import DontCache
    from waze import fetch

    def failing_street(lat, lng, env, radius=0.0005):
        raise fetch.WazeServerError("reverse-geocoding: HTTP 500", 500)
    monkeypatch.setattr(api, "fetch_reverse_geocode", failing_street)
    monkeypatch.setattr(api, "fetch_timezone", lambda lat, lng, timestamp=None: fixture("timezone.json"))
    out = api._reverse({"lat": 51.5308, "lng": -0.1238})
    assert isinstance(out, DontCache)
    assert out.data["address"] is None and out.data["region"] == "row"
    assert out.data["timezone"]["id"] == "America/New_York"          # the fixture's zone, passed through

    def bad_gateway(lat, lng, env, radius=0.0005):
        raise fetch.WazeServerError("reverse-geocoding: HTTP 502", 502)
    monkeypatch.setattr(api, "fetch_reverse_geocode", bad_gateway)
    with pytest.raises(fetch.WazeServerError):
        api._reverse({"lat": 51.5308, "lng": -0.1238})
    monkeypatch.setattr(api, "fetch_reverse_geocode", lambda lat, lng, env, radius=0.0005: fixture("reverse_geocoding.json"))
    assert api._reverse({"lat": 40.7484, "lng": -73.9857})["address"]["street"]       # a normal answer stays plain


def test_feed_tiling_merges_by_id(monkeypatch):
    """A list at the cap splits the box in four; the tiles' records merge by id."""
    import config
    calls = []
    full = fixture("georss_telaviv.json")

    def fake_georss(south, west, north, east, env, types=None):
        calls.append((round(south, 3), round(west, 3), round(north, 3), round(east, 3)))
        if len(calls) == 1:
            return full                                            # the parent box, 200 jams
        start = (len(calls) - 2) * 20                                   # four disjoint slices of 20 jams
        return {"alerts": full["alerts"][:3], "jams": full["jams"][start:start + 20]}
    monkeypatch.setattr(api, "fetch_georss", fake_georss)
    monkeypatch.setattr(config, "WAZE_TILE_DEPTH", 1)
    feed = api._fetch_feed({"south": 32.04, "west": 34.74, "north": 32.12, "east": 34.84}, "il")
    assert len(calls) == 5 and calls[1][2] == 32.08                   # four tiles split at the centre
    assert len(feed["jams"]) == 80 and len(feed["alerts"]) == 3        # merged by id, no duplicates
    assert feed["is_capped"] == {"alerts": False, "jams": False}
