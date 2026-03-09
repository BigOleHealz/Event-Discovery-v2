import uuid
from datetime import datetime, timezone

import pytest
from geoalchemy2 import WKTElement
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, Invite, User, Venue


_START = datetime(2026, 7, 4, 20, 0, tzinfo=timezone.utc)


async def _setup_event_and_user(db_session: AsyncSession) -> tuple[Event, User]:
    venue = Venue(
        name=f"Venue {uuid.uuid4().hex[:6]}",
        location=WKTElement("POINT(-122.4194 37.7749)", srid=4326),
    )
    db_session.add(venue)

    user = User(
        email=f"sender_{uuid.uuid4().hex[:8]}@example.com",
        oauth_provider="google",
        oauth_sub=uuid.uuid4().hex,
    )
    db_session.add(user)
    await db_session.flush()

    event = Event(title="Invite Event", start_at=_START, venue_id=venue.id)
    db_session.add(event)
    await db_session.flush()

    return event, user


async def test_create_invite(db_session: AsyncSession) -> None:
    event, sender = await _setup_event_and_user(db_session)

    invite = Invite(
        event_id=event.id,
        sender_id=sender.id,
        recipient_phone="+14155550100",
        deep_link=f"https://app.example.com/events/{event.id}?ref=invite",
    )
    db_session.add(invite)
    await db_session.flush()

    assert invite.id is not None
    assert invite.status == "pending"
    assert invite.twilio_sid is None
    assert invite.sent_at is not None


async def test_invite_repr(db_session: AsyncSession) -> None:
    event, sender = await _setup_event_and_user(db_session)
    invite = Invite(
        event_id=event.id,
        sender_id=sender.id,
        recipient_phone="+14155550101",
        deep_link="https://app.example.com/events/x",
    )
    db_session.add(invite)
    await db_session.flush()
    r = repr(invite)
    assert "+14155550101" in r
    assert "pending" in r


async def test_invite_with_twilio_sid(db_session: AsyncSession) -> None:
    event, sender = await _setup_event_and_user(db_session)
    invite = Invite(
        event_id=event.id,
        sender_id=sender.id,
        recipient_phone="+14155550102",
        deep_link="https://app.example.com/events/x",
        twilio_sid="SM" + uuid.uuid4().hex,
        status="delivered",
    )
    db_session.add(invite)
    await db_session.flush()

    assert invite.status == "delivered"
    assert invite.twilio_sid is not None


async def test_invite_invalid_event_fk(db_session: AsyncSession) -> None:
    _, sender = await _setup_event_and_user(db_session)
    invite = Invite(
        event_id=uuid.uuid4(),  # non-existent event
        sender_id=sender.id,
        recipient_phone="+14155550103",
        deep_link="https://app.example.com/events/x",
    )
    db_session.add(invite)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_invite_invalid_sender_fk(db_session: AsyncSession) -> None:
    event, _ = await _setup_event_and_user(db_session)
    invite = Invite(
        event_id=event.id,
        sender_id=uuid.uuid4(),  # non-existent user
        recipient_phone="+14155550104",
        deep_link="https://app.example.com/events/x",
    )
    db_session.add(invite)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()
