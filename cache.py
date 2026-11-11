"""A tiny in-memory response cache with the call shape the waze module uses.
Results live in this process only and expire after `ttl` (a
datetime.timedelta): the live traffic feed for an area is kept 2 minutes and
shared by /jams/search and /alerts/search, places and geocoding for hours.
Entries are kept as JSON, so every hit is a fresh copy the caller may change
freely.
"""
import json
import threading
import time

_store = {}
_lock = threading.Lock()
MAX_ENTRIES = 2000


class DontCache:
    """Wrap a result to return it without keeping it (an answer that is valid
    but known to be transient)."""

    def __init__(self, data):
        self.data = data


def _key(namespace, data):
    return namespace + ":" + json.dumps(data, sort_keys=True, default=str)


def cached_call(namespace, data, fn, ttl, cache=True, refresh=False):
    """Serve fn(**data) from memory for `ttl`, else run it and keep the result.
    Exceptions from fn propagate uncached; a DontCache result is returned
    unwrapped and not kept."""
    if not (cache and ttl):
        result = fn(**data)
        return result.data if isinstance(result, DontCache) else result
    key = _key(namespace, data)
    now = time.time()
    if not refresh:
        with _lock:
            hit = _store.get(key)
        if hit and hit[0] > now:
            return json.loads(hit[1])
    result = fn(**data)
    if isinstance(result, DontCache):
        return result.data
    frozen = json.dumps(result, default=str)
    with _lock:
        if len(_store) >= MAX_ENTRIES:
            for stale in [k for k, (expires, _) in _store.items() if expires <= now] or list(_store)[:100]:
                _store.pop(stale, None)
        _store[key] = (now + ttl.total_seconds(), frozen)
    return result
