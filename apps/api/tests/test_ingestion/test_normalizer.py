"""Unit tests for the ingestion normaliser."""
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.ingestion.base import RawEvent
from app.ingestion.normalizer import (
    UnifiedEvent,
    build_embed_text,
    normalize,
    normalize_eventbrite,
    normalize_meetup,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

EVENTBRITE_RAW: dict = {
    "id": "12345678",
    "name": {"text": "Tech Summit 2026"},
    "description": {"text": "A premier tech conference in San Francisco."},
    "start": {"utc": "2026-06-15T18:00:00Z", "local": "2026-06-15T11:00:00"},
    "end": {"utc": "2026-06-15T23:00:00Z", "local": "2026-06-15T16:00:00"},
    "url": "https://www.eventbrite.com/e/tech-summit-2026-12345678",
    "venue": {
        "name": "Moscone Center",
        "latitude": "37.7838",
        "longitude": "-122.4006",
        "address": {
            "localized_address_display": "747 Howard St, San Francisco, CA 94103",
            "city": "San Francisco",
        },
    },
    "category": {"short_name": "Science & Technology"},
    "ticket_availability": {
        "minimum_ticket_price": {"major_value": "49.00"},
        "maximum_ticket_price": {"major_value": "299.00"},
    },
    "logo": {"url": "https://img.evbuc.com/summit.jpg"},
}

MEETUP_RAW: dict = {
    "id": "meetup-abc123",
    "title": "Python Bay Area Meetup",
    "description": "Monthly Python community meetup.",
    "dateTime": "2026-07-10T19:00:00+00:00",
    "endTime": "2026-07-10T21:00:00+00:00",
    "eventUrl": "https://www.meetup.com/python-bay-area/events/abc123/",
    "venue": {
        "name": "GitHub HQ",
        "address": "88 Colin P Kelly Jr St",
        "city": "San Francisco",
        "lat": 37.7823,
        "lng": -122.3917,
    },
    "group": {
        "category": {"urlkey": "tech"},
    },
    "feeSettings": {"amount": 0, "currency": "USD"},
}


# ---------------------------------------------------------------------------
# Eventbrite normaliser
# ---------------------------------------------------------------------------

def test_normalize_eventbrite_title():
    raw = RawEvent(source="eventbrite", external_id="12345678", raw_data=EVENTBRITE_RAW)
    event = normalize_eventbrite(raw)
    assert event.title == "Tech Summit 2026"


def test_normalize_eventbrite_source_and_id():
    raw = RawEvent(source="eventbrite", external_id="12345678", raw_data=EVENTBRITE_RAW)
    event = normalize_eventbrite(raw)
    assert event.source == "eventbrite"
    assert event.external_id == "12345678"


def test_normalize_eventbrite_datetime_is_utc():
    raw = RawEvent(source="eventbrite", external_id="12345678", raw_data=EVENTBRITE_RAW)
    event = normalize_eventbrite(raw)
    assert event.start_at == datetime(2026, 6, 15, 18, 0, 0, tzinfo=timezone.utc)
    assert event.end_at == datetime(2026, 6, 15, 23, 0, 0, tzinfo=timezone.utc)


def test_normalize_eventbrite_venue():
    raw = RawEvent(source="eventbrite", external_id="12345678", raw_data=EVENTBRITE_RAW)
    event = normalize_eventbrite(raw)
    assert event.venue_name == "Moscone Center"
    assert event.lat == pytest.approx(37.7838, rel=1e-3)
    assert event.lng == pytest.approx(-122.4006, rel=1e-3)
    assert event.city == "San Francisco"


def test_normalize_eventbrite_price():
    raw = RawEvent(source="eventbrite", external_id="12345678", raw_data=EVENTBRITE_RAW)
    event = normalize_eventbrite(raw)
    assert event.price_min == Decimal("49.00")
    assert event.price_max == Decimal("299.00")


def test_normalize_eventbrite_category_slug():
    raw = RawEvent(source="eventbrite", external_id="12345678", raw_data=EVENTBRITE_RAW)
    event = normalize_eventbrite(raw)
    assert event.category_slug == "tech"


def test_normalize_eventbrite_missing_venue_returns_zero_coords():
    data = {**EVENTBRITE_RAW, "venue": {}}
    raw = RawEvent(source="eventbrite", external_id="12345678", raw_data=data)
    event = normalize_eventbrite(raw)
    assert event.lat == 0.0
    assert event.lng == 0.0


# ---------------------------------------------------------------------------
# Meetup normaliser
# ---------------------------------------------------------------------------

def test_normalize_meetup_title():
    raw = RawEvent(source="meetup", external_id="meetup-abc123", raw_data=MEETUP_RAW)
    event = normalize_meetup(raw)
    assert event.title == "Python Bay Area Meetup"


def test_normalize_meetup_coords():
    raw = RawEvent(source="meetup", external_id="meetup-abc123", raw_data=MEETUP_RAW)
    event = normalize_meetup(raw)
    assert event.lat == pytest.approx(37.7823, rel=1e-3)
    assert event.lng == pytest.approx(-122.3917, rel=1e-3)


def test_normalize_meetup_category_slug():
    raw = RawEvent(source="meetup", external_id="meetup-abc123", raw_data=MEETUP_RAW)
    event = normalize_meetup(raw)
    assert event.category_slug == "tech"


def test_normalize_meetup_free_event():
    raw = RawEvent(source="meetup", external_id="meetup-abc123", raw_data=MEETUP_RAW)
    event = normalize_meetup(raw)
    assert event.price_min == Decimal("0")


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def test_normalize_dispatcher_routes_eventbrite():
    raw = RawEvent(source="eventbrite", external_id="12345678", raw_data=EVENTBRITE_RAW)
    event = normalize(raw)
    assert isinstance(event, UnifiedEvent)
    assert event.source == "eventbrite"


def test_normalize_dispatcher_routes_meetup():
    raw = RawEvent(source="meetup", external_id="meetup-abc123", raw_data=MEETUP_RAW)
    event = normalize(raw)
    assert isinstance(event, UnifiedEvent)
    assert event.source == "meetup"


def test_normalize_unknown_source_raises():
    raw = RawEvent(source="unknown_source", external_id="x", raw_data={})
    with pytest.raises(ValueError, match="No normaliser registered"):
        normalize(raw)


# ---------------------------------------------------------------------------
# build_embed_text
# ---------------------------------------------------------------------------

def test_build_embed_text_includes_all_parts():
    event = UnifiedEvent(
        source="eventbrite",
        external_id="1",
        title="Jazz Night",
        description="Great jazz musicians perform live.",
        start_at=datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc),
        end_at=None,
        lat=37.77,
        lng=-122.41,
        venue_name="The Fillmore",
        venue_address="1805 Geary Blvd",
        city="San Francisco",
        category_slug="music",
        ticket_url=None,
        price_min=None,
        price_max=None,
        image_url=None,
        raw_payload={},
    )
    text = build_embed_text(event)
    assert "Jazz Night" in text
    assert "music" in text
    assert "The Fillmore" in text
    assert "San Francisco" in text
    assert "2026-08-20" in text
    assert "Great jazz" in text


def test_build_embed_text_omits_empty_parts():
    event = UnifiedEvent(
        source="eventbrite",
        external_id="1",
        title="Mystery Event",
        description=None,
        start_at=datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc),
        end_at=None,
        lat=0.0,
        lng=0.0,
        venue_name=None,
        city=None,
        category_slug=None,
        ticket_url=None,
        price_min=None,
        price_max=None,
        image_url=None,
        raw_payload={},
    )
    text = build_embed_text(event)
    # No consecutive pipes from empty parts
    assert "| |" not in text
    assert text.startswith("Mystery Event")


def test_build_embed_text_truncates_description_at_500():
    long_desc = "x" * 600
    event = UnifiedEvent(
        source="eventbrite",
        external_id="1",
        title="T",
        description=long_desc,
        start_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end_at=None,
        lat=0.0,
        lng=0.0,
        venue_name=None,
        city=None,
        category_slug=None,
        ticket_url=None,
        price_min=None,
        price_max=None,
        image_url=None,
        raw_payload={},
    )
    text = build_embed_text(event)
    # Description portion should be at most 500 chars
    assert "x" * 501 not in text
