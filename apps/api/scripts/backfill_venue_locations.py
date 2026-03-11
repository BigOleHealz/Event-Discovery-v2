"""
Backfill location (PostGIS geography point) for venue rows that have a NULL
location column.

Strategy per venue (first success wins):
  1. Google Places API — Find Place from Text using "{name} {address}"
     → also back-fills place_id, phone, website
  2. Nominatim (OpenStreetMap) — geocode the full address string

Run from apps/api/:
    uv run python scripts/backfill_venue_locations.py [--dry-run] [--limit N]
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Ensure the app package is importable when running this script directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from sqlalchemy import select, text

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.venue import Venue

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
_FIND_PLACE_URL = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
_GEOCODING_URL = "https://maps.googleapis.com/maps/api/geocode/json"
_DETAILS_FIELDS = "place_id,name,formatted_address,geometry,international_phone_number,website"

# Stats
_stats = {"places_ok": 0, "nominatim_ok": 0, "failed": 0, "skipped": 0}


# ---------------------------------------------------------------------------
# Places API helpers
# ---------------------------------------------------------------------------

def _places_find_and_detail(name: str, address: str | None, api_key: str) -> dict | None:
    """Find Place from Text → Place Details. Returns raw result dict or None."""
    addr = address or ""
    # Avoid duplicating the name when the address already starts with it
    # e.g. "The Moroccan Lounge, 901 1st St, Los Angeles, CA" already contains the name
    if addr.lower().startswith(name.lower()):
        query = addr
    else:
        query = f"{name} {addr}".strip()
    try:
        resp = httpx.get(
            _FIND_PLACE_URL,
            params={
                "input": query,
                "inputtype": "textquery",
                "fields": "place_id",
                "key": api_key,
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        candidates = resp.json().get("candidates") or []
        if not candidates:
            logger.debug("  Places: no candidates for %r", query)
            return None
        place_id = candidates[0].get("place_id")
        if not place_id:
            return None
    except Exception as exc:
        logger.debug("  Places Find Place failed: %s", exc)
        return None

    try:
        resp = httpx.get(
            _DETAILS_URL,
            params={
                "place_id": place_id,
                "fields": _DETAILS_FIELDS,
                "key": api_key,
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "OK":
            return data.get("result") or {}
        logger.debug("  Places Details status=%s for place_id=%s", data.get("status"), place_id)
    except Exception as exc:
        logger.debug("  Places Details failed: %s", exc)

    return None


# ---------------------------------------------------------------------------
# Google Geocoding API fallback
# ---------------------------------------------------------------------------

def _google_geocode(address: str, api_key: str) -> tuple[float, float] | None:
    """Geocode an address string via the Google Geocoding API.

    Preferred over Nominatim for this backfill because:
    - same API key as Places (no extra setup)
    - no IP-based rate restrictions that block server environments
    - 1 request per venue, billed at $0.005/call (Geocoding SKU)
    """
    try:
        resp = httpx.get(
            _GEOCODING_URL,
            params={"address": address, "key": api_key},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "OK":
            loc = data["results"][0]["geometry"]["location"]
            lat, lng = float(loc["lat"]), float(loc["lng"])
            if lat != 0.0 or lng != 0.0:
                return lat, lng
        logger.debug("  Geocoding status=%s for %r", data.get("status"), address)
    except Exception as exc:
        logger.debug("  Google Geocoding failed for %r: %s", address, exc)

    return None


# ---------------------------------------------------------------------------
# Main backfill logic
# ---------------------------------------------------------------------------

async def backfill(dry_run: bool = False, limit: int | None = None) -> None:
    api_key = settings.google_places_api_key
    if not api_key:
        logger.warning("GOOGLE_PLACES_API_KEY not set — Places API lookups will be skipped")

    async with AsyncSessionLocal() as db:
        stmt = select(Venue).where(Venue.location.is_(None))
        if limit:
            stmt = stmt.limit(limit)
        result = await db.execute(stmt)
        venues: list[Venue] = list(result.scalars().all())

    logger.info("Found %d venues with null location", len(venues))

    async with AsyncSessionLocal() as db:
        for i, venue in enumerate(venues, 1):
            logger.info("[%d/%d] %s — %s", i, len(venues), venue.name, venue.address)

            lat = lng = None
            new_place_id = None
            new_phone = None
            new_website = None

            # --- Strategy 1: Google Places API ---
            if api_key:
                place_result = _places_find_and_detail(venue.name, venue.address, api_key)
                if place_result:
                    geo = (place_result.get("geometry") or {}).get("location") or {}
                    if geo.get("lat") is not None and geo.get("lng") is not None:
                        lat = float(geo["lat"])
                        lng = float(geo["lng"])
                        new_place_id = place_result.get("place_id") or venue.place_id
                        new_phone = place_result.get("international_phone_number") or venue.phone
                        new_website = place_result.get("website") or venue.website
                        logger.info("  ✓ Places API → (%.5f, %.5f)", lat, lng)
                        _stats["places_ok"] += 1

            # --- Strategy 2: Google Geocoding API ---
            if lat is None and venue.address and api_key:
                coords = _google_geocode(venue.address, api_key)
                if coords:
                    lat, lng = coords
                    logger.info("  ✓ Geocoding API → (%.5f, %.5f)", lat, lng)
                    _stats["nominatim_ok"] += 1
                else:
                    logger.warning("  ✗ Both strategies failed for %r", venue.address)
                    _stats["failed"] += 1
                    continue

            if lat is None:
                logger.warning("  ✗ No coordinates found")
                _stats["failed"] += 1
                continue

            if dry_run:
                logger.info("  [dry-run] would set location=POINT(%.5f %.5f)", lng, lat)
                _stats["skipped"] += 1
                continue

            # Update the venue row
            location_wkt = f"POINT({lng} {lat})"
            update_vals: dict = {"location": text(f"ST_GeogFromText('{location_wkt}')")}
            if new_place_id:
                update_vals["place_id"] = new_place_id
            if new_phone:
                update_vals["phone"] = new_phone
            if new_website:
                update_vals["website"] = new_website

            # Use raw SQL for the PostGIS geography assignment
            await db.execute(
                text(
                    "UPDATE venues SET "
                    "location = ST_GeogFromText(:wkt), "
                    "place_id = COALESCE(:place_id, place_id), "
                    "phone = COALESCE(:phone, phone), "
                    "website = COALESCE(:website, website) "
                    "WHERE id = :id"
                ),
                {
                    "wkt": location_wkt,
                    "place_id": new_place_id,
                    "phone": new_phone,
                    "website": new_website,
                    "id": str(venue.id),
                },
            )

        await db.commit()

    logger.info(
        "\nDone. Places API: %d  Geocoding API: %d  Failed: %d  Dry-run skipped: %d",
        _stats["places_ok"],
        _stats["nominatim_ok"],
        _stats["failed"],
        _stats["skipped"],
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill null venue locations")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be updated without writing")
    parser.add_argument("--limit", type=int, default=None, help="Only process N venues (for testing)")
    args = parser.parse_args()

    asyncio.run(backfill(dry_run=args.dry_run, limit=args.limit))
