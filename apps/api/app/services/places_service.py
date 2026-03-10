"""Google Places API venue enrichment.

Converts a raw SerpApi venue into a standardised PlaceInfo by calling the
Google Place Details or Find Place endpoints.

CID resolution strategy (tried in order, first win used)
---------------------------------------------------------
1. ``event_location_map.link`` — the Google Maps URL embeds a hex Feature ID
   like ``!1s0x8644b1316e46d40b:0x8c69c5b81db8e737`` in its path.  The
   **second** hex segment is the venue's CID.  Converting it to decimal gives
   the same ``ludocid`` value that appears in ``venue.link`` — but this source
   is more reliably present.

2. ``venue.link`` ``ludocid`` query parameter — parsed as a decimal string
   directly from the URL.

3. Name-based fallback — Find Place from Text with the venue name + city hint,
   then a Details call for the returned place_id (two round-trips).

Returns ``None`` (no exception raised) when the API key is absent, the
request fails, or Google returns no match.
"""

import logging
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import httpx

logger = logging.getLogger(__name__)

_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
_FIND_PLACE_URL = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"

# Fields billed at "Basic" + "Contact" tiers — omit expensive "Atmosphere"
# fields (reviews, photos) to keep per-call cost minimal.
_DETAILS_FIELDS = (
    "place_id,name,formatted_address,geometry,"
    "international_phone_number,website,rating"
)


@dataclass
class PlaceInfo:
    """Standardised venue record returned by Google Places."""

    place_id: str
    name: str
    formatted_address: str
    lat: float
    lng: float
    phone: str | None = None
    website: str | None = None
    rating: float | None = None


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def extract_cid_from_maps_link(maps_link: str | None) -> str | None:
    """Extract the CID (decimal) from an ``event_location_map.link`` URL.

    Google Maps URLs embed the venue's hex Feature ID in the path::

        https://www.google.com/maps/place//data=!4m2!3m1!1s0x8644b1316e46d40b:0x8c69c5b81db8e737?...

    The Feature ID has the form ``{place_hex}:{cid_hex}``.  The second segment
    (``0x8c69c5b81db8e737``) is the CID.  Converting it to a decimal string
    yields the same value as the ``ludocid`` query parameter on ``venue.link``.

    Returns the CID as a decimal string, or ``None`` if not found.
    """
    if not maps_link:
        return None
    # Match "!1s0x<hex>:0x<hex>" anywhere in the URL (path or query string)
    match = re.search(r"!1s(0x[0-9a-f]+):(0x[0-9a-f]+)", maps_link, re.IGNORECASE)
    if not match:
        return None
    cid_hex = match.group(2)
    return str(int(cid_hex, 16))


def extract_ludocid(venue_link: str | None) -> str | None:
    """Parse the ``ludocid`` query parameter from a Google Search venue URL.

    Example input::

        https://www.google.com/search?...&ludocid=10117835432427841335&...

    Returns the raw string value, or ``None`` if the parameter is absent.
    """
    if not venue_link:
        return None
    qs = parse_qs(urlparse(venue_link).query)
    values = qs.get("ludocid")
    return values[0] if values else None


def lookup_venue(
    venue_link: str | None,
    venue_name: str | None,
    city: str | None,
    api_key: str,
    maps_link: str | None = None,
) -> PlaceInfo | None:
    """Best-effort venue lookup using the Google Places API.

    Parameters
    ----------
    venue_link:
        The ``venue.link`` value from a SerpApi event.  Used as a fallback
        source for the ``ludocid`` query parameter.
    venue_name:
        Display name of the venue, used as text-search fallback input.
    city:
        City/region hint (e.g. "Austin, TX") used to bias the text search.
    api_key:
        Google Places API key.  An empty string disables all calls.
    maps_link:
        The ``event_location_map.link`` URL.  When present, the hex CID
        embedded in its path is the most reliable source for the venue CID
        and is tried first.
    """
    if not api_key:
        return None

    cid = _resolve_cid(maps_link=maps_link, venue_link=venue_link)
    if cid:
        info = _lookup_by_cid(cid, api_key)
        if info:
            return info

    if venue_name:
        return _lookup_by_name(venue_name, city or "", api_key)

    return None


# ---------------------------------------------------------------------------
# Internal implementation
# ---------------------------------------------------------------------------


def _resolve_cid(maps_link: str | None, venue_link: str | None) -> str | None:
    """Return the best available CID string, preferring the maps-link source."""
    cid = extract_cid_from_maps_link(maps_link)
    if cid:
        return cid
    return extract_ludocid(venue_link)


def _lookup_by_cid(ludocid: str, api_key: str) -> PlaceInfo | None:
    """Place Details call using the CID (ludocid) as place_id."""
    try:
        resp = httpx.get(
            _DETAILS_URL,
            params={
                "place_id": f"cid:{ludocid}",
                "fields": _DETAILS_FIELDS,
                "key": api_key,
            },
            timeout=8.0,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "OK":
            return _parse_details_response(data)
        logger.debug(
            "Places CID lookup status=%s for ludocid=%s", data.get("status"), ludocid
        )
    except Exception:
        logger.debug("Places CID lookup failed for ludocid=%s", ludocid, exc_info=True)
    return None


def _lookup_by_name(name: str, location_hint: str, api_key: str) -> PlaceInfo | None:
    """Find Place from Text → Place Details (two-call fallback)."""
    try:
        resp = httpx.get(
            _FIND_PLACE_URL,
            params={
                "input": f"{name} {location_hint}".strip(),
                "inputtype": "textquery",
                "fields": "place_id",
                "key": api_key,
            },
            timeout=8.0,
        )
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates") or []
        if not candidates:
            logger.debug("Places Find Place returned no candidates for %r", name)
            return None
        place_id = candidates[0].get("place_id")
        if not place_id:
            return None
        return _get_details(place_id, api_key)
    except Exception:
        logger.debug("Places name lookup failed for %r", name, exc_info=True)
    return None


def _get_details(place_id: str, api_key: str) -> PlaceInfo | None:
    """Fetch full Place Details for a known place_id string."""
    try:
        resp = httpx.get(
            _DETAILS_URL,
            params={
                "place_id": place_id,
                "fields": _DETAILS_FIELDS,
                "key": api_key,
            },
            timeout=8.0,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "OK":
            return _parse_details_response(data)
        logger.debug(
            "Places Details status=%s for place_id=%s", data.get("status"), place_id
        )
    except Exception:
        logger.debug("Places Details failed for place_id=%s", place_id, exc_info=True)
    return None


def _parse_details_response(data: dict) -> PlaceInfo | None:
    """Convert a raw Place Details API response body into a ``PlaceInfo``."""
    result = data.get("result") or {}
    if not result:
        return None

    geo = (result.get("geometry") or {}).get("location") or {}
    lat = geo.get("lat")
    lng = geo.get("lng")
    if lat is None or lng is None:
        return None

    place_id = result.get("place_id")
    if not place_id:
        return None

    rating_raw = result.get("rating")

    return PlaceInfo(
        place_id=place_id,
        name=result.get("name") or "",
        formatted_address=result.get("formatted_address") or "",
        lat=float(lat),
        lng=float(lng),
        phone=result.get("international_phone_number") or None,
        website=result.get("website") or None,
        rating=float(rating_raw) if rating_raw is not None else None,
    )
