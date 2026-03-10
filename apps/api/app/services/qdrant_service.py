"""
Qdrant client factory — singleton per process.

Uses the sync QdrantClient (not the async variant) because Celery tasks run in
a thread pool; the sync client is safe to share across tasks as long as it is
created once per worker process.
"""
from functools import lru_cache

from qdrant_client import QdrantClient

from app.config import settings


@lru_cache(maxsize=1)
def get_qdrant_client() -> QdrantClient:
    """Return the process-level Qdrant client (created once per worker)."""
    return QdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key or None,
        timeout=10,
    )
