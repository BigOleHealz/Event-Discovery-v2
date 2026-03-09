#!/usr/bin/env python3
"""
Create (or verify) the Qdrant 'events' collection used for semantic deduplication
and similarity search.

Run once against a fresh Qdrant instance:
    python scripts/init_qdrant.py

Safe to re-run — uses recreate=False so an existing collection is left intact.
Pass --recreate to drop and rebuild (useful after changing vector config):
    python scripts/init_qdrant.py --recreate
"""
import argparse
import os
import sys

from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import (
    Distance,
    HnswConfigDiff,
    OptimizersConfigDiff,
    PayloadSchemaType,
    ScalarQuantization,
    ScalarQuantizationConfig,
    ScalarType,
    VectorParams,
)

COLLECTION_NAME = "events"
VECTOR_SIZE = 1536        # text-embedding-3-small
DISTANCE = Distance.COSINE


def build_client() -> QdrantClient:
    url = os.getenv("QDRANT_URL", "http://localhost:6333")
    api_key = os.getenv("QDRANT_API_KEY") or None
    return QdrantClient(url=url, api_key=api_key)


def collection_exists(client: QdrantClient) -> bool:
    try:
        client.get_collection(COLLECTION_NAME)
        return True
    except UnexpectedResponse:
        return False


def create_collection(client: QdrantClient) -> None:
    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(
            size=VECTOR_SIZE,
            distance=DISTANCE,
            # HNSW tuned for recall/speed balance at our expected collection size
            hnsw_config=HnswConfigDiff(m=16, ef_construct=100),
        ),
        # Scalar int8 quantization: ~4x memory reduction, <5% recall loss
        quantization_config=ScalarQuantization(
            scalar=ScalarQuantizationConfig(
                type=ScalarType.INT8,
                quantile=0.99,
                always_ram=True,
            )
        ),
        optimizers_config=OptimizersConfigDiff(
            # Index kicks in after 20k vectors; below that use brute-force
            indexing_threshold=20_000,
        ),
    )
    print(f"✓ Collection '{COLLECTION_NAME}' created.")


def create_payload_indexes(client: QdrantClient) -> None:
    """
    Payload indexes allow pre-filtering before ANN search (critical for the
    ±3-day date window used during deduplication and geo pre-filters).
    """
    indexes = [
        ("start_at", PayloadSchemaType.INTEGER),   # unix timestamp
        ("source", PayloadSchemaType.KEYWORD),
        ("external_id", PayloadSchemaType.KEYWORD),
        ("event_id", PayloadSchemaType.KEYWORD),
    ]
    for field, schema_type in indexes:
        client.create_payload_index(
            collection_name=COLLECTION_NAME,
            field_name=field,
            field_schema=schema_type,
        )
        print(f"  ✓ Payload index: {field} ({schema_type.value})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialise Qdrant 'events' collection.")
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Drop and recreate the collection (destructive).",
    )
    args = parser.parse_args()

    client = build_client()

    # Verify Qdrant is reachable
    try:
        client.get_collections()
    except Exception as exc:
        print(f"✗ Cannot connect to Qdrant: {exc}", file=sys.stderr)
        sys.exit(1)

    exists = collection_exists(client)

    if exists and not args.recreate:
        print(f"✓ Collection '{COLLECTION_NAME}' already exists — skipping (pass --recreate to reset).")
        info = client.get_collection(COLLECTION_NAME)
        print(f"  vectors_count : {info.vectors_count}")
        print(f"  points_count  : {info.points_count}")
        sys.exit(0)

    if exists and args.recreate:
        client.delete_collection(COLLECTION_NAME)
        print(f"✓ Dropped existing collection '{COLLECTION_NAME}'.")

    create_collection(client)
    create_payload_indexes(client)

    print(f"\nQdrant collection ready: {COLLECTION_NAME}")
    print(f"  vector size : {VECTOR_SIZE}")
    print(f"  distance    : {DISTANCE.value}")


if __name__ == "__main__":
    main()
