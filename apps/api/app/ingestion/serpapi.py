"""
SerpApi Google Events ingester.

Google Events aggregates listings from Eventbrite, Ticketmaster, Meetup, venue
sites, and more.  This ingester queries SerpApi's Google Events engine — which
handles the scraping — and turns the results into RawEvent objects.

Config (from settings):
  SERPAPI_API_KEY       – SerpApi private key (required)
  SERPAPI_LOCATIONS     – Comma-separated city strings, e.g.
                          "San Francisco CA,New York NY,Austin TX"
  SERPAPI_DATE_FILTER   – htichips filter string (default: "date:week")

Pagination: SerpApi returns 10 results per page; we step start=0,10,20...
up to _MAX_PAGES.  We stop early if a page returns fewer than 10 results.

Dedup: external_id is a UUID5 of the canonical event URL so the same event
re-fetched on successive runs always produces the same ID.

Docs: https://serpapi.com/google-events-api
"""

import asyncio
import logging
from datetime import datetime
from uuid import NAMESPACE_URL, uuid5

import httpx

from app.config import settings
from app.ingestion.base import BaseIngester, RawEvent

logger = logging.getLogger(__name__)

_BASE_URL = "https://serpapi.com/search"
_PAGE_SIZE = 10      # SerpApi returns exactly 10 results per page
_MAX_PAGES = 10      # cap at 100 events per location per run
_RETRY_BACKOFF_S = (1.0, 2.0, 4.0)


class SerpApiIngester(BaseIngester):
    """
    Fetch public events via SerpApi's Google Events engine.

    ``api_key`` satisfies the BaseIngester interface; locations and date filter
    are read from ``settings`` at construction time.
    """

    def __init__(self, api_key: str) -> None:
        super().__init__(api_key)
        self._locations: list[str] = [
            loc.strip()
            for loc in (settings.serpapi_locations or "").split(",")
            if loc.strip()
        ]
        self._date_filter: str = settings.serpapi_date_filter

    async def fetch_events(self, since: datetime) -> list[RawEvent]:
        # `since` is unused — Google Events doesn't support a changed_since
        # filter.  We rely on htichips=date:week for recency.
        if not self.api_key:
            logger.warning("SerpApi: SERPAPI_API_KEY not set; skipping.")
            return []
        if not self._locations:
            logger.warning("SerpApi: SERPAPI_LOCATIONS not configured; skipping.")
            return []

        all_events: list[RawEvent] = []

        async with httpx.AsyncClient(timeout=30.0) as client:
            for location in self._locations:
                location_events = await self._fetch_location(client, location)
                all_events.extend(location_events)
                await asyncio.sleep(0.5)  # brief pause between locations

        logger.info(
            "SerpApi: fetched %d events across %d location(s)",
            len(all_events),
            len(self._locations),
        )
        return all_events

    async def _fetch_location(
        self, client: httpx.AsyncClient, location: str
    ) -> list[RawEvent]:
        events: list[RawEvent] = []
        query = f"Events in {location}"

        for page in range(_MAX_PAGES):
            start = page * _PAGE_SIZE
            try:
                data = await self._get(client, query, start)
            except Exception:
                logger.exception("SerpApi: error fetching %r page %d", location, page)
                break

            results: list[dict] = data.get("events_results") or []
            if not results:
                break

            for item in results:
                link = item.get("link") or ""
                # Deterministic external_id: UUID5 of the canonical event URL
                external_id = (
                    str(uuid5(NAMESPACE_URL, link)) if link else f"serpapi-{location}-{start}"
                )
                events.append(
                    RawEvent(
                        source="serpapi",
                        external_id=external_id,
                        # Stash the queried location so the normaliser can fall
                        # back to it when the address array is missing.
                        raw_data={**item, "_location": location},
                    )
                )

            if len(results) < _PAGE_SIZE:
                break  # last page — no point making another request

            await asyncio.sleep(0.3)

        return events

    async def _get(
        self, client: httpx.AsyncClient, query: str, start: int
    ) -> dict:
        params: dict[str, str | int] = {
            "engine": "google_events",
            "q": query,
            "api_key": self.api_key,
            "start": start,
            "hl": "en",
            "gl": "us",
        }
        if self._date_filter:
            params["htichips"] = self._date_filter

        last_exc: Exception | None = None
        for attempt, backoff in enumerate(_RETRY_BACKOFF_S):
            try:
                resp = await client.get(_BASE_URL, params=params)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if exc.response.status_code in (429, 503) and attempt < len(_RETRY_BACKOFF_S) - 1:
                    logger.warning(
                        "SerpApi: HTTP %d on attempt %d, backing off %.1fs",
                        exc.response.status_code,
                        attempt + 1,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                else:
                    raise
            except httpx.RequestError as exc:
                last_exc = exc
                if attempt < len(_RETRY_BACKOFF_S) - 1:
                    await asyncio.sleep(backoff)
                else:
                    raise

        raise RuntimeError("Unreachable") from last_exc
