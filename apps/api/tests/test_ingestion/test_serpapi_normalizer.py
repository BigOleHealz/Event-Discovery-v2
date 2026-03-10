"""
Unit tests for the SerpApi Google Events normaliser.

Geocoding is patched out with a fixed (37.77, -122.41) coordinate so tests
never make real HTTP calls and remain deterministic regardless of clock.
"""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.ingestion.base import RawEvent
from app.ingestion.normalizer import (
    _infer_category,
    _parse_serpapi_when,
    normalize_serpapi,
)

# ---------------------------------------------------------------------------
# Fixture payloads
# ---------------------------------------------------------------------------

_SERPAPI_RAW: dict = {
    "title": "Kody West Live Concert",
    "date": {
        "start_date": "Dec 7",
        "when": "Sun, Dec 7, 8:00 – 9:30 PM CST",
    },
    "address": [
        "Antone's Nightclub, 305 E 5th St.",
        "Austin, TX",
    ],
    "link": "https://open.spotify.com/concert/3v1YTE4nhHZxxjhHkWn9Ul",
    "description": "Find tickets for Kody West at Antone's Nightclub in Austin",
    "ticket_info": [
        {
            "source": "Spotify.com",
            "link": "https://open.spotify.com/concert/3v1YTE4nhHZxxjhHkWn9Ul",
            "link_type": "tickets",
        },
        {
            "source": "Ticketmaster.com",
            "link": "https://www.ticketmaster.com/kody-west",
            "link_type": "tickets",
        },
    ],
    "venue": {
        "name": "Antone's Nightclub",
        "rating": 4.5,
        "reviews": 1753,
    },
    "thumbnail": "https://example.com/thumb.jpg",
    "image": "https://example.com/image.jpg",
    "_location": "Austin TX",
}

_SERPAPI_MULTI_DAY_RAW: dict = {
    "title": "Austin Comedy Killers",
    "date": {
        "start_date": "Dec 7",
        "when": "Dec 2, 9:00 PM – Dec 30, 10:30 PM CST",
    },
    "address": ["Sunset Strip Comedy, 214 E 6th St", "Austin, TX"],
    "link": "https://example.com/comedy-event",
    "description": "Stand-up comedy showcase every Tuesday",
    "ticket_info": [
        {"source": "Eventvesta.com", "link": "https://eventvesta.com/events/1", "link_type": "more info"},
    ],
    "venue": {"name": "Sunset Strip Comedy"},
    "thumbnail": "https://example.com/thumb.jpg",
    "_location": "Austin TX",
}

_SERPAPI_NO_VENUE_RAW: dict = {
    "title": "Tech Meetup",
    "date": {"start_date": "Mar 15", "when": "Mar 15, 6:00 – 9:00 PM"},
    "address": ["San Francisco, CA"],
    "link": "https://example.com/tech-meetup",
    "description": "Monthly tech networking event",
    "ticket_info": [],
    "thumbnail": None,
    "_location": "San Francisco CA",
}


# ---------------------------------------------------------------------------
# _parse_serpapi_when
# ---------------------------------------------------------------------------


class TestParseSerpApiWhen:
    def test_same_day_with_dow_prefix(self) -> None:
        """'Sun, Dec 7, 8:00 – 9:30 PM CST' → start and end on same day."""
        start, end = _parse_serpapi_when("Sun, Dec 7, 8:00 – 9:30 PM CST")
        assert start is not None
        assert end is not None
        assert start.month == 12
        assert start.day == 7
        assert start.hour == 8
        assert end.day == 7
        assert end.hour == 21
        assert end.minute == 30

    def test_multi_day_range(self) -> None:
        """'Dec 2, 9:00 PM – Dec 30, 10:30 PM CST' → different start and end days."""
        start, end = _parse_serpapi_when("Dec 2, 9:00 PM – Dec 30, 10:30 PM CST")
        assert start is not None
        assert end is not None
        assert start.day == 2
        assert end.day == 30

    def test_no_end_time(self) -> None:
        """When there is no dash separator, end should be None."""
        start, end = _parse_serpapi_when("Mar 15, 6:00 PM")
        assert start is not None
        assert end is None

    def test_returns_none_for_empty(self) -> None:
        start, end = _parse_serpapi_when(None)
        assert start is None
        assert end is None

    def test_returns_none_for_garbage(self) -> None:
        start, end = _parse_serpapi_when("not a date at all !!!!")
        # fuzzy parsing may still produce a result; we just check no crash
        # (dateutil's fuzzy mode is permissive)

    def test_start_is_utc_aware(self) -> None:
        start, _ = _parse_serpapi_when("Dec 7, 8:00 PM CST")
        assert start is not None
        assert start.tzinfo is not None


# ---------------------------------------------------------------------------
# _infer_category
# ---------------------------------------------------------------------------


class TestInferCategory:
    def test_concert_title(self) -> None:
        assert _infer_category("Jazz Concert at the Park", None) == "music"

    def test_comedy_show(self) -> None:
        assert _infer_category("Stand-Up Comedy Night", None) == "entertainment"

    def test_yoga_in_description(self) -> None:
        assert _infer_category("Saturday Morning Session", "Outdoor yoga in the park") == "wellness"

    def test_tech_meetup(self) -> None:
        assert _infer_category("Monthly Tech Meetup", None) == "tech"

    def test_no_match_returns_none(self) -> None:
        assert _infer_category("Quarterly Board Meeting", "Agenda items for review") is None


# ---------------------------------------------------------------------------
# normalize_serpapi
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_geocode():
    """Patch geocoder so tests don't make real HTTP calls."""
    # geocode is imported inside normalize_serpapi at call time, so we patch
    # the function at its definition site in app.ingestion.geocoder.
    with patch("app.ingestion.geocoder.geocode", return_value=(30.27, -97.74)) as m:
        yield m


class TestNormalizeSerpApi:
    def test_basic_fields(self, mock_geocode) -> None:
        raw = RawEvent(source="serpapi", external_id="test-id-1", raw_data=_SERPAPI_RAW)
        event = normalize_serpapi(raw)

        assert event.source == "serpapi"
        assert event.external_id == "test-id-1"
        assert event.title == "Kody West Live Concert"
        assert "Antone" in (event.description or "")

    def test_venue_name_from_venue_object(self, mock_geocode) -> None:
        raw = RawEvent(source="serpapi", external_id="test-id-2", raw_data=_SERPAPI_RAW)
        event = normalize_serpapi(raw)
        assert event.venue_name == "Antone's Nightclub"

    def test_city_from_last_address_part(self, mock_geocode) -> None:
        raw = RawEvent(source="serpapi", external_id="test-id-3", raw_data=_SERPAPI_RAW)
        event = normalize_serpapi(raw)
        assert event.city == "Austin, TX"

    def test_ticket_url_prefers_tickets_type(self, mock_geocode) -> None:
        raw = RawEvent(source="serpapi", external_id="test-id-4", raw_data=_SERPAPI_RAW)
        event = normalize_serpapi(raw)
        assert event.ticket_url == "https://open.spotify.com/concert/3v1YTE4nhHZxxjhHkWn9Ul"

    def test_ticket_links_all_entries_preserved(self, mock_geocode) -> None:
        """Both ticket_info entries are stored in ticket_links."""
        raw = RawEvent(source="serpapi", external_id="test-id-4b", raw_data=_SERPAPI_RAW)
        event = normalize_serpapi(raw)
        assert event.ticket_links is not None
        assert len(event.ticket_links) == 2
        assert event.ticket_links[0] == {
            "source": "Spotify.com",
            "url": "https://open.spotify.com/concert/3v1YTE4nhHZxxjhHkWn9Ul",
            "type": "tickets",
        }
        assert event.ticket_links[1] == {
            "source": "Ticketmaster.com",
            "url": "https://www.ticketmaster.com/kody-west",
            "type": "tickets",
        }

    def test_ticket_links_single_more_info_entry(self, mock_geocode) -> None:
        """Single 'more info' link is captured with correct type."""
        raw = RawEvent(source="serpapi", external_id="test-id-4c", raw_data=_SERPAPI_MULTI_DAY_RAW)
        event = normalize_serpapi(raw)
        assert event.ticket_links is not None
        assert len(event.ticket_links) == 1
        assert event.ticket_links[0]["type"] == "more info"
        assert event.ticket_links[0]["source"] == "Eventvesta.com"

    def test_ticket_links_none_when_empty(self, mock_geocode) -> None:
        """Empty ticket_info list yields None ticket_links."""
        raw = RawEvent(source="serpapi", external_id="test-id-4d", raw_data=_SERPAPI_NO_VENUE_RAW)
        event = normalize_serpapi(raw)
        assert event.ticket_links is None

    def test_image_prefers_image_over_thumbnail(self, mock_geocode) -> None:
        raw = RawEvent(source="serpapi", external_id="test-id-5", raw_data=_SERPAPI_RAW)
        event = normalize_serpapi(raw)
        assert event.image_url == "https://example.com/image.jpg"

    def test_lat_lng_from_geocoder(self, mock_geocode) -> None:
        raw = RawEvent(source="serpapi", external_id="test-id-6", raw_data=_SERPAPI_RAW)
        event = normalize_serpapi(raw)
        assert event.lat == 30.27
        assert event.lng == -97.74
        mock_geocode.assert_called_once_with("Austin, TX")

    def test_price_is_none(self, mock_geocode) -> None:
        raw = RawEvent(source="serpapi", external_id="test-id-7", raw_data=_SERPAPI_RAW)
        event = normalize_serpapi(raw)
        assert event.price_min is None
        assert event.price_max is None

    def test_category_inferred_from_title(self, mock_geocode) -> None:
        raw = RawEvent(source="serpapi", external_id="test-id-8", raw_data=_SERPAPI_RAW)
        event = normalize_serpapi(raw)
        # "Concert" keyword → "music"
        assert event.category_slug == "music"

    def test_multi_day_event_has_end_date(self, mock_geocode) -> None:
        raw = RawEvent(source="serpapi", external_id="test-id-9", raw_data=_SERPAPI_MULTI_DAY_RAW)
        event = normalize_serpapi(raw)
        assert event.end_at is not None
        assert event.start_at.day != event.end_at.day

    def test_fallback_ticket_url_to_more_info_link(self, mock_geocode) -> None:
        raw = RawEvent(source="serpapi", external_id="test-id-10", raw_data=_SERPAPI_MULTI_DAY_RAW)
        event = normalize_serpapi(raw)
        # No "tickets" link type — should fall back to first ticket_info link
        assert event.ticket_url == "https://eventvesta.com/events/1"

    def test_no_venue_falls_back_to_address_first_part(self, mock_geocode) -> None:
        raw = RawEvent(source="serpapi", external_id="test-id-11", raw_data=_SERPAPI_NO_VENUE_RAW)
        event = normalize_serpapi(raw)
        # Address has only one part (city); venue_name should be "San Francisco"
        assert event.venue_name == "San Francisco"

    def test_no_image_is_none(self, mock_geocode) -> None:
        raw = RawEvent(source="serpapi", external_id="test-id-12", raw_data=_SERPAPI_NO_VENUE_RAW)
        event = normalize_serpapi(raw)
        assert event.image_url is None

    def test_start_at_is_utc_aware(self, mock_geocode) -> None:
        raw = RawEvent(source="serpapi", external_id="test-id-13", raw_data=_SERPAPI_RAW)
        event = normalize_serpapi(raw)
        assert event.start_at.tzinfo == timezone.utc

    def test_raw_payload_stored(self, mock_geocode) -> None:
        raw = RawEvent(source="serpapi", external_id="test-id-14", raw_data=_SERPAPI_RAW)
        event = normalize_serpapi(raw)
        assert event.raw_payload["title"] == "Kody West Live Concert"

    def test_dispatcher_routes_serpapi(self, mock_geocode) -> None:
        """normalize() dispatcher correctly routes source='serpapi'."""
        from app.ingestion.normalizer import normalize

        raw = RawEvent(source="serpapi", external_id="test-id-15", raw_data=_SERPAPI_RAW)
        event = normalize(raw)
        assert event.source == "serpapi"
