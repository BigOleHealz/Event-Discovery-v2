"""Unit tests for the Google Places venue lookup service.

All HTTP calls are mocked so tests run offline and deterministically.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.services.places_service import (
    PlaceInfo,
    _parse_details_response,
    _resolve_cid,
    extract_cid_from_maps_link,
    extract_ludocid,
    lookup_venue,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_DETAILS_OK: dict = {
    "status": "OK",
    "result": {
        "place_id": "ChIJtesting123",
        "name": "The Concourse Project",
        "formatted_address": "8509 Burleson Rd, Austin, TX 78719, USA",
        "geometry": {"location": {"lat": 30.1951, "lng": -97.6898}},
        "international_phone_number": "+1 512-000-0000",
        "website": "https://theconcourseproject.com",
        "rating": 4.5,
    },
}

_DETAILS_ZERO_RESULTS: dict = {"status": "ZERO_RESULTS", "result": {}}

_FIND_PLACE_OK: dict = {
    "status": "OK",
    "candidates": [{"place_id": "ChIJtesting123"}],
}

_FIND_PLACE_EMPTY: dict = {"status": "OK", "candidates": []}


def _mock_resp(data: dict, status_code: int = 200) -> MagicMock:
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = data
    m.raise_for_status = MagicMock()
    return m


# ---------------------------------------------------------------------------
# extract_ludocid
# ---------------------------------------------------------------------------


class TestExtractLudocid:
    def test_extracts_from_full_url(self) -> None:
        url = (
            "https://www.google.com/search?sca_esv=abc&q=Venue"
            "&ludocid=10117835432427841335&ibp=gwp"
        )
        assert extract_ludocid(url) == "10117835432427841335"

    def test_returns_none_for_missing_param(self) -> None:
        url = "https://www.google.com/search?q=Venue"
        assert extract_ludocid(url) is None

    def test_returns_none_for_none_input(self) -> None:
        assert extract_ludocid(None) is None

    def test_returns_none_for_empty_string(self) -> None:
        assert extract_ludocid("") is None


# ---------------------------------------------------------------------------
# extract_cid_from_maps_link
# ---------------------------------------------------------------------------

# The real maps link from the sample SerpApi event
_MAPS_LINK = (
    "https://www.google.com/maps/place//data=!4m2!3m1!1s"
    "0x8644b1316e46d40b:0x8c69c5b81db8e737"
    "?sa=X&ved=2ahUKEwid2L-myu-RAxU_PrkGHcPsI_gQ9eIBegQIAxAA"
)
_EXPECTED_CID = "10117835432427841335"


class TestExtractCidFromMapsLink:
    def test_extracts_decimal_cid(self) -> None:
        assert extract_cid_from_maps_link(_MAPS_LINK) == _EXPECTED_CID

    def test_matches_ludocid_value(self) -> None:
        """The decimal CID from the maps link equals the ludocid on venue.link."""
        venue_link = (
            "https://www.google.com/search?q=The+Concourse+Project"
            f"&ludocid={_EXPECTED_CID}&ibp=gwp"
        )
        assert extract_cid_from_maps_link(_MAPS_LINK) == extract_ludocid(venue_link)

    def test_returns_none_for_none(self) -> None:
        assert extract_cid_from_maps_link(None) is None

    def test_returns_none_when_pattern_absent(self) -> None:
        assert extract_cid_from_maps_link("https://www.google.com/maps") is None

    def test_case_insensitive(self) -> None:
        link = (
            "https://www.google.com/maps/place//data=!4m2!3m1!1s"
            "0x8644B1316E46D40B:0x8C69C5B81DB8E737"
        )
        assert extract_cid_from_maps_link(link) == _EXPECTED_CID


# ---------------------------------------------------------------------------
# _resolve_cid
# ---------------------------------------------------------------------------


class TestResolveCid:
    def test_prefers_maps_link_over_venue_link(self) -> None:
        # Both sources agree on the same CID value, but maps_link is tried first
        result = _resolve_cid(maps_link=_MAPS_LINK, venue_link=None)
        assert result == _EXPECTED_CID

    def test_falls_back_to_venue_link_when_maps_link_absent(self) -> None:
        venue_link = (
            "https://www.google.com/search?q=Venue"
            f"&ludocid={_EXPECTED_CID}"
        )
        result = _resolve_cid(maps_link=None, venue_link=venue_link)
        assert result == _EXPECTED_CID

    def test_returns_none_when_both_absent(self) -> None:
        assert _resolve_cid(maps_link=None, venue_link=None) is None

    def test_maps_link_wins_when_both_present(self) -> None:
        venue_link = "https://www.google.com/search?ludocid=99999"
        # maps_link CID should win
        result = _resolve_cid(maps_link=_MAPS_LINK, venue_link=venue_link)
        assert result == _EXPECTED_CID


# ---------------------------------------------------------------------------
# _parse_details_response
# ---------------------------------------------------------------------------


class TestParseDetailsResponse:
    def test_happy_path(self) -> None:
        info = _parse_details_response(_DETAILS_OK)
        assert info is not None
        assert info.place_id == "ChIJtesting123"
        assert info.name == "The Concourse Project"
        assert info.formatted_address == "8509 Burleson Rd, Austin, TX 78719, USA"
        assert info.lat == pytest.approx(30.1951)
        assert info.lng == pytest.approx(-97.6898)
        assert info.phone == "+1 512-000-0000"
        assert info.website == "https://theconcourseproject.com"
        assert info.rating == pytest.approx(4.5)

    def test_returns_none_when_result_empty(self) -> None:
        assert _parse_details_response({"status": "ZERO_RESULTS", "result": {}}) is None

    def test_returns_none_when_geometry_missing(self) -> None:
        data = {
            "status": "OK",
            "result": {
                "place_id": "ChIJx",
                "name": "Venue",
                "formatted_address": "123 Main St",
            },
        }
        assert _parse_details_response(data) is None

    def test_optional_fields_default_to_none(self) -> None:
        data = {
            "status": "OK",
            "result": {
                "place_id": "ChIJx",
                "name": "Venue",
                "formatted_address": "123 Main St",
                "geometry": {"location": {"lat": 1.0, "lng": 2.0}},
            },
        }
        info = _parse_details_response(data)
        assert info is not None
        assert info.phone is None
        assert info.website is None
        assert info.rating is None


# ---------------------------------------------------------------------------
# lookup_venue
# ---------------------------------------------------------------------------


class TestLookupVenue:
    def test_returns_none_when_api_key_empty(self) -> None:
        result = lookup_venue(
            venue_link="https://www.google.com/search?ludocid=123",
            venue_name="Venue",
            city="Austin TX",
            api_key="",
        )
        assert result is None

    @patch("app.services.places_service.httpx.get")
    def test_cid_path_via_maps_link(self, mock_get) -> None:
        """CID extracted from event_location_map.link is used first."""
        mock_get.return_value = _mock_resp(_DETAILS_OK)
        info = lookup_venue(
            venue_link=None,
            venue_name="The Concourse Project",
            city="Austin TX",
            api_key="test-key",
            maps_link=_MAPS_LINK,
        )
        assert info is not None
        assert info.place_id == "ChIJtesting123"
        mock_get.assert_called_once()
        assert f"cid:{_EXPECTED_CID}" in str(mock_get.call_args)

    @patch("app.services.places_service.httpx.get")
    def test_cid_path_via_venue_link_fallback(self, mock_get) -> None:
        """CID from ludocid query param is used when maps_link is absent."""
        mock_get.return_value = _mock_resp(_DETAILS_OK)
        info = lookup_venue(
            venue_link=(
                "https://www.google.com/search?q=Venue"
                f"&ludocid={_EXPECTED_CID}"
            ),
            venue_name="The Concourse Project",
            city="Austin TX",
            api_key="test-key",
            maps_link=None,
        )
        assert info is not None
        assert info.place_id == "ChIJtesting123"
        mock_get.assert_called_once()
        assert f"cid:{_EXPECTED_CID}" in str(mock_get.call_args)

    @patch("app.services.places_service.httpx.get")
    def test_falls_back_to_name_search_when_cid_fails(self, mock_get) -> None:
        # First call (CID) → ZERO_RESULTS; second call (Find Place) → candidate;
        # third call (Details) → full result.
        mock_get.side_effect = [
            _mock_resp(_DETAILS_ZERO_RESULTS),
            _mock_resp(_FIND_PLACE_OK),
            _mock_resp(_DETAILS_OK),
        ]
        info = lookup_venue(
            venue_link=(
                "https://www.google.com/search?q=Venue"
                "&ludocid=10117835432427841335"
            ),
            venue_name="The Concourse Project",
            city="Austin TX",
            api_key="test-key",
        )
        assert info is not None
        assert info.place_id == "ChIJtesting123"
        assert mock_get.call_count == 3

    @patch("app.services.places_service.httpx.get")
    def test_name_only_path_when_no_venue_link(self, mock_get) -> None:
        mock_get.side_effect = [
            _mock_resp(_FIND_PLACE_OK),
            _mock_resp(_DETAILS_OK),
        ]
        info = lookup_venue(
            venue_link=None,
            venue_name="The Concourse Project",
            city="Austin TX",
            api_key="test-key",
        )
        assert info is not None
        assert info.name == "The Concourse Project"
        assert mock_get.call_count == 2

    @patch("app.services.places_service.httpx.get")
    def test_returns_none_when_find_place_empty(self, mock_get) -> None:
        mock_get.return_value = _mock_resp(_FIND_PLACE_EMPTY)
        info = lookup_venue(
            venue_link=None,
            venue_name="Mystery Venue",
            city="Austin TX",
            api_key="test-key",
        )
        assert info is None

    @patch("app.services.places_service.httpx.get")
    def test_returns_none_on_http_error(self, mock_get) -> None:
        mock_get.side_effect = Exception("network error")
        info = lookup_venue(
            venue_link="https://www.google.com/search?ludocid=123",
            venue_name="Venue",
            city="Austin TX",
            api_key="test-key",
        )
        assert info is None


# ---------------------------------------------------------------------------
# Integration with normalize_serpapi
# ---------------------------------------------------------------------------


class TestNormalizeSerpApiWithPlaces:
    """Verify that normalize_serpapi passes Places data into UnifiedEvent."""

    _RAW: dict = {
        "title": "GRYFFIN with AVELLO",
        "date": {"when": "Sat, 03 Jan, 21:00–23:00 GMT-6"},
        "address": ["The Concourse Project, 8509 Burleson Rd", "Austin, TX, United States"],
        "link": "https://www.statesman.com/event/123",
        "description": "Electronic music concert",
        "ticket_info": [
            {"source": "Spotify.com", "link": "https://spotify.com/x", "link_type": "tickets"}
        ],
        "venue": {
            "name": "The Concourse Project",
            "rating": 4.5,
            "link": (
                "https://www.google.com/search?q=The+Concourse+Project"
                f"&ludocid={_EXPECTED_CID}"
            ),
        },
        "event_location_map": {
            "link": _MAPS_LINK,
            "image": "https://www.google.com/maps/vt/data=...",
        },
        "thumbnail": "https://example.com/thumb.jpg",
        "_location": "Austin TX",
    }

    @patch("app.services.places_service.httpx.get")
    @patch("app.config.settings")
    def test_enriched_venue_data_used(self, mock_settings, mock_get) -> None:
        mock_settings.google_places_api_key = "test-key"
        mock_get.return_value = _mock_resp(_DETAILS_OK)

        from app.ingestion.base import RawEvent
        from app.ingestion.normalizer import normalize_serpapi

        raw = RawEvent(source="serpapi", external_id="test-gryffin", raw_data=self._RAW)
        event = normalize_serpapi(raw)

        assert event.venue_place_id == "ChIJtesting123"
        assert event.venue_phone == "+1 512-000-0000"
        assert event.venue_website == "https://theconcourseproject.com"
        # Coordinates come from Places, not Nominatim
        assert event.lat == pytest.approx(30.1951)
        assert event.lng == pytest.approx(-97.6898)

    @patch("app.services.places_service.httpx.get")
    @patch("app.ingestion.geocoder.geocode", return_value=(30.27, -97.74))
    @patch("app.config.settings")
    def test_falls_back_to_geocoder_when_no_key(
        self, mock_settings, mock_geocode, mock_get
    ) -> None:
        mock_settings.google_places_api_key = ""  # no key

        from app.ingestion.base import RawEvent
        from app.ingestion.normalizer import normalize_serpapi

        raw = RawEvent(source="serpapi", external_id="test-gryffin-2", raw_data=self._RAW)
        event = normalize_serpapi(raw)

        mock_get.assert_not_called()
        assert event.venue_place_id is None
        assert event.lat == pytest.approx(30.27)
