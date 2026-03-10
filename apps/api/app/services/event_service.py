"""
PostgreSQL event service.

Handles upsert of UnifiedEvent → Event row, with idempotency via
ON CONFLICT (source, external_id) DO UPDATE on mutable fields only.
"""
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.normalizer import UnifiedEvent
from app.models.category import Category
from app.models.event import Event
from app.models.venue import Venue

logger = logging.getLogger(__name__)


def _has_coords(lat: float, lng: float) -> bool:
    """Return True when coordinates are a real location.

    (0.0, 0.0) is the sentinel value returned by the geocoder on failure
    and is treated as "no coordinates" to avoid inserting a bogus point in
    the Atlantic Ocean.  Any other combination is accepted as valid.
    """
    return not (lat == 0.0 and lng == 0.0)


async def _get_or_create_venue(db: AsyncSession, event: UnifiedEvent) -> uuid.UUID | None:
    if not event.venue_name:
        return None

    has_coords = _has_coords(event.lat, event.lng)
    location_wkt = f"POINT({event.lng} {event.lat})" if has_coords else None

    existing: Venue | None = None

    # Prefer Google Places ID as dedup key — it is globally unique and stable
    if event.venue_place_id:
        result = await db.execute(
            select(Venue).where(Venue.place_id == event.venue_place_id)
        )
        existing = result.scalar_one_or_none()
        if existing:
            # Opportunistically backfill any fields that were missing
            if event.venue_phone and not existing.phone:
                existing.phone = event.venue_phone
            if event.venue_website and not existing.website:
                existing.website = event.venue_website
            if has_coords and existing.location is None:
                existing.location = location_wkt
            return existing.id

    # Fall back to (name, address) match for sources without a Places ID
    if existing is None:
        result = await db.execute(
            select(Venue).where(
                Venue.name == event.venue_name,
                Venue.address == event.venue_address,
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            # Backfill any fields that were missing on the existing row
            if event.venue_place_id and not existing.place_id:
                existing.place_id = event.venue_place_id
            if event.venue_phone and not existing.phone:
                existing.phone = event.venue_phone
            if event.venue_website and not existing.website:
                existing.website = event.venue_website
            if has_coords and existing.location is None:
                existing.location = location_wkt
            return existing.id

    venue = Venue(
        name=event.venue_name,
        address=event.venue_address,
        location=location_wkt,
        place_id=event.venue_place_id,
        phone=event.venue_phone,
        website=event.venue_website,
    )
    db.add(venue)
    await db.flush()  # get ID without committing
    return venue.id


async def _get_category_id(db: AsyncSession, slug: str | None) -> int | None:
    if not slug:
        return None
    result = await db.execute(select(Category).where(Category.slug == slug))
    cat = result.scalar_one_or_none()
    return cat.id if cat else None


async def upsert(db: AsyncSession, event: UnifiedEvent) -> tuple[str, bool]:
    """
    Upsert a UnifiedEvent into the `events` table.

    Returns (event_id_str, is_new) where `is_new` is True if the row was
    INSERTed (vs. updated).

    Uses PostgreSQL INSERT … ON CONFLICT DO UPDATE so it is safe to call
    multiple times with the same (source, external_id) pair.
    """
    venue_id = await _get_or_create_venue(db, event)
    category_id = await _get_category_id(db, event.category_slug)

    # Check idempotency: skip expensive embedding if row already exists
    existing_stmt = select(Event.id, Event.embedding_id).where(
        Event.source == event.source,
        Event.external_id == event.external_id,
    )
    existing_result = await db.execute(existing_stmt)
    existing_row = existing_result.first()
    is_new = existing_row is None

    canonical_uuid = uuid.UUID(event.canonical_id) if event.canonical_id else None

    # Build the upsert values dict
    values: dict = {
        "source": event.source,
        "external_id": event.external_id,
        "title": event.title,
        "description": event.description,
        "start_at": event.start_at,
        "end_at": event.end_at,
        "venue_id": venue_id,
        "category_id": category_id,
        "ticket_url": event.ticket_url,
        "ticket_links": event.ticket_links,
        "price_min": event.price_min,
        "price_max": event.price_max,
        "image_url": event.image_url,
        "raw_payload": event.raw_payload,
        "canonical_id": canonical_uuid,
    }

    stmt = (
        insert(Event)
        .values(id=uuid.uuid4(), **values)
        .on_conflict_do_update(
            index_elements=["source", "external_id"],
            # Only update mutable fields; never overwrite id/created_at
            set_={
                k: v
                for k, v in values.items()
                if k not in ("source", "external_id")
            },
        )
        .returning(Event.id)
    )

    result = await db.execute(stmt)
    row = result.first()
    event_id = str(row[0]) if row else str(uuid.uuid4())

    if is_new:
        logger.debug("Inserted new event %s/%s → %s", event.source, event.external_id, event_id)
    else:
        logger.debug("Updated event %s/%s", event.source, event.external_id)

    return event_id, is_new


async def update_embedding_id(
    db: AsyncSession, source: str, external_id: str, embedding_id: str
) -> None:
    """Store the Qdrant point ID back on the event row after embedding."""
    stmt = (
        insert(Event)
        .values(source=source, external_id=external_id, embedding_id=embedding_id)
        .on_conflict_do_update(
            index_elements=["source", "external_id"],
            set_={"embedding_id": embedding_id},
        )
    )
    await db.execute(stmt)
