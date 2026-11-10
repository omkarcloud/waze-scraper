"""Cache TTL per /waze/* endpoint (cache.py, keyed on the validated params).
The live feed moves by the minute, so its rows are short; places and
geocoding are static. alerts/search and jams/search share ONE cached feed
fetch per bounding box (api._feed), so both on the same area cost one
upstream call (and one token use)."""
from datetime import timedelta

# One row per bounding box: the parsed georss feed (alerts + jams); Waze's
# own map refreshes it every ~2 min.
FEED_CACHE = timedelta(minutes=2)
# Routes carry live ETAs; a planned (future) trip is just as volatile.
ROUTES_CACHE = timedelta(minutes=5)
# Autocomplete suggestions: venues are added slowly.
PLACES_SEARCH_CACHE = timedelta(hours=12)
# A venue / Google place record: hours and phone change rarely.
PLACE_CACHE = timedelta(hours=24)
# Streets and time zones do not move.
REVERSE_CACHE = timedelta(hours=24)
