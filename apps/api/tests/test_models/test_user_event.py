import uuid
from datetime import datetime, timezone

import pytest
from geoalchemy2 import WKTElement
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, User, UserEvent, Venue


_START = datetime(2026, 8, 10, 18, 0, tzinfo=timezone.utc)


async def _setup(db_session: AsyncSession) -> tuple[User, Event]:
    venue = Venue(
        name=f"Venue {uuid.uuid4().hex[:6]}",
        location=WKTElement("POINT(-122.4194 37.7749)", srid=4326),
    )
    db_session.add(venue)

    user = User(
        email=f"actor_{uuid.uuid4().hex[:8]}@example.com",
        oauth_provider="google",
        oauth_sub=uuid.uuid4().hex,
    )
    db_session.add(user)
    await db_session.flush()

    event = Event(title="Action Event", start_at=_START, venue_id=venue.id)
    db_session.add(event)
    await db_session.flush()

    return user, event


async def test_create_user_event(db_session: AsyncSession) -> None:
    user, event = await _setup(db_session)
    ue = UserEvent(user_id=user.id, event_id=event.id, action="view")
    db_session.add(ue)
    await db_session.flush()

    assert ue.user_id == user.id
    assert ue.event_id == event.id
    assert ue.action == "view"
    assert ue.created_at is not None


async def test_user_event_repr(db_session: AsyncSession) -> None:
    user, event = await _setup(db_session)
    ue = UserEvent(user_id=user.id, event_id=event.id, action="save")
    db_session.add(ue)
    await db_session.flush()
    r = repr(ue)
    assert "save" in r


async def test_multiple_actions_same_user_event(db_session: AsyncSession) -> None:
    """A user can have multiple distinct action records for the same event."""
    user, event = await _setup(db_session)
    for action in ("view", "save", "rsvp"):
        db_session.add(UserEvent(user_id=user.id, event_id=event.id, action=action))
    await db_session.flush()


async def test_composite_pk_prevents_duplicate_action(db_session: AsyncSession) -> None:
    """Same (user_id, event_id, action) triplet must be rejected."""
    user, event = await _setup(db_session)
    db_session.add(UserEvent(user_id=user.id, event_id=event.id, action="rsvp"))
    await db_session.flush()

    db_session.add(UserEvent(user_id=user.id, event_id=event.id, action="rsvp"))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_user_event_invalid_user_fk(db_session: AsyncSession) -> None:
    _, event = await _setup(db_session)
    ue = UserEvent(user_id=uuid.uuid4(), event_id=event.id, action="view")
    db_session.add(ue)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_user_event_invalid_event_fk(db_session: AsyncSession) -> None:
    user, _ = await _setup(db_session)
    ue = UserEvent(user_id=user.id, event_id=uuid.uuid4(), action="view")
    db_session.add(ue)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()
