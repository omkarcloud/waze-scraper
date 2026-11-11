"""Waze transport: the live map's own JSON API (www.waze.com/live-map/api/*)
over plain curl_cffi with browser-impersonated TLS, each call carrying a
reCAPTCHA Enterprise token minted on the browser box (waze/token.py). No
cookies (the page sets none), no login.

Upstream surfaces (livemap-*.js, 2026-11-07; samples in waze/fixtures/):

  * GEORSS   GET /live-map/api/georss?top&bottom&left&right&env&types
             -> {alerts[], jams[], users[]}; 200 per type per box (the
             caller tiles a box that hits the cap); `types` is mandatory
             (500 without it); the wrong env answers an empty feed. The
             public map hides user reports and wazers (config flag
             hideUserReportsAndWazers), so alerts are road closures
             (municipal and closure-monitor providers) and users is [].
  * ROUTES   POST /live-map/api/user-drive?geo_env  {from:{x,y}, to:{x,y},
             nPaths, useCase:"LIVEMAP_PLANNING", arriveAt, startTime|endTime}
             -> 201 {alternatives:[{coords[], response{...}}]} (3 routes at
             most); an {error, code} body for an impossible trip.
  * SEARCH   GET /live-map/api/autocomplete/?q&exp=8,10,12&geo-env&sll&v&lang
             -> [{name, cleanName, address, venueId, latLng}].
  * VENUE    GET /live-map/api/venues/<a.b.c>?locale -> the Waze venue
             (name, address, phone, services, hours, images, googlePlaceId).
             Answered a Mac without a token twice on 2026-11-07 (a cached
             copy, it seems): the pod probe got 403 on every tier bare, so
             it is gated like the rest.
  * PLACE    GET /live-map/api/places/<google id>?locale&fields=... -> the
             Google place details (Waze's allowed field list is fixed:
             PLACE_FIELDS; `rating` answers 400 Invalid fields).
  * REVERSE  GET /live-map/api/reverse-geocoding?lat&lng&geoEnv&radius
             -> [{address, street, city, streetName}]
  * TIMEZONE GET /live-map/api/timezone?lat&lng&timestamp
             -> {dstOffset, rawOffset, timeZoneId, timeZoneName}

Egress: config.waze_proxy() (knob WAZE_EXIT); the token is not IP-bound.
One curl session per thread; a retryable failure drops it so the retry
rides a fresh exit. A 403 is Waze refusing the TOKEN (score / age), not
the IP: the call retires that token and retries at once with another, up
to WAZE_TOKEN_ATTEMPTS, before it is reported as WazeBlocked.

Failure taxonomy (scraper_errors, mapped to HTTP by route_glue):
  WazeUpstreamError  transport failure / 5xx / box down      — retryable
  WazeBlocked        every token refused (403) / 429         — retryable
  WazeBadRequest     upstream 400 / an {error} route body    — never retried
  WazeNotFound       404 / unknown venue or place            — never retried
"""
import os
import re
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import egress
import fastjson
import identity_pool
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError
from waze import token as tokens
from waze.refs import SITE

API = SITE + "/live-map/api"
IMPERSONATE = "chrome"
TIMEOUT = 30

# Waze's own allowed `fields` for /places/<google id> (livemap-*.js `el`).
PLACE_FIELDS = ("address_components", "formatted_address", "formatted_phone_number", "geometry",
                "international_phone_number", "name", "opening_hours", "place_id", "types", "website", "photos")
AUTOCOMPLETE_EXP = "8,10,12"      # MAPS_SUGGEST, PREFER_WAZE, NEW_RANKING: what the page sends
GEORSS_TYPES = "alerts,traffic"   # users is always [] on the public map


class WazeUpstreamError(UpstreamError):
    """Transport failure, 5xx, or no token to be had — retryable."""


class WazeBlocked(WazeUpstreamError, Blocked):
    """Every token tried was refused (403) or spent (429) — retryable."""


class WazeServerError(WazeUpstreamError):
    """Waze answered 5xx — retryable; `status` is the HTTP status."""

    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


class WazeBadRequest(BadRequest):
    """Upstream 400 or a route request Waze could not serve — never retried."""


class WazeNotFound(NotFound):
    """No such venue / place — never retried."""


# ---- sessions ------------------------------------------------------------------------
# One session per cheroot worker thread (a curl handle must not be shared);
# the exit (config.waze_proxy()) is picked when the session is created and
# the Accept-Language follows it (owner rule 2026-10-24).
_local = threading.local()


def _session():
    sess = getattr(_local, "session", None)
    if sess is None:
        from curl_cffi import requests as curl_requests
        sess = curl_requests.Session(impersonate=IMPERSONATE)
        proxy = config.waze_proxy()
        if proxy:
            sess.proxies = {"http": proxy, "https": proxy}
        sess.headers.update({
            "accept": "application/json, text/plain, */*",
            "accept-language": egress.accept_language(proxy),
            "referer": SITE + "/live-map/",
        })
        _local.session = sess
    return sess


def _drop_session():
    sess = getattr(_local, "session", None)
    _local.session = None
    if sess is not None:
        try:
            sess.close()
        except Exception:
            pass


def _retrying(fn):
    """config.MAX_RETRIES attempts for the retryable errors (UpstreamError
    incl. Blocked), a fresh session (exit) each time; NotFound / BadRequest
    surface at once."""
    last = None
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            return fn()
        except WazeUpstreamError as e:
            last = e
            _drop_session()
            if attempt < config.MAX_RETRIES:
                time.sleep(config.RETRY_BACKOFF * attempt)
    raise last


# ---- the one request shape ----------------------------------------------------------

def _call(method, path, label, params=None, json_body=None):
    """One live-map API call with a pooled token (one use charged as it is
    taken); retried over fresh tokens on 403 / 429. Returns the parsed JSON
    body."""
    def once():
        refused = None
        for _ in range(config.WAZE_TOKEN_ATTEMPTS):
            try:
                tok = tokens.pool.get()
            except (tokens.TokenUnavailable, identity_pool.MintBudgetExceeded) as e:
                raise WazeUpstreamError(f"{label}: no reCAPTCHA token ({e})")
            headers = {"x-recaptcha-token": tok.value}
            if json_body is not None:
                headers["content-type"] = "application/json; charset=UTF-8"
            try:
                resp = _session().request(method, API + path, params=params, json=json_body,
                                          headers=headers, timeout=TIMEOUT)
            except Exception as e:
                raise WazeUpstreamError(f"{label}: request failed: {type(e).__name__}: {e}")
            if resp.status_code in (403, 429):
                # 403 = scored too low (or expired): counts towards rotating
                # the mint exit; 429 = Waze says its use budget is spent
                tokens.pool.retire(tok, "refused" if resp.status_code == 403 else "spent")
                refused = resp.status_code
                continue
            return _classify(resp, label)
        raise WazeBlocked(f"{label}: {config.WAZE_TOKEN_ATTEMPTS} reCAPTCHA tokens refused (HTTP {refused})")
    return _retrying(once)


def _classify(resp, label):
    status = resp.status_code
    if status == 404:
        raise WazeNotFound(f"{label}: not found")
    if status == 400:
        raise WazeBadRequest(_message(resp) or f"{label}: HTTP 400")
    if status >= 500:
        raise WazeServerError(f"{label}: HTTP {status} {resp.text[:120]!r}", status)
    if status not in (200, 201):
        raise WazeUpstreamError(f"{label}: HTTP {status}")
    try:
        return fastjson.loads(resp.text)
    except ValueError:
        raise WazeUpstreamError(f"{label}: response is not JSON: {resp.text[:120]!r}")


def _message(resp):
    try:
        body = resp.json()
    except Exception:
        return None
    if isinstance(body, dict):
        return body.get("message") or body.get("error")
    return None


# ---- surfaces ------------------------------------------------------------------------

def fetch_georss(south, west, north, east, env, types=GEORSS_TYPES):
    """The live feed inside a bounding box -> {alerts, jams, users}."""
    params = {"top": f"{north:.6f}", "bottom": f"{south:.6f}", "left": f"{west:.6f}", "right": f"{east:.6f}",
              "env": env, "types": types}
    body = _call("GET", "/georss", f"georss {env}", params=params)
    if not isinstance(body, dict):
        raise WazeUpstreamError("georss: unexpected payload shape")
    return body


_ROUTE_ERROR_RE = re.compile(r"^\s*INTERNAL:\s*|\s*\[Details\(.*?\)\]\s*$")


def route_error(out):
    """The user-facing message of a user-drive {error, code} body (HTTP 201
    all the same): "Apologies - we can only provide routes under 3000 miles
    at this point. [...]" (code 402), "Routing Server Error" (code 501: no
    route, or the wrong cluster for these coordinates)."""
    text = _ROUTE_ERROR_RE.sub("", str(out.get("error") or "")).strip()
    code = out.get("code")
    if not text:
        text = f"routing error {code}" if code else "no route"
    return f"Waze could not route this trip: {text}" + (f" (code {code})" if code else "")


def fetch_routes(from_lat, from_lng, to_lat, to_lng, env, n_paths=3, depart_at_ms=None, arrive_at_ms=None):
    """Route alternatives -> {alternatives: [...]}. `depart_at_ms` /
    `arrive_at_ms` are epoch milliseconds (the page's startTime / endTime);
    an {error, code} body (no route, >3000 miles, wrong cluster) is a
    WazeBadRequest."""
    body = {"from": {"y": from_lat, "x": from_lng}, "to": {"y": to_lat, "x": to_lng},
            "nPaths": n_paths, "useCase": "LIVEMAP_PLANNING", "arriveAt": bool(arrive_at_ms)}
    if arrive_at_ms:
        body["endTime"] = int(arrive_at_ms)
    elif depart_at_ms:
        body["startTime"] = int(depart_at_ms)
    out = _call("POST", "/user-drive", f"routes {env}", params={"geo_env": env}, json_body=body)
    if not isinstance(out, dict):
        raise WazeUpstreamError("routes: unexpected payload shape")
    if "error" in out and "alternatives" not in out:
        raise WazeBadRequest(route_error(out))
    return out


WORLD_VIEWPORT = (-85.0, -180.0, 85.0, 180.0)
DEFAULT_LANGUAGE = "en"           # autocomplete answers "Missing autocomplete parameters" without `lang` (2026-11-07)


def fetch_autocomplete(query, env=None, near=None, viewport=None, language=None):
    """Place / address suggestions -> a list of hits. `near` = (lat, lng)
    biases the ranking (sll); `viewport` = (south, west, north, east). One
    of the two is mandatory upstream ("Missing autocomplete parameters",
    2026-11-07), so a bare query gets the whole world as its viewport."""
    params = {"q": query, "exp": AUTOCOMPLETE_EXP, "geo-env": env or "", "lang": language or DEFAULT_LANGUAGE}
    if near:
        params["sll"] = f"{near[0]:.6f},{near[1]:.6f}"
    viewport = viewport or (None if near else WORLD_VIEWPORT)
    if viewport:
        params["v"] = f"{viewport[0]:.6f},{viewport[1]:.6f};{viewport[2]:.6f},{viewport[3]:.6f}"
    body = _call("GET", "/autocomplete/", "autocomplete", params=params)
    if not isinstance(body, list):
        raise WazeUpstreamError("autocomplete: unexpected payload shape")
    return body


def fetch_venue(venue_id, language=None):
    """A Waze venue (venues.<a.b.c>) -> its record; unknown -> WazeNotFound."""
    params = {"locale": language} if language else None
    body = _call("GET", f"/venues/{venue_id}", f"venue {venue_id}", params=params)
    if not isinstance(body, dict) or not body.get("id"):
        raise WazeNotFound(f"venue {venue_id}: not found")
    return body


def fetch_google_place(google_place_id, language=None):
    """The Google place details Waze serves for a googlePlaces.<id>."""
    params = [("fields", f) for f in PLACE_FIELDS]
    if language:
        params.append(("locale", language))
    body = _call("GET", f"/places/{google_place_id}", f"place {google_place_id}", params=params)
    if not isinstance(body, dict):
        raise WazeUpstreamError("place: unexpected payload shape")
    return body


def fetch_reverse_geocode(lat, lng, env, radius=0.0005):
    """The street at a coordinate -> a list (usually one row)."""
    params = {"lat": f"{lat:.6f}", "lng": f"{lng:.6f}", "geoEnv": env, "radius": f"{radius:g}"}
    body = _call("GET", "/reverse-geocoding", "reverse-geocoding", params=params)
    return body if isinstance(body, list) else []


def fetch_timezone(lat, lng, timestamp=None):
    """The time zone at a coordinate -> {dstOffset, rawOffset, timeZoneId, timeZoneName}."""
    params = {"lat": f"{lat:.6f}", "lng": f"{lng:.6f}", "timestamp": str(int(timestamp or time.time()))}
    body = _call("GET", "/timezone", "timezone", params=params)
    return body if isinstance(body, dict) else {}
