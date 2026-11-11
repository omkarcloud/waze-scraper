"""Raw live-map API JSON -> clean dicts for /waze/*. Pure functions, no I/O.

Fixtures (waze/fixtures/, captured 2026-11-07 from www.waze.com/live-map/api
with a browser-minted reCAPTCHA token, see waze/fetch.py):
  georss_nyc.json / georss_london.json / georss_telaviv.json   the three
      clusters (na / row / il); georss_la_big.json a metro-sized box near
      the 200-per-type cap; georss_empty.json the wrong-cluster answer
  user_drive_nyc.json (a toll route with E-ZPass pricing), user_drive_london.json
      (ULEZ restricted area, on-route jams), user_drive_paris.json
  autocomplete_times_square.json   (Google places + a Waze venue)
  autocomplete_world_*.json        (2026-11-10) one bare query (world viewport,
      no sll) asked on each region: {"query", "na": [...], "row": [...], "il": [...]}
  venue.json                       a Waze venue with hours / services / images
  reverse_geocoding.json, timezone.json
  nearby_address.json              (/venues/nearby, always empty: not served)

Conventions (API design rules): snake_case, `link` not `url`, booleans as
questions, numbers as numbers, dates as ISO 8601 UTC, null for absent
values, identity fields first, presentation-only strings dropped.
"""
import json
import math
import re
from datetime import datetime, timezone

from waze import refs

ALERT_TYPES = ("ROAD_CLOSED", "ACCIDENT", "HAZARD", "POLICE", "JAM", "WEATHERHAZARD", "CONSTRUCTION", "MISC")
JAM_LEVELS = {0: "free_flow", 1: "light", 2: "moderate", 3: "heavy", 4: "very_heavy", 5: "standstill"}
WEEKDAYS = ("sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday")   # venue hours: 0 = Sunday (the page compares with getUTCDay())
GEORSS_CAP = 200          # per type per box


# ---- helpers ---------------------------------------------------------------------------

def _text(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso(millis):
    ms = _int(millis)
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _point(raw):
    """{x, y} (Waze) or {lat, lng} -> {lat, lng}, or None."""
    if not isinstance(raw, dict):
        return None
    lat = _float(raw.get("y") if "y" in raw else raw.get("lat"))
    lng = _float(raw.get("x") if "x" in raw else raw.get("lng"))
    if lat is None or lng is None:
        return None
    return {"lat": lat, "lng": lng}


def _line(raw):
    return [p for p in (_point(x) for x in (raw or [])) if p]


def _obj(raw):
    """A dict, or a JSON string holding one (user-drive's `label` and
    `tollPriceInfo` arrive as strings in some builds)."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}
    return {}


# ---- georss ----------------------------------------------------------------------------

def parse_alert(raw):
    """One georss alert -> a closure / incident record."""
    raw = raw or {}
    location = _point(raw.get("location"))
    return {
        "id": _text(raw.get("id")),
        "type": _text(raw.get("type")),
        "subtype": _text(raw.get("subtype")),
        "description": _text(raw.get("reportDescription")),
        "street": _text(raw.get("street")),
        "city": _text(raw.get("city")),
        "location": location,
        "reported_by": _text(raw.get("reportBy")),
        "provider": _text(raw.get("provider")),
        "provider_id": _text(raw.get("providerId")),
        "thumbs_up_count": _int(raw.get("nThumbsUp")) or 0,
        "published_at": _iso(raw.get("pubMillis")),
        "link": refs.point_link(location["lat"], location["lng"]) if location else None,
    }


def parse_jam(raw):
    """One georss jam -> a congestion record (speed in km/h, length in m)."""
    raw = raw or {}
    line = _line(raw.get("line"))
    level = _int(raw.get("level"))
    speed = _float(raw.get("speed"))          # metres per second upstream
    cause = raw.get("causeAlert") if isinstance(raw.get("causeAlert"), dict) else None
    mid = line[len(line) // 2] if line else None
    return {
        "id": _int(raw.get("id")) if _int(raw.get("id")) is not None else _text(raw.get("id")),
        "street": _text(raw.get("street")),
        "city": _text(raw.get("city")),
        "start_node": _text(raw.get("startNode")),
        "end_node": _text(raw.get("endNode")),
        "level": level,
        "severity": JAM_LEVELS.get(level),
        "speed_kmh": round(speed * 3.6, 1) if speed is not None else None,
        "length_meters": _int(raw.get("length")),
        "is_blocked": bool(raw.get("blockUpdate")) or (level == 5 and bool(cause)),
        "blocked_since": _iso(raw.get("blockUpdate")),
        "cause": {
            "type": _text(cause.get("type")),
            "subtype": _text(cause.get("subtype")),
            "description": _text(cause.get("reportDescription")),
        } if cause else None,
        "updated_at": _iso(raw.get("updateMillis")),
        "line": line,
        "segments": [{
            "id": _int(s.get("ID")),
            "from_node": _int(s.get("fromNode")),
            "to_node": _int(s.get("toNode")),
            "is_forward": bool(s.get("isForward")),
        } for s in (raw.get("segments") or []) if isinstance(s, dict)],
        "link": refs.point_link(mid["lat"], mid["lng"]) if mid else None,
    }


def parse_georss(body):
    """{alerts, jams, users} -> {"alerts": [...], "jams": [...], "is_capped": {...}}.
    Alerts newest first, jams worst first. `is_capped` says which list hit
    Waze's 200-per-box ceiling (the caller tiles the box then)."""
    body = body or {}
    alerts = [parse_alert(a) for a in (body.get("alerts") or []) if isinstance(a, dict)]
    jams = [parse_jam(j) for j in (body.get("jams") or []) if isinstance(j, dict)]
    alerts.sort(key=lambda a: a["published_at"] or "", reverse=True)
    jams.sort(key=lambda j: (j["level"] or 0, j["length_meters"] or 0), reverse=True)
    return {
        "alerts": alerts,
        "jams": jams,
        "is_capped": {"alerts": len(alerts) >= GEORSS_CAP, "jams": len(jams) >= GEORSS_CAP},
    }


# ---- routes ----------------------------------------------------------------------------

def _money(value):
    """A currency amount, 2 decimals (user-drive sends float noise: 31.439999999999994)."""
    value = _float(value)
    return round(value, 2) if value is not None else None


def parse_toll(raw):
    info = _obj(raw)
    if not info:
        return None
    pass_name = _text(info.get("passBasedMissingPassName")) or _text(info.get("passBasedUserPassName"))
    return {
        "price": _money(info.get("tollPrice")),
        "currency": _text(info.get("tollPriceCurrencyCode")),
        "road_name": _text(info.get("tollRoadName")),
        "pass_name": pass_name,
        # Only a pass gets a pass price: a toll without one answers 0.0 here
        # (Israel's Highway 6, 2026-11-10), which would read as "free".
        "price_with_pass": _money(info.get("passBasedPriceChangeToPrice")) if pass_name else None,
        "note": _text(info.get("serverParameterizedMessage")),
    }


def parse_route_jam(raw):
    raw = raw or {}
    return {"id": _int(raw.get("id")), "severity": _int(raw.get("severity")), "line": _line(raw.get("line"))}


def parse_route_alert(raw):
    """An on-route alert (same fields as the feed where present)."""
    raw = raw or {}
    out = parse_alert(raw)
    if out["location"] is None:
        out["location"] = _point({"y": raw.get("y"), "x": raw.get("x")}) or _point(raw.get("latLng"))
    return out


def parse_route(alt, to, from_=None):
    """One user-drive alternative -> a route."""
    alt = alt or {}
    r = alt.get("response") if isinstance(alt.get("response"), dict) else {}
    label = _obj(r.get("label"))
    extra = label.get("extraInfoLabel") if isinstance(label.get("extraInfoLabel"), dict) else {}
    route_type = _text(extra.get("routeTypeLabel"))
    return {
        "name": _text(r.get("routeName")),
        "duration_seconds": _int(r.get("totalSeconds")),
        "distance_meters": _int(r.get("totalLength")),
        "is_fastest": bool(r.get("isFastest")),
        "is_toll": bool(r.get("isToll")),
        "is_hov": bool(r.get("isHOV")),
        "passes_through_danger_area": bool(r.get("passesThroughDangerArea")),
        "route_type": route_type.lstrip("$") if route_type else None,
        "traffic_status": _text(extra.get("trafficStatusLabel")),
        "toll": parse_toll(r.get("tollPriceInfo")),
        "restricted_areas": [a for a in (r.get("areas") or []) if a],
        "required_permits": [p for p in (r.get("requiredPermits") or []) if p],
        "attributes": [a for a in (r.get("routeAttr") or []) if a],
        "jams": [parse_route_jam(j) for j in (r.get("jams") or []) if isinstance(j, dict)],
        "alerts": [parse_route_alert(a) for a in (r.get("alerts") or []) if isinstance(a, dict)],
        "polyline": _line(alt.get("coords")),
        "link": refs.directions_link(to, from_),
    }


def parse_routes(body, to, from_=None):
    alts = (body or {}).get("alternatives") or []
    routes = [parse_route(a, to, from_) for a in alts if isinstance(a, dict)]
    routes.sort(key=lambda x: (not x["is_fastest"], x["duration_seconds"] or 0))
    return routes


# ---- places ----------------------------------------------------------------------------

def _place_ref(venue_id):
    """autocomplete's venueId -> (kind, id, link)."""
    venue_id = _text(venue_id) or ""
    if venue_id.startswith("venues."):
        vid = venue_id[len("venues."):]
        return "waze_venue", vid, refs.venue_link(vid)
    if venue_id.startswith("googlePlaces."):
        gid = venue_id[len("googlePlaces."):]
        return "google_place", gid, refs.google_place_link(gid)
    return None, venue_id or None, None


def parse_place_hit(raw):
    """One autocomplete suggestion."""
    raw = raw or {}
    kind, place_id, link = _place_ref(raw.get("venueId"))
    location = _point(raw.get("latLng"))
    if link is None and location:
        link = refs.point_link(location["lat"], location["lng"])
    return {
        "id": place_id,
        "kind": kind or "address",
        "name": _text(raw.get("cleanName")) or _text(raw.get("name")),
        "address": _text(raw.get("address")),
        "location": location,
        "link": link,
    }


def parse_place_hits(body):
    return [parse_place_hit(h) for h in (body or []) if isinstance(h, dict)]


# A search without `near` asks every region: each one answers from its own
# part of the world only ("London" is London, ON on na and London, UK on row;
# "Sydney Opera House" is found on row alone; probed 2026-11-10). The region
# whose first hit fits the query best goes first. Its confidence adds up:
#   - the share of the query's words found in the hit's name + address (0..1)
#   - the hit's name equal to (1) or starting with (0.5) the query's part
#     before the first comma
#   - the share of the next AGREE_HITS hits within AGREE_KM of it (0..1): a
#     real city's own landmarks back it up, same-named small towns scatter.
_WORD_RE = re.compile(r"[^\W_]+")
AGREE_KM = 30
AGREE_HITS = 3


def _words(text):
    return [w.casefold() for w in _WORD_RE.findall(text or "")]


def _distance_km(a, b):
    """Equirectangular distance between two {"lat","lng"} points (fine at city scale)."""
    x = math.radians(b["lng"] - a["lng"]) * math.cos(math.radians((a["lat"] + b["lat"]) / 2))
    y = math.radians(b["lat"] - a["lat"])
    return 6371.0 * math.hypot(x, y)


def region_confidence(query, hits):
    """How sure one region's autocomplete is of its first hit: 0..3, None without hits."""
    if not hits:
        return None
    top = hits[0]
    wanted = set(_words(query))
    found = set(_words(f"{top.get('name') or ''} {top.get('address') or ''}"))
    coverage = len(wanted & found) / len(wanted) if wanted else 0.0
    head = " ".join(_words(query.split(",")[0]))
    name = " ".join(_words(top.get("name")))
    naming = 1.0 if head and name == head else 0.5 if head and name.startswith(head + " ") else 0.0
    agreeing = 0
    if top.get("location"):
        agreeing = sum(1 for h in hits[1:1 + AGREE_HITS]
                       if h.get("location") and _distance_km(top["location"], h["location"]) <= AGREE_KM)
    return coverage + naming + agreeing / AGREE_HITS


def merge_region_hits(query, hits_by_region, order):
    """{region: [hit, ...]} -> (best region or None, hits): the most confident
    region's hits first, then the other regions' by confidence (ties keep
    `order`), the same place listed twice kept once."""
    scored = [(region_confidence(query, hits_by_region.get(region) or []), i, region)
              for i, region in enumerate(order)]
    scored = [s for s in scored if s[0] is not None]
    ranked = [region for _, _, region in sorted(scored, key=lambda s: (-s[0], s[1]))]
    merged, seen = [], set()
    for region in ranked:
        for hit in hits_by_region[region]:
            key = (hit["kind"], hit["id"]) if hit.get("id") else (hit.get("name"), json.dumps(hit.get("location")))
            if key not in seen:
                seen.add(key)
                merged.append(hit)
    return (ranked[0] if ranked else None), merged


def _hhmm(raw):
    if not isinstance(raw, dict):
        return None
    h, m = _int(raw.get("hours")), _int(raw.get("minutes"))
    if h is None:
        return None
    return f"{h:02d}:{m or 0:02d}"


def parse_opening_hours(raw):
    """Waze venue hours [{day, from, to}] -> one row per listed day; 00:00
    to 00:00 is open all day."""
    out = []
    for row in raw or []:
        if not isinstance(row, dict):
            continue
        day = _int(row.get("day"))
        opens, closes = _hhmm(row.get("from")), _hhmm(row.get("to"))
        out.append({
            "day": WEEKDAYS[day] if day is not None and 0 <= day < 7 else _text(row.get("day")),
            "opens": opens,
            "closes": closes,
            "is_open_all_day": opens == "00:00" and closes == "00:00",
        })
    return out


def _venue_address(raw):
    raw = raw if isinstance(raw, dict) else {}
    street = _text(raw.get("street"))
    number = _text(raw.get("houseNumber"))
    city = _text(raw.get("city"))
    state = _text(raw.get("state"))
    country = _text(raw.get("countryName"))
    line = " ".join(p for p in (number, street) if p) or None
    formatted = ", ".join(p for p in (line, city, state, country) if p) or None
    return {
        "street": street,
        "house_number": number,
        "city": city,
        "state": state,
        "country_code": _text(raw.get("countryCode")),
        "country": country,
        "formatted": formatted,
    }


def parse_venue(raw):
    """A Waze venue record (/venues/<id>)."""
    raw = raw or {}
    venue_id = _text(raw.get("id")) or ""
    bare = venue_id[len("venues."):] if venue_id.startswith("venues.") else venue_id
    phone = raw.get("phone") if isinstance(raw.get("phone"), dict) else {}
    return {
        "id": bare or None,
        "kind": "waze_venue",
        "name": _text(raw.get("name")),
        "link": refs.venue_link(bare) if bare else None,
        "location": _point(raw.get("latLng")),
        "address": _venue_address(raw.get("address")),
        "phone": _text(phone.get("text")) or _text(raw.get("phone") if isinstance(raw.get("phone"), str) else None),
        "website": _text(raw.get("website")) or _text(raw.get("url")),
        "description": _text(raw.get("description")),
        "categories": [c for c in (raw.get("categories") or []) if c] or None,
        "services": [s for s in (raw.get("services") or []) if s],
        "opening_hours": parse_opening_hours(raw.get("hours")),
        "images": [{
            "id": _text(img.get("id")),
            "small": _text((img.get("src") or {}).get("small")),
            "large": _text((img.get("src") or {}).get("large")),
            "attributions": [a for a in (img.get("attributions") or []) if a],
        } for img in (raw.get("images") or []) if isinstance(img, dict)],
        "google_place_id": _text(raw.get("googlePlaceId")),
        "region": _text(raw.get("geoEnv")),
    }


def _components(raw):
    """Google address_components -> a flat dict by type (a component lists
    several, "political" first; the first component of each type wins)."""
    out = {}
    for comp in raw or []:
        if not isinstance(comp, dict):
            continue
        for kind in comp.get("types") or []:
            if kind not in out:
                out[kind] = _text(comp.get("long_name")) or _text(comp.get("short_name"))
    return out


def parse_google_place(raw, google_place_id=None):
    """The Google place details Waze serves (/places/<id>, Google Places
    field names; the body may wrap them in `result`)."""
    raw = raw or {}
    place = raw.get("result") if isinstance(raw.get("result"), dict) else raw
    geometry = place.get("geometry") if isinstance(place.get("geometry"), dict) else {}
    hours = place.get("opening_hours") if isinstance(place.get("opening_hours"), dict) else {}
    comps = _components(place.get("address_components"))
    place_id = _text(place.get("place_id")) or google_place_id
    return {
        "id": place_id,
        "kind": "google_place",
        "name": _text(place.get("name")),
        "link": refs.google_place_link(place_id) if place_id else None,
        "location": _point(geometry.get("location")),
        "address": {
            "formatted": _text(place.get("formatted_address")),
            "street": " ".join(p for p in (comps.get("street_number"), comps.get("route")) if p) or None,
            "district": comps.get("sublocality_level_1") or comps.get("sublocality"),
            "city": comps.get("locality") or comps.get("postal_town") or comps.get("sublocality_level_1"),
            "state": comps.get("administrative_area_level_1"),
            "postal_code": comps.get("postal_code"),
            "country": comps.get("country"),
        },
        "phone": _text(place.get("formatted_phone_number")),
        "international_phone": _text(place.get("international_phone_number")),
        "website": _text(place.get("website")),
        "types": [t for t in (place.get("types") or []) if t],
        "opening_hours": {
            "is_open_now": hours.get("open_now") if isinstance(hours.get("open_now"), bool) else None,
            "weekday_text": [t for t in (hours.get("weekday_text") or []) if t],
            "periods": [p for p in (hours.get("periods") or []) if isinstance(p, dict)],
        } if hours else None,
        "photos": [{
            "reference": _text(p.get("photo_reference")),
            "width": _int(p.get("width")),
            "height": _int(p.get("height")),
            "attributions": [a for a in (p.get("html_attributions") or []) if a],
        } for p in (place.get("photos") or []) if isinstance(p, dict)],
    }


# ---- geocoding -------------------------------------------------------------------------

def parse_reverse_geocode(rows):
    """[{address, street, city, streetName}] -> the first match or None."""
    row = next((r for r in (rows or []) if isinstance(r, dict)), None)
    if not row:
        return None
    return {
        "address": _text(row.get("address")),
        "street": _text(row.get("street")) or _text(row.get("streetName")),
        "city": _text(row.get("city")),
    }


def parse_timezone(raw):
    raw = raw or {}
    if not raw.get("timeZoneId"):
        return None
    base, dst = _int(raw.get("rawOffset")) or 0, _int(raw.get("dstOffset")) or 0
    return {
        "id": _text(raw.get("timeZoneId")),
        "name": _text(raw.get("timeZoneName")),
        "utc_offset_seconds": base + dst,
        "raw_offset_seconds": base,
        "dst_offset_seconds": dst,
    }
