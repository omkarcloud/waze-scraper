"""Marshmallow request schemas for every /waze/* route.

ONE param per input (tripadvisor QueryOrIdField convention, never a sibling
`url`/`id` pair); the resolvers live in waze/refs.py:

    location    "lat,lng" | "south,west,north,east" | a live-map link
    from / to   "lat,lng" | a Waze venue id | a Google place id | a link
    place       a Waze venue id | a Google place id | a live-map / ul link
    depart_at / arrive_at   ISO 8601 (2026-11-08T17:30, 2026-11-08T17:30:00Z,
                            2026-11-08T17:30+02:00); no zone = UTC
"""
from datetime import datetime, timedelta, timezone

from marshmallow import ValidationError, fields, validate, validates_schema

from schema_fields import (BaseSchema, CommaListField, LanguageCodeField, LimitField, PositiveInt,
                           QueryField, RefField, StrippedString)
from waze import parsers, refs

MAX_RADIUS_KM = 50
MAX_ROUTES = 3            # Waze serves three alternatives at most (nPaths=5 answers 3)
MAX_ALERTS = 1000
PLAN_WINDOW_DAYS = 30     # how far ahead a route may be planned


class LocationField(RefField):
    resolver = staticmethod(refs.resolve_location)


class PointField(RefField):
    resolver = staticmethod(refs.resolve_point)


class PlaceField(RefField):
    resolver = staticmethod(refs.resolve_place)


class DateTimeField(StrippedString):
    """ISO 8601 date-time -> epoch milliseconds (UTC when no zone given)."""

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        if value is None:
            return None
        text = value.replace(" ", "T")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            raise ValidationError("Must be an ISO 8601 date-time (2026-11-08T17:30 or 2026-11-08T17:30:00Z).")
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if parsed < now - timedelta(hours=1):
            raise ValidationError("Must not be in the past.")
        if parsed > now + timedelta(days=PLAN_WINDOW_DAYS):
            raise ValidationError(f"Must be within the next {PLAN_WINDOW_DAYS} days.")
        return int(parsed.timestamp() * 1000)


class RadiusField(fields.Float):
    def __init__(self, **kwargs):
        kwargs.setdefault("load_default", 5.0)
        kwargs.setdefault("validate", validate.Range(min=0.1, max=MAX_RADIUS_KM))
        super().__init__(**kwargs)


_LOCATION_DOC = ("lat,lng (e.g. 40.758,-73.985), a bounding box south,west,north,east, "
                 "or a waze.com live-map link")
_RADIUS_DOC = f"search radius in km around a lat,lng location (ignored for a bounding box), default 5, max {MAX_RADIUS_KM}"


class AlertsSearchSchema(BaseSchema):
    location = LocationField(metadata={"description": _LOCATION_DOC})
    radius_km = RadiusField(metadata={"description": _RADIUS_DOC})
    types = CommaListField(allowed=parsers.ALERT_TYPES, metadata={
        "description": "only these alert types, comma-separated: " + ", ".join(parsers.ALERT_TYPES)})
    limit = LimitField(default=200, max_size=MAX_ALERTS, metadata={
        "description": f"maximum alerts to return (newest first), default 200, max {MAX_ALERTS}"})


class JamsSearchSchema(BaseSchema):
    location = LocationField(metadata={"description": _LOCATION_DOC})
    radius_km = RadiusField(metadata={"description": _RADIUS_DOC})
    min_level = fields.Integer(load_default=0, validate=validate.Range(min=0, max=5), metadata={
        "description": "only jams at this Waze level or worse: 0 free flow .. 5 standstill (default 0)"})
    limit = LimitField(default=200, max_size=MAX_ALERTS, metadata={
        "description": f"maximum jams to return (worst first), default 200, max {MAX_ALERTS}"})


class RoutesSearchSchema(BaseSchema):
    from_ = PointField(data_key="from", metadata={
        "description": "start: lat,lng, a Waze venue id, a Google place id, or a waze.com link"})
    to = PointField(metadata={
        "description": "destination: lat,lng, a Waze venue id, a Google place id, or a waze.com link"})
    depart_at = DateTimeField(metadata={
        "description": "plan for a departure at this ISO 8601 date-time (UTC when no zone); default now"})
    arrive_at = DateTimeField(metadata={
        "description": "plan to arrive by this ISO 8601 date-time (UTC when no zone); not with depart_at"})
    max_routes = PositiveInt(max_value=MAX_ROUTES, load_default=MAX_ROUTES, metadata={
        "description": f"alternatives to return, 1-{MAX_ROUTES} (default {MAX_ROUTES})"})

    @validates_schema
    def _one_time(self, data, **kwargs):
        if data.get("depart_at") and data.get("arrive_at"):
            raise ValidationError("Give depart_at or arrive_at, not both.", "arrive_at")


class PlacesSearchSchema(BaseSchema):
    query = QueryField(metadata={"description": "place name, business, landmark or address to look up"})
    near = LocationField(required=False, load_default=None, metadata={
        "description": "rank results around this lat,lng (or inside a bounding box south,west,north,east); "
                       "also picks the Waze region"})
    language = LanguageCodeField(allow_locale=False, metadata={
        "description": "ISO 639-1 language for names and addresses (en, fr, es, he, ...)"})
    limit = LimitField(default=10, max_size=20, metadata={"description": "maximum suggestions (default 10, max 20)"})


class PlaceDetailsSchema(BaseSchema):
    place = PlaceField(metadata={
        "description": "a Waze venue id (venues.187433368.1874464748.1166515), a Google place id "
                       "(ChIJmQJIxlVYwokRLgeuocVOGVU) or a waze.com place link"})
    language = LanguageCodeField(allow_locale=False, metadata={
        "description": "ISO 639-1 language for the address and opening hours (en, fr, es, he, ...)"})


class ReverseGeocodeSchema(BaseSchema):
    location = LocationField(metadata={
        "description": "lat,lng (e.g. 40.758,-73.985) or a waze.com live-map link; a bounding box uses its centre"})
