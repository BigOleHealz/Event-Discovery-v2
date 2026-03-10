import uuid

import pytest
from geoalchemy2 import WKTElement
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Venue


# San Francisco coordinates
SF_LNG, SF_LAT = -122.4194, 37.7749


def _point(lng: float, lat: float) -> WKTElement:
    return WKTElement(f"POINT({lng} {lat})", srid=4326)


def _make_venue(**overrides: object) -> Venue:
    defaults: dict[str, object] = {
        "name": f"Venue {uuid.uuid4().hex[:6]}",
        "location": _point(SF_LNG, SF_LAT),
    }
    defaults.update(overrides)
    return Venue(**defaults)  # type: ignore[arg-type]


async def test_create_venue(db_session: AsyncSession) -> None:
    venue = _make_venue(name="The Fillmore", address="1805 Geary Blvd, SF")
    db_session.add(venue)
    await db_session.flush()

    assert venue.id is not None
    assert venue.name == "The Fillmore"
    assert venue.address == "1805 Geary Blvd, SF"
    assert venue.created_at is not None


async def test_venue_repr(db_session: AsyncSession) -> None:
    venue = _make_venue(name="Repr Venue")
    db_session.add(venue)
    await db_session.flush()
    assert "Repr Venue" in repr(venue)


async def test_venue_location_stored(db_session: AsyncSession) -> None:
    """location column should persist as a geography value."""
    venue = _make_venue(location=_point(-73.9857, 40.7484))  # NYC
    db_session.add(venue)
    await db_session.flush()

    # location is non-null after flush
    assert venue.location is not None


async def test_venue_optional_fields(db_session: AsyncSession) -> None:
    venue = _make_venue()  # no address, no place_id
    db_session.add(venue)
    await db_session.flush()

    assert venue.address is None
    assert venue.place_id is None


async def test_venue_location_nullable(db_session: AsyncSession) -> None:
    """location is nullable — venues without coordinates are allowed."""
    venue = Venue(name="No Location")  # type: ignore[call-arg]
    db_session.add(venue)
    await db_session.flush()
    assert venue.location is None


async def test_venue_phone_website_stored(db_session: AsyncSession) -> None:
    """phone and website columns round-trip correctly."""
    venue = _make_venue(phone="+1 512-555-0100", website="https://example.com")
    db_session.add(venue)
    await db_session.flush()

    assert venue.phone == "+1 512-555-0100"
    assert venue.website == "https://example.com"
