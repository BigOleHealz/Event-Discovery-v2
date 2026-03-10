"""
OpenAI text embedding service.

Model: text-embedding-3-small (1536 dims) — matches VECTOR_SIZE in Qdrant.
Batching: up to 2048 texts per API call (OpenAI hard limit).
"""
import logging
from functools import lru_cache

from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

_MODEL = "text-embedding-3-small"
_BATCH_SIZE = 2048  # OpenAI max per call


@lru_cache(maxsize=1)
def _get_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.openai_api_key)


async def embed(text: str) -> list[float]:
    """Embed a single text string.  Returns a 1536-dimensional vector."""
    vectors = await embed_batch([text])
    return vectors[0]


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """
    Embed multiple texts in one or more batched API calls.

    Preserves input order.  Raises on API errors — callers should catch and
    handle or let the task retry.
    """
    if not texts:
        return []

    client = _get_client()
    all_vectors: list[list[float]] = []

    for i in range(0, len(texts), _BATCH_SIZE):
        batch = texts[i : i + _BATCH_SIZE]
        logger.debug("Embedding batch of %d texts", len(batch))
        response = await client.embeddings.create(model=_MODEL, input=batch)
        # Results are guaranteed to be in the same order as input
        batch_vectors = [item.embedding for item in sorted(response.data, key=lambda x: x.index)]
        all_vectors.extend(batch_vectors)

    return all_vectors
