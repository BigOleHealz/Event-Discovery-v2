"""
Semantic deduplication service.

Algorithm (per CLAUDE.md §2):
1. Build embed text from UnifiedEvent.
2. Embed with OpenAI text-embedding-3-small.
3. Pre-filter Qdrant by ±3-day window around event start_at.
4. ANN search with score_threshold=0.92.
5. If a near-duplicate is found, set canonical_id on the incoming event.
6. Upsert point into Qdrant (idempotent by deterministic UUID5).

Idempotency: Qdrant point ID = UUID5(NAMESPACE_DNS, "{source}:{external_id}").
"""
import logging
from datetime import timedelta
from uuid import NAMESPACE_DNS, uuid5

from qdrant_client.models import (
    FieldCondition,
    Filter,
    PointStruct,
    Range,
)

from app.dedup import embedder
from app.ingestion.normalizer import UnifiedEvent, build_embed_text

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.92
_THREE_DAYS_S = int(timedelta(days=3).total_seconds())
COLLECTION_NAME = "events"


def make_qdrant_id(source: str, external_id: str) -> str:
    """Deterministic UUID5 from source + external_id — idempotent across re-runs."""
    return str(uuid5(NAMESPACE_DNS, f"{source}:{external_id}"))


async def process(event: UnifiedEvent, qdrant_client: object) -> UnifiedEvent:  # type: ignore[type-arg]
    """
    Embed, deduplicate, and upsert the event into Qdrant.

    Mutates `event.canonical_id` in-place if a near-duplicate is found.
    Returns the (possibly mutated) event.

    `qdrant_client` is typed as `object` to avoid importing QdrantClient at
    module level in tests; callers pass `QdrantClient(...)` directly.
    """
    from qdrant_client import QdrantClient  # local import to keep tests fast

    client: QdrantClient = qdrant_client  # type: ignore[assignment]

    text = build_embed_text(event)
    vector = await embedder.embed(text)

    ts = int(event.start_at.timestamp())
    window_start = ts - _THREE_DAYS_S
    window_end = ts + _THREE_DAYS_S

    response = client.query_points(
        collection_name=COLLECTION_NAME,
        query=vector,
        query_filter=Filter(
            must=[
                FieldCondition(
                    key="start_at",
                    range=Range(gte=window_start, lte=window_end),
                )
            ]
        ),
        limit=5,
        score_threshold=SIMILARITY_THRESHOLD,
    )
    results = response.points

    if results:
        canonical_event_id = results[0].payload.get("event_id") if results[0].payload else None
        if canonical_event_id and canonical_event_id != str(event.external_id):
            event.canonical_id = canonical_event_id
            logger.info(
                "Dedup: %s/%s → canonical %s (score=%.3f)",
                event.source,
                event.external_id,
                canonical_event_id,
                results[0].score,
            )

    payload = {
        "event_id": "",  # filled in by event_service after DB upsert
        "source": event.source,
        "external_id": event.external_id,
        "title": event.title,
        "start_at": ts,
        "lat": event.lat,
        "lng": event.lng,
    }

    client.upsert(
        collection_name=COLLECTION_NAME,
        points=[
            PointStruct(
                id=make_qdrant_id(event.source, event.external_id),
                vector=vector,
                payload=payload,
            )
        ],
    )

    return event


async def update_event_id(source: str, external_id: str, event_id: str, qdrant_client: object) -> None:  # type: ignore[type-arg]
    """
    Back-fill the `event_id` payload field after the DB upsert has returned the
    UUID.  Called by `event_service` once the row is persisted.
    """
    from qdrant_client import QdrantClient
    from qdrant_client.models import SetPayload

    client: QdrantClient = qdrant_client  # type: ignore[assignment]
    point_id = make_qdrant_id(source, external_id)

    client.set_payload(
        collection_name=COLLECTION_NAME,
        payload={"event_id": event_id},
        points=[point_id],
    )
