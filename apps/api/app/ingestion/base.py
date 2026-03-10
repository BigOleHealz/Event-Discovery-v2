"""Abstract base class for all event source ingesters."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RawEvent:
    """Minimal container for a single raw event from any source."""

    source: str
    external_id: str
    raw_data: dict = field(default_factory=dict)


class BaseIngester(ABC):
    """
    All source-specific ingesters inherit from this class.

    Subclasses must implement `fetch_events` which returns a flat list of
    `RawEvent` objects.  Rate limiting, retries, and pagination are the
    responsibility of each subclass; the base class provides `_with_retry` to
    wrap individual HTTP calls with exponential backoff.
    """

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    @abstractmethod
    async def fetch_events(self, since: datetime) -> list[RawEvent]:
        """
        Fetch events modified or created after `since`.

        Returns a list of raw (un-normalised) events.  The caller (ingest_task)
        is responsible for passing them through the normaliser.
        """
        ...
