import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from geoalchemy2 import WKTElement
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, Event, Venue


_START = datetime(2026, 6, 15, 19, 0, tzinfo=timezone.utc)
_END = datetime(2026, 6, 15, 22, 0, tzinfo=timezone.utc)


def _make_event(**overrides: object) -> Event:
    defaults: dict[str, object] = {
        "title": f"Event {uuid.uuid4().hex[:6]}",
        "start_at": _START,
    }
    defaults.update(overrides)
    return Event(**defaults)  # type: ignore[arg-type]


def _make_venue(db_session: AsyncSession) -> Venue:
    v = Venue(
        name=f"Venue {uuid.uuid4().hex[:6]}",
        location=WKTElement("POINT(-122.4194 37.7749)", srid=4326),
    )
    db_session.add(v)
    return v


async def test_create_minimal_event(db_session: AsyncSession) -> None:
    event = _make_event()
    db_session.add(event)
    await db_session.flush()

    assert event.id is not None
    assert event.title.startswith("Event ")
    assert event.start_at == _START
    assert event.created_at is not None


async def test_event_repr(db_session: AsyncSession) -> None:
    event = _make_event(title="Jazz Night")
    db_session.add(event)
    await db_session.flush()
    assert "Jazz Night" in repr(event)


async def test_event_all_fields(db_session: AsyncSession) -> None:
    venue = _make_venue(db_session)
    await db_session.flush()

    category = Category(slug=f"music-{uuid.uuid4().hex[:4]}", label="Music")
    db_session.add(category)
    await db_session.flush()

    event = _make_event(
        title="Full Event",
        description="A complete event",
        end_at=_END,
        source="eventbrite",
        external_id=f"eb-{uuid.uuid4().hex}",
        venue_id=venue.id,
        category_id=category.id,
        ticket_url="https://eventbrite.com/e/123",
        price_min=Decimal("0.00"),
        price_max=Decimal("25.00"),
        image_url="https://example.com/image.jpg",
        raw_payload={"original": "data"},
        embedding_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "eventbrite:eb-123")),
    )
    db_session.add(event)
    await db_session.flush()

    assert event.venue_id == venue.id
    assert event.category_id == category.id
    assert event.price_min == Decimal("0.00")
    assert event.price_max == Decimal("25.00")
    assert event.raw_payload == {"original": "data"}


async def test_event_source_external_id_unique(db_session: AsyncSession) -> None:
    source, ext_id = "eventbrite", f"eb-{uuid.uuid4().hex}"
    db_session.add(_make_event(source=source, external_id=ext_id))
    await db_session.flush()

    db_session.add(_make_event(source=source, external_id=ext_id))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_event_canonical_self_reference(db_session: AsyncSession) -> None:
    """canonical_id should point to another event (the dedup winner)."""
    canonical = _make_event(title="Canonical Event", source="meetup", external_id=f"m-{uuid.uuid4().hex}")
    db_session.add(canonical)
    await db_session.flush()

    duplicate = _make_event(
        title="Duplicate Event",
        source="eventbrite",
        external_id=f"eb-{uuid.uuid4().hex}",
        canonical_id=canonical.id,
    )
    db_session.add(duplicate)
    await db_session.flush()

    assert duplicate.canonical_id == canonical.id


async def test_event_nullable_fields(db_session: AsyncSession) -> None:
    event = _make_event()
    db_session.add(event)
    await db_session.flush()

    assert event.source is None
    assert event.external_id is None
    assert event.canonical_id is None
    assert event.description is None
    assert event.end_at is None
    assert event.venue_id is None
    assert event.category_id is None
    assert event.ticket_url is None
    assert event.price_min is None
    assert event.price_max is None
    assert event.image_url is None
    assert event.raw_payload is None
    assert event.embedding_id is None


async def test_event_start_at_required(db_session: AsyncSession) -> None:
    event = Event(title="No Start")  # type: ignore[call-arg]
    db_session.add(event)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()
