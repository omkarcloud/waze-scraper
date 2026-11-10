"""Use the scraper straight from Python — no server needed.

    python main.py

Every function returns the same JSON the API does; results are written to
output/*.json. Params are the API's own query params, validated by the same
schemas (waze/schemas.py).
"""
import json
import os

from waze import api, schemas

os.makedirs("output", exist_ok=True)


def save(name, data):
    path = os.path.join("output", name)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"saved {path}")


def call(schema, fn, **params):
    """Validate the params like the API does, then call the endpoint function."""
    return fn(**schema().load(params))


if __name__ == "__main__":
    # live traffic jams within 5 km of Times Square, worst first
    save("jams_times_square.json", call(schemas.JamsSearchSchema, api.jams_search,
                                        location="40.758,-73.9855"))

    # up to 3 routes from Times Square to JFK Airport, with live ETA and tolls
    save("routes_times_square_to_jfk.json", call(schemas.RoutesSearchSchema, api.routes_search,
                                                 **{"from": "40.758,-73.9855", "to": "40.6413,-73.7781"}))

    # a Waze place: address, phone, opening hours, services and photos
    save("place_empire_state_building.json", call(schemas.PlaceDetailsSchema, api.place_details,
                                                  place="187433367.1874399211.868693"))
