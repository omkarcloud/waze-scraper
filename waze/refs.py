"""Reference resolvers, link builders and the region (geo-env) rule for /waze/*.

ONE param per input (tripadvisor QueryOrIdField convention, never a sibling
`url`/`id` pair). Every resolver takes the raw query string and returns a
plain dict, or raises ValueError with a user-facing message (RefField turns
it into a 400):

  location   "lat,lng" (a point) | "south,west,north,east" (a bounding box)
             | a Waze live-map / ul.waze.com link carrying one
             -> {"lat", "lng"} or {"south", "west", "north", "east"}
  point      "lat,lng" | a Waze venue id | a Google place id | a live-map
             link (its `to=`, else `from=`, else `latlng=` / `ll=`)
             -> {"lat", "lng"} | {"venue_id"} | {"google_place_id"}
  place      a Waze venue id ("venues.187433368.1874464748.1166515" or the
             bare "187433368.1874464748.1166515"), a Google place id
             ("googlePlaces.ChIJ…" or the bare "ChIJ…"), or a live-map /
             ul.waze.com link with `place.w.<venue>` / `place.<google>` /
             `?place=<google>`
             -> {"venue_id"} | {"google_place_id"}

Identifier shapes on the live map (web-assets.waze.com/livemap/prod/
livemap-*.js, 2026-11-07): a Waze venue is `venues.<a>.<b>.<c>` (three
integers), a Google place is `googlePlaces.<id>`; links carry them as
`place.w.<a.b.c>` and `place.<id>`, a point as `ll.<lat>,<lng>`.

Regions: Waze runs three routing clusters and every call names one
(`env` / `geoEnv` / `geo_env` = "na" | "row" | "il"). A bounding box asked
on the wrong cluster answers an empty feed and a route request fails (code
501), so geo_env() picks it from the coordinates: Israel -> "il", the USA
and Canada -> "na", everything else -> "row" (Mexico City is on ROW:
probed 2026-11-07, na answers it empty). Along the US-Mexico border and
for Puerto Rico a rectangle cannot tell the two apart, so
geo_env_candidates() lists both there and the caller falls back on an
empty feed / a routing error.
"""
import re
from urllib.parse import parse_qs, urlparse

SITE = "https://www.waze.com"
LIVE_MAP = SITE + "/live-map"

# The middle part is an int32 that wraps negative on newer venues
# (235864579.-1936518114.1663094 is Westminster Abbey, London; 2026-11-10).
VENUE_ID_RE = re.compile(r"^\d+\.-?\d+\.\d+$")
GOOGLE_PLACE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,}$")
_LATLNG_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
_BBOX_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
_VENUE_IN_PATH_RE = re.compile(r"(\d+\.-?\d+\.\d+)(?:\.html)?/?$")

MAX_BBOX_SPAN_DEG = 3.0     # georss serves ~metro-sized boxes; larger ones only hit the 200-item cap harder


def _is_waze_link(value):
    return value.startswith(("http://", "https://", "//"))


def _parse_link(value):
    url = value if not value.startswith("//") else "https:" + value
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not (host == "waze.com" or host.endswith(".waze.com")):
        raise ValueError("Must be a waze.com link.")
    query = {k: v[0] for k, v in parse_qs(parsed.query).items() if v}
    return parsed, query


def _latlng(text):
    match = _LATLNG_RE.match(text or "")
    if not match:
        return None
    lat, lng = float(match.group(1)), float(match.group(2))
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ValueError("Latitude must be -90..90 and longitude -180..180.")
    return {"lat": lat, "lng": lng}


def _bbox(text):
    match = _BBOX_RE.match(text or "")
    if not match:
        return None
    south, west, north, east = (float(match.group(i)) for i in range(1, 5))
    if not (-90 <= south <= 90 and -90 <= north <= 90 and -180 <= west <= 180 and -180 <= east <= 180):
        raise ValueError("Latitude must be -90..90 and longitude -180..180.")
    if south >= north or west >= east:
        raise ValueError("A bounding box is south,west,north,east with south < north and west < east.")
    if north - south > MAX_BBOX_SPAN_DEG or east - west > MAX_BBOX_SPAN_DEG:
        raise ValueError(f"A bounding box may span at most {MAX_BBOX_SPAN_DEG:g} degrees per side.")
    return {"south": south, "west": west, "north": north, "east": east}


def _place_token(token):
    """A `to=` / `from=` / `place=` value of a live-map link -> a point /
    venue / google place dict, or None when it is none of those."""
    if not token:
        return None
    if token.startswith("ll."):
        return _latlng(token[3:])
    if token.startswith("place.ws."):
        return None                              # a street id: no detail page serves it
    if token.startswith("place.w."):
        venue = token[len("place.w."):]
        return {"venue_id": venue} if VENUE_ID_RE.match(venue) else None
    if token.startswith("place."):
        gid = token[len("place."):]
        return {"google_place_id": gid} if GOOGLE_PLACE_ID_RE.match(gid) else None
    if token.startswith("venues."):
        venue = token[len("venues."):]
        return {"venue_id": venue} if VENUE_ID_RE.match(venue) else None
    if token.startswith("googlePlaces."):
        gid = token[len("googlePlaces."):]
        return {"google_place_id": gid} if GOOGLE_PLACE_ID_RE.match(gid) else None
    return None


def _link_point(parsed, query, prefer_point=False):
    """The point / place a live-map or ul.waze.com link points at: its
    `to=`, else `from=`, else `place=`, else `latlng=` / `ll=`, else a venue
    id in the path. `prefer_point` reads the coordinates first (a ul link
    carries both `place=` and `ll=`)."""
    def point():
        for key in ("latlng", "ll"):
            found = _latlng(query.get(key, ""))
            if found:
                return found
        return None
    if prefer_point:
        found = point()
        if found:
            return found
    for key in ("to", "from"):
        found = _place_token(query.get(key))
        if found:
            return found
    if query.get("place") and GOOGLE_PLACE_ID_RE.match(query["place"]):
        return {"google_place_id": query["place"]}
    found = point()
    if found:
        return found
    match = _VENUE_IN_PATH_RE.search(parsed.path or "")
    if match:
        return {"venue_id": match.group(1)}
    return None


# ---- resolvers ---------------------------------------------------------------------

def resolve_location(value):
    """"lat,lng" | "south,west,north,east" | a Waze link with a point."""
    value = (value or "").strip()
    if not value:
        raise ValueError("Must be lat,lng or a bounding box south,west,north,east.")
    if _is_waze_link(value):
        parsed, query = _parse_link(value)
        found = _link_point(parsed, query, prefer_point=True)
        if found and "lat" in found:
            return found
        raise ValueError("The link carries no coordinates (expected ?latlng=lat,lng or to=ll.lat,lng).")
    found = _bbox(value)
    if found:
        return found
    found = _latlng(value)
    if found:
        return found
    raise ValueError("Must be lat,lng (e.g. 40.758,-73.985), a bounding box south,west,north,east, "
                     "or a waze.com live-map link.")


def resolve_point(value):
    """"lat,lng" | Waze venue id | Google place id | a Waze link."""
    value = (value or "").strip()
    if not value:
        raise ValueError("Must be lat,lng, a Waze venue id, a Google place id or a waze.com link.")
    if _is_waze_link(value):
        parsed, query = _parse_link(value)
        found = _link_point(parsed, query)
        if found:
            return found
        raise ValueError("The link names no place or point (expected to=ll.lat,lng, to=place.<id> or ?latlng=).")
    found = _latlng(value)
    if found:
        return found
    place = _bare_place(value)
    if place:
        return place
    raise ValueError("Must be lat,lng (e.g. 40.758,-73.985), a Waze venue id (187433368.1874464748.1166515), "
                     "a Google place id (ChIJ…) or a waze.com link.")


def _bare_place(value):
    found = _place_token(value)
    if found and "lat" not in found:
        return found
    if VENUE_ID_RE.match(value):
        return {"venue_id": value}
    if GOOGLE_PLACE_ID_RE.match(value) and "." not in value:
        return {"google_place_id": value}
    return None


def resolve_place(value):
    """Waze venue id | Google place id | a Waze link with a place."""
    value = (value or "").strip()
    if not value:
        raise ValueError("Must be a Waze venue id, a Google place id or a waze.com place link.")
    if _is_waze_link(value):
        parsed, query = _parse_link(value)
        found = _link_point(parsed, query)
        if found and "lat" not in found:
            return found
        raise ValueError("The link names no place (expected to=place.w.<venue id>, to=place.<google id> "
                         "or ul.waze.com/ul?place=<google id>).")
    place = _bare_place(value)
    if place:
        return place
    raise ValueError("Must be a Waze venue id (venues.187433368.1874464748.1166515), a Google place id "
                     "(ChIJmQJIxlVYwokRLgeuocVOGVU) or a waze.com place link.")


# ---- links ---------------------------------------------------------------------------

def point_link(lat, lng, zoom=17):
    return f"{LIVE_MAP}/?latlng={lat:.6f},{lng:.6f}&zoom={zoom}"


def directions_link(to, from_=None):
    """to / from_: {"lat","lng"} | {"venue_id"} | {"google_place_id"}."""
    query = f"to={_link_token(to)}"
    if from_:
        query += f"&from={_link_token(from_)}"
    return f"{LIVE_MAP}/directions?{query}"


def _link_token(ref):
    if "venue_id" in ref:
        return f"place.w.{ref['venue_id']}"
    if "google_place_id" in ref:
        return f"place.{ref['google_place_id']}"
    return f"ll.{ref['lat']:.6f},{ref['lng']:.6f}"


def venue_link(venue_id):
    return directions_link({"venue_id": venue_id})


def google_place_link(google_place_id):
    return directions_link({"google_place_id": google_place_id})


# ---- regions ---------------------------------------------------------------------------

def geo_env(lat, lng):
    """The Waze cluster a coordinate belongs to: "il" | "na" | "row"."""
    if 29.0 <= lat <= 33.6 and 34.0 <= lng <= 36.0:
        return "il"
    if lat >= 24.5 and -170.0 <= lng <= -52.0:          # continental USA + Canada
        return "na"
    if 18.0 <= lat <= 23.0 and -161.0 <= lng <= -154.0:  # Hawaii
        return "na"
    if lat >= 51.0 and (lng <= -130.0 or lng >= 170.0):  # Alaska + the Aleutians
        return "na"
    return "row"


def geo_env_candidates(lat, lng):
    """The clusters to try, best first: both along the US-Mexico border
    band (northern Mexico vs Texas / Arizona / California, lat 24.5-32.8)
    and around Puerto Rico; one everywhere else."""
    env = geo_env(lat, lng)
    if env == "na" and lat < 32.8 and -118.0 <= lng <= -96.0:
        return ("na", "row")
    if 17.5 <= lat <= 18.6 and -68.0 <= lng <= -64.5:           # Puerto Rico / USVI
        return ("na", "row")
    return (env,)


def bbox_center(bbox):
    return (bbox["south"] + bbox["north"]) / 2, (bbox["west"] + bbox["east"]) / 2
