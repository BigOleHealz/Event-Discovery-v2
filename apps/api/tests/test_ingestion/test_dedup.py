"""Unit tests for dedup_service — specifically make_qdrant_id and threshold logic."""
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.dedup.dedup_service import SIMILARITY_THRESHOLD, make_qdrant_id
from app.ingestion.normalizer import UnifiedEvent


def _make_event(**kwargs) -> UnifiedEvent:
    defaults = dict(
        source="eventbrite",
        external_id="evt-001",
        title="Rock Concert",
        description="Live rock music.",
        start_at=datetime(2026, 9, 1, 19, 0, tzinfo=timezone.utc),
        end_at=None,
        lat=37.77,
        lng=-122.41,
        venue_name="The Fillmore",
        venue_address="1805 Geary Blvd",
        city="San Francisco",
        category_slug="music",
        ticket_url="https://example.com/tickets",
        price_min=Decimal("25.00"),
        price_max=Decimal("50.00"),
        image_url=None,
        raw_payload={},
    )
    defaults.update(kwargs)
    return UnifiedEvent(**defaults)


# ---------------------------------------------------------------------------
# make_qdrant_id
# ---------------------------------------------------------------------------

def test_make_qdrant_id_is_deterministic():
    id1 = make_qdrant_id("eventbrite", "12345")
    id2 = make_qdrant_id("eventbrite", "12345")
    assert id1 == id2


def test_make_qdrant_id_differs_by_source():
    id_eb = make_qdrant_id("eventbrite", "12345")
    id_mu = make_qdrant_id("meetup", "12345")
    assert id_eb != id_mu


def test_make_qdrant_id_differs_by_external_id():
    id1 = make_qdrant_id("eventbrite", "111")
    id2 = make_qdrant_id("eventbrite", "222")
    assert id1 != id2


def test_make_qdrant_id_is_valid_uuid_string():
    import re

    qdrant_id = make_qdrant_id("eventbrite", "abc")
    assert re.match(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", qdrant_id
    )


# ---------------------------------------------------------------------------
# SIMILARITY_THRESHOLD guard
# ---------------------------------------------------------------------------

def test_similarity_threshold_value():
    # Threshold must stay at or above 0.90 to prevent over-merging.
    # If this fails, a deliberate decision to lower it needs review.
    assert SIMILARITY_THRESHOLD >= 0.90


# ---------------------------------------------------------------------------
# process() — mock Qdrant and embedder
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_no_duplicate_leaves_canonical_id_none():
    """When Qdrant finds no near-duplicates, canonical_id stays None."""
    from app.dedup.dedup_service import process

    mock_qdrant = MagicMock()
    mock_qdrant.query_points.return_value = MagicMock(points=[])  # no results
    mock_qdrant.upsert.return_value = None

    with patch("app.dedup.dedup_service.embedder.embed", new=AsyncMock(return_value=[0.1] * 1536)):
        event = _make_event()
        result = await process(event, mock_qdrant)

    assert result.canonical_id is None
    mock_qdrant.upsert.assert_called_once()


@pytest.mark.asyncio
async def test_process_duplicate_sets_canonical_id():
    """When Qdrant returns a near-duplicate, canonical_id is set."""
    from app.dedup.dedup_service import process

    existing_event_id = "550e8400-e29b-41d4-a716-446655440000"
    mock_result = MagicMock()
    mock_result.score = 0.95
    mock_result.payload = {"event_id": existing_event_id}

    mock_qdrant = MagicMock()
    mock_qdrant.query_points.return_value = MagicMock(points=[mock_result])
    mock_qdrant.upsert.return_value = None

    with patch("app.dedup.dedup_service.embedder.embed", new=AsyncMock(return_value=[0.1] * 1536)):
        event = _make_event(source="meetup", external_id="meetup-999")
        result = await process(event, mock_qdrant)

    assert result.canonical_id == existing_event_id


@pytest.mark.asyncio
async def test_process_does_not_self_deduplicate():
    """
    If Qdrant returns a match whose event_id matches the current event's
    external_id (same event re-ingested), canonical_id must NOT be set.
    """
    from app.dedup.dedup_service import process

    mock_result = MagicMock()
    mock_result.score = 0.99
    mock_result.payload = {"event_id": "evt-001"}  # same as our external_id

    mock_qdrant = MagicMock()
    mock_qdrant.query_points.return_value = MagicMock(points=[mock_result])
    mock_qdrant.upsert.return_value = None

    with patch("app.dedup.dedup_service.embedder.embed", new=AsyncMock(return_value=[0.1] * 1536)):
        event = _make_event(source="eventbrite", external_id="evt-001")
        result = await process(event, mock_qdrant)

    # Same external_id → not a cross-source duplicate; canonical_id must be None
    assert result.canonical_id is None


@pytest.mark.asyncio
async def test_process_calls_qdrant_upsert_with_correct_id():
    """The Qdrant point ID must be the deterministic UUID5."""
    from app.dedup.dedup_service import process

    mock_qdrant = MagicMock()
    mock_qdrant.query_points.return_value = MagicMock(points=[])
    mock_qdrant.upsert.return_value = None

    with patch("app.dedup.dedup_service.embedder.embed", new=AsyncMock(return_value=[0.1] * 1536)):
        event = _make_event(source="eventbrite", external_id="evt-001")
        await process(event, mock_qdrant)

    call_args = mock_qdrant.upsert.call_args
    points = call_args.kwargs.get("points") or call_args.args[1]
    assert points[0].id == make_qdrant_id("eventbrite", "evt-001")
