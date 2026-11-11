"""The /waze/* endpoint functions (route_glue's route_table calls them with
the schema's loaded kwargs).

alerts/search and jams/search slice ONE cached feed: cache.cached_call
stores the parsed georss body under waze.feed keyed on the bounding box,
so both endpoints on the same area cost one upstream call (one token use).
A box that hits Waze's 200-per-type ceiling is tiled into four and the
tiles merged by id, WAZE_TILE_DEPTH levels deep.
"""
import math
from concurrent.futures import ThreadPoolExecutor

from cache import DontCache, cached_call
from waze import cache_config as ttl
from waze import parsers, refs
from waze.fetch import (WazeBadRequest, WazeServerError, fetch_autocomplete, fetch_georss, fetch_google_place,
                        fetch_reverse_geocode, fetch_routes, fetch_timezone, fetch_venue)
import config

KM_PER_DEG_LAT = 111.32


# ---- areas -----------------------------------------------------------------------------

def _bbox_for(location, radius_km):
    """A location (point or box) -> a bounding box dict (south, west, north, east)."""
    if "south" in location:
        return dict(location)
    lat, lng = location["lat"], location["lng"]
    dlat = radius_km / KM_PER_DEG_LAT
    dlng = radius_km / (KM_PER_DEG_LAT * max(math.cos(math.radians(lat)), 0.01))
    return {"south": max(lat - dlat, -90.0), "west": max(lng - dlng, -180.0),
            "north": min(lat + dlat, 90.0), "east": min(lng + dlng, 180.0)}


def _bbox_key(bbox):
    return ",".join(f"{bbox[k]:.4f}" for k in ("south", "west", "north", "east"))


def _area_out(bbox, region=None):
    lat, lng = refs.bbox_center(bbox)
    return {"south": round(bbox["south"], 6), "west": round(bbox["west"], 6),
            "north": round(bbox["north"], 6), "east": round(bbox["east"], 6),
            "center": {"lat": round(lat, 6), "lng": round(lng, 6)},
            "region": region or refs.geo_env(lat, lng)}


def _fetch_feed(bbox, env, depth=0):
    """The parsed feed for one box, tiled when a list hit the cap."""
    feed = parsers.parse_georss(fetch_georss(bbox["south"], bbox["west"], bbox["north"], bbox["east"], env))
    if depth >= config.WAZE_TILE_DEPTH or not (feed["is_capped"]["alerts"] or feed["is_capped"]["jams"]):
        return feed
    mid_lat, mid_lng = refs.bbox_center(bbox)
    alerts, jams = {}, {}
    for south, north in ((bbox["south"], mid_lat), (mid_lat, bbox["north"])):
        for west, east in ((bbox["west"], mid_lng), (mid_lng, bbox["east"])):
            tile = _fetch_feed({"south": south, "west": west, "north": north, "east": east}, env, depth + 1)
            alerts.update((a["id"], a) for a in tile["alerts"])
            jams.update((j["id"], j) for j in tile["jams"])
    merged = {"alerts": list(alerts.values()), "jams": list(jams.values())}
    merged["alerts"].sort(key=lambda a: a["published_at"] or "", reverse=True)
    merged["jams"].sort(key=lambda j: (j["level"] or 0, j["length_meters"] or 0), reverse=True)
    merged["is_capped"] = {"alerts": False, "jams": False}
    return merged


def _feed_record(bbox):
    south, west, north, east = (float(v) for v in bbox.split(","))
    bbox = {"south": south, "west": west, "north": north, "east": east}
    lat, lng = refs.bbox_center(bbox)
    feed, env_used = None, None
    for env in refs.geo_env_candidates(lat, lng):
        feed, env_used = _fetch_feed(bbox, env), env
        if feed["alerts"] or feed["jams"]:
            break
    feed["region"] = env_used
    return feed


def _feed(bbox):
    return cached_call("waze.feed", {"bbox": _bbox_key(bbox)}, _feed_record, ttl.FEED_CACHE)


# ---- alerts/search -----------------------------------------------------------------------

def alerts_search(location, radius_km, types, limit):
    """Road closures and incidents inside the area, newest first."""
    bbox = _bbox_for(location, radius_km)
    feed = _feed(bbox)
    alerts = feed["alerts"]
    if types:
        wanted = {t.upper() for t in types}
        alerts = [a for a in alerts if (a["type"] or "").upper() in wanted]
    return {
        "area": _area_out(bbox, feed.get("region")),
        "types": list(types) if types else None,
        "count": len(alerts),
        "is_truncated": len(alerts) > limit,
        "alerts": alerts[:limit],
    }


# ---- jams/search -------------------------------------------------------------------------

def jams_search(location, radius_km, min_level, limit):
    """Traffic jams inside the area, worst first."""
    bbox = _bbox_for(location, radius_km)
    feed = _feed(bbox)
    jams = [j for j in feed["jams"] if (j["level"] or 0) >= (min_level or 0)]
    return {
        "area": _area_out(bbox, feed.get("region")),
        "min_level": min_level or 0,
        "count": len(jams),
        "is_truncated": len(jams) > limit,
        "jams": jams[:limit],
    }


# ---- routes/search -----------------------------------------------------------------------

def _resolve_point(ref):
    """{"lat","lng"} | {"venue_id"} | {"google_place_id"} -> (point dict, place record or None)."""
    if "lat" in ref:
        return {"lat": ref["lat"], "lng": ref["lng"]}, None
    if "venue_id" in ref:
        record = _place_record(f"venue:{ref['venue_id']}", None)
    else:
        record = _place_record(f"google:{ref['google_place_id']}", None)
    location = record.get("location")
    if not location:
        raise WazeBadRequest("that place has no coordinates on Waze")
    return location, record


def _routes(from_, to, depart_at, arrive_at, max_routes):
    origin, origin_place = _resolve_point(from_)
    dest, dest_place = _resolve_point(to)
    body, env_used = None, None
    candidates = refs.geo_env_candidates(origin["lat"], origin["lng"])
    for i, env in enumerate(candidates):
        try:
            body, env_used = fetch_routes(origin["lat"], origin["lng"], dest["lat"], dest["lng"], env,
                                          n_paths=max_routes, depart_at_ms=depart_at, arrive_at_ms=arrive_at), env
            break
        except WazeBadRequest:
            if i == len(candidates) - 1:
                raise
    routes = parsers.parse_routes(body, to, from_)[:max_routes]
    return {
        "from": {**origin, "place": _place_summary(origin_place)},
        "to": {**dest, "place": _place_summary(dest_place)},
        "region": env_used,
        "depart_at": parsers._iso(depart_at) if depart_at else None,
        "arrive_at": parsers._iso(arrive_at) if arrive_at else None,
        "count": len(routes),
        "routes": routes,
    }


def _place_summary(record):
    if not record:
        return None
    return {"id": record.get("id"), "kind": record.get("kind"), "name": record.get("name"),
            "address": (record.get("address") or {}).get("formatted"), "link": record.get("link")}


def routes_search(**data):
    return cached_call("waze.routes", data, _routes, ttl.ROUTES_CACHE)


# ---- places/search -----------------------------------------------------------------------

# Without `near` a query is asked on every region at once (each one only
# knows its own part of the world) and parsers.merge_region_hits puts the
# region that fits it best first.
SEARCH_REGIONS = ("na", "row", "il")
_search_pool = ThreadPoolExecutor(max_workers=len(SEARCH_REGIONS) * 2, thread_name_prefix="waze-search")


def _world_search(query, language):
    """A bare query -> (best region or None, hits from every region)."""
    futures = {env: _search_pool.submit(fetch_autocomplete, query, env=env, language=language)
               for env in SEARCH_REGIONS}
    hits = {env: parsers.parse_place_hits(future.result()) for env, future in futures.items()}
    return parsers.merge_region_hits(query, hits, SEARCH_REGIONS)


def _places_search(query, near, language, limit):
    env, point, viewport = None, None, None
    if near:
        if "south" in near:
            viewport = (near["south"], near["west"], near["north"], near["east"])
            lat, lng = refs.bbox_center(near)
        else:
            lat, lng = near["lat"], near["lng"]
            point = (lat, lng)
            viewport = (max(lat - 0.5, -90.0), max(lng - 0.5, -180.0), min(lat + 0.5, 90.0), min(lng + 0.5, 180.0))
        env = refs.geo_env(lat, lng)
        hits = parsers.parse_place_hits(fetch_autocomplete(query, env=env, near=point, viewport=viewport,
                                                           language=language))
    else:
        env, hits = _world_search(query, language)
    return {
        "query": query,
        "near": {"lat": lat, "lng": lng} if near else None,
        "region": env,
        "count": len(hits),
        "results": hits[:limit],
    }


def places_search(**data):
    return cached_call("waze.places_search", data, _places_search, ttl.PLACES_SEARCH_CACHE)


# ---- places/details ----------------------------------------------------------------------

def _place_details(key, language):
    kind, _, place_id = key.partition(":")
    if kind == "venue":
        record = parsers.parse_venue(fetch_venue(place_id, language))
        google_id = record.get("google_place_id")
        record["google_place"] = parsers.parse_google_place(fetch_google_place(google_id, language), google_id) \
            if google_id else None
        return record
    return parsers.parse_google_place(fetch_google_place(place_id, language), place_id)


def _place_record(key, language):
    return cached_call("waze.place", {"key": key, "language": language}, _place_details, ttl.PLACE_CACHE)


def place_details(place, language):
    """A Waze venue (with its Google place record when linked) or a Google place."""
    key = f"venue:{place['venue_id']}" if "venue_id" in place else f"google:{place['google_place_id']}"
    return _place_record(key, language)


# ---- geocode/reverse ---------------------------------------------------------------------

def _reverse(location):
    if "south" in location:
        lat, lng = refs.bbox_center(location)
    else:
        lat, lng = location["lat"], location["lng"]
    env = refs.geo_env(lat, lng)
    try:
        address, street_failed = parsers.parse_reverse_geocode(fetch_reverse_geocode(lat, lng, env)), False
    except WazeServerError as e:
        # Waze answers 500 for some points every time, at any radius, while
        # their neighbours work (51.5308,-0.1238 at King's Cross, London;
        # 2026-11-10): give the time zone without a street, uncached.
        if e.status != 500:
            raise
        address, street_failed = None, True
    tz = parsers.parse_timezone(fetch_timezone(lat, lng))
    result = {
        "location": {"lat": lat, "lng": lng},
        "region": env,
        "address": address,
        "timezone": tz,
        "link": refs.point_link(lat, lng),
    }
    return DontCache(result) if street_failed else result


def geocode_reverse(**data):
    return cached_call("waze.reverse", data, _reverse, ttl.REVERSE_CACHE)
