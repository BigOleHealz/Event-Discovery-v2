"""
Geocoder — Nominatim (OpenStreetMap) with Google Geocoding API fallback.

Strategy (first success wins):
  1. Nominatim — free, no API key, 1 req/s limit.
  2. Google Geocoding API — used when Nominatim is unavailable (403/timeout),
     which happens in server/container environments that Nominatim IP-blocks.
     Requires GOOGLE_PLACES_API_KEY (same key as Places API).

Cache: per-process in-memory dict keyed by lowercased address string so a
typical ingestion run makes at most N unique HTTP calls across all events
sharing the same venue city/address.

Nominatim ToS: https://operations.osmfoundation.org/policies/nominatim/
Google Geocoding pricing: $0.005 / request (Geocoding SKU).
"""

import logging
import time

import httpx

logger = logging.getLogger(__name__)

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_NOMINATIM_HEADERS = {
    "User-Agent": "event-discovery-app/1.0 (contact@example.com)",
    "Accept-Language": "en",
}
_GOOGLE_GEOCODING_URL = "https://maps.googleapis.com/maps/api/geocode/json"

_MIN_NOMINATIM_INTERVAL_S = 1.1  # Nominatim policy: max 1 req/s

_cache: dict[str, tuple[float, float]] = {}
_last_nominatim_call_at: float = 0.0


def geocode(address: str) -> tuple[float, float]:
    """
    Return (lat, lng) for *address*.  Falls back to (0.0, 0.0) on total failure.

    Tries Nominatim first, then Google Geocoding API if Nominatim is blocked or
    returns no results.  Results are cached per process.

    Thread-safety: not guaranteed — fine for single-process Celery workers
    running one task at a time (worker_prefetch_multiplier=1).
    """
    key = address.lower().strip()
    if not key:
        return 0.0, 0.0

    if key in _cache:
        return _cache[key]

    result = _nominatim(address) or _google_geocoding(address)
    coords = result if result else (0.0, 0.0)
    _cache[key] = coords
    return coords


# ---------------------------------------------------------------------------
# Nominatim
# ---------------------------------------------------------------------------

def _nominatim(address: str) -> tuple[float, float] | None:
    global _last_nominatim_call_at

    elapsed = time.monotonic() - _last_nominatim_call_at
    if elapsed < _MIN_NOMINATIM_INTERVAL_S:
        time.sleep(_MIN_NOMINATIM_INTERVAL_S - elapsed)

    try:
        _last_nominatim_call_at = time.monotonic()
        resp = httpx.get(
            _NOMINATIM_URL,
            params={"q": address, "format": "json", "limit": 1},
            headers=_NOMINATIM_HEADERS,
            timeout=8.0,
        )
        resp.raise_for_status()
        results = resp.json()
        if results:
            lat = float(results[0]["lat"])
            lng = float(results[0]["lon"])
            if lat != 0.0 or lng != 0.0:
                logger.debug("Nominatim geocoded %r → (%.5f, %.5f)", address, lat, lng)
                return lat, lng
    except Exception:
        logger.debug("Nominatim unavailable for %r, will try Google", address, exc_info=True)

    return None


# ---------------------------------------------------------------------------
# Google Geocoding API fallback
# ---------------------------------------------------------------------------

def _google_geocoding(address: str) -> tuple[float, float] | None:
    from app.config import settings  # lazy import to avoid circular deps

    api_key = settings.google_places_api_key
    if not api_key:
        return None

    try:
        resp = httpx.get(
            _GOOGLE_GEOCODING_URL,
            params={"address": address, "key": api_key},
            timeout=8.0,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "OK" and data.get("results"):
            loc = data["results"][0]["geometry"]["location"]
            lat, lng = float(loc["lat"]), float(loc["lng"])
            if lat != 0.0 or lng != 0.0:
                logger.debug("Google geocoded %r → (%.5f, %.5f)", address, lat, lng)
                return lat, lng
        logger.debug("Google Geocoding status=%s for %r", data.get("status"), address)
    except Exception:
        logger.debug("Google Geocoding failed for %r", address, exc_info=True)

    return None
