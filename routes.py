"""The 6 Waze endpoints. Every path is served with and without the `/waze`
prefix, so code generated against the hosted API on RapidAPI (paths like
/jams/search) runs unchanged against this server.

Params are validated by the same schemas the hosted API uses
(waze/schemas.py): a bad or unknown param is a 400 that names the field."""
import json

from bottle import request, response, route

from schema_fields import load_query
from waze import api, schemas
from waze.fetch import WazeBadRequest, WazeNotFound


def json_response(data, status=200):
    response.status = status
    response.content_type = "application/json"
    return json.dumps(data, ensure_ascii=False)


def query_dict():
    """The query as unicode strings (bottle 0.12's .get() hands back latin-1
    decoded bytes, so a UTF-8 "Zürich" would arrive as "ZÃ¼rich")."""
    return {key: request.query.getunicode(key) for key in request.query.keys()}


def call(label, schema, fn):
    """Validate the query with `schema`, run `fn(**params)` and map errors:
    bad params -> 400, unknown place -> 404, Waze failures -> 500."""
    data, error = load_query(schema, query_dict())
    if error:
        return json_response(error, 400)
    try:
        return json_response(fn(**data))
    except ValueError as e:                # bad id / params
        return json_response({"error": str(e)}, 400)
    except WazeBadRequest as e:            # Waze rejected the request (e.g. no route)
        return json_response({"error": f"waze rejected the request: {e}"}, 400)
    except WazeNotFound as e:
        return json_response({"error": str(e) or "not found"}, 404)
    except Exception as e:                 # retries exhausted / no token
        return json_response({"error": f"waze {label} failed: {e}"}, 500)


ENDPOINTS = [
    ("/jams/search", schemas.JamsSearchSchema, api.jams_search),
    ("/alerts/search", schemas.AlertsSearchSchema, api.alerts_search),
    ("/routes/search", schemas.RoutesSearchSchema, api.routes_search),
    ("/places/search", schemas.PlacesSearchSchema, api.places_search),
    ("/places/details", schemas.PlaceDetailsSchema, api.place_details),
    ("/geocode/reverse", schemas.ReverseGeocodeSchema, api.geocode_reverse),
]


def mount(path, schema, fn):
    """Serve one endpoint at /path and /waze/path."""
    label = path.strip("/")

    def handler():
        return call(label, schema, fn)
    handler.__name__ = "waze_" + label.replace("/", "_").replace("-", "_")
    route(path, method="GET")(handler)
    route("/waze" + path, method="GET")(handler)


for _path, _schema, _fn in ENDPOINTS:
    mount(_path, _schema, _fn)


@route("/", method="GET")
@route("/health", method="GET")
def health():
    return json_response({"status": "ok", "endpoints": [p for p, _, _ in ENDPOINTS]})
