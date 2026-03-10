"""
Nominatim (OpenStreetMap) geocoder.

Used to convert city/address strings from SerpApi events into lat/lng pairs.

Rate limit: Nominatim's policy allows 1 request per second.  We enforce this
with a module-level monotonic timer so every geocode() call across a process
respects the limit.

Cache: per-process in-memory dict keyed by lowercased address string.  For a
typical ingestion run that queries 5 cities, we make at most 5 HTTP calls.
Nominatim's own cache handles the heavy lifting after that.

Nominatim ToS: https://operations.osmfoundation.org/policies/nominatim/
"""

import logging
import time

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://nominatim.openstreetmap.org/search"
_HEADERS = {
    # Nominatim requires a descriptive User-Agent with a contact point.
    "User-Agent": "event-discovery-app/1.0 (contact@example.com)",
    "Accept-Language": "en",
}
_MIN_INTERVAL_S = 1.1  # slightly above 1 req/s to stay comfortably within policy

_cache: dict[str, tuple[float, float]] = {}
_last_call_at: float = 0.0


def geocode(address: str) -> tuple[float, float]:
    """
    Return (lat, lng) for *address*.  Falls back to (0.0, 0.0) on any error.

    Thread-safety: not guaranteed — fine for single-process Celery workers
    running one task at a time (worker_prefetch_multiplier=1).
    """
    global _last_call_at

    key = address.lower().strip()
    if not key:
        return 0.0, 0.0

    if key in _cache:
        return _cache[key]

    # Enforce rate limit
    elapsed = time.monotonic() - _last_call_at
    if elapsed < _MIN_INTERVAL_S:
        time.sleep(_MIN_INTERVAL_S - elapsed)

    try:
        _last_call_at = time.monotonic()
        resp = httpx.get(
            _BASE_URL,
            params={"q": address, "format": "json", "limit": 1},
            headers=_HEADERS,
            timeout=8.0,
        )
        resp.raise_for_status()
        results = resp.json()
        if results:
            lat = float(results[0]["lat"])
            lng = float(results[0]["lon"])
            logger.debug("Geocoded %r → (%.4f, %.4f)", address, lat, lng)
            _cache[key] = (lat, lng)
            return lat, lng
    except Exception:
        logger.debug("Geocoding failed for %r", address, exc_info=True)

    _cache[key] = (0.0, 0.0)
    return 0.0, 0.0
