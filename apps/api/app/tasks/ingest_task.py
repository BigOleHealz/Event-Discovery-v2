"""
Ingestion Celery task.

Flow per CLAUDE.md §2:
  run_ingestion(source)
    → ingester.fetch_events(since)
    → normalizer.normalize(raw)
    → dedup_service.process(event, qdrant_client)  [embed + dedup + qdrant upsert]
    → event_service.upsert(db, event)              [postgres upsert]
    → dedup_service.update_event_id(...)            [back-fill qdrant payload]
    → ingestion_run record updated in DB

Celery tasks are synchronous; async DB/embedding calls run via asyncio.run().
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from celery import shared_task
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.dedup import dedup_service
from app.ingestion import normalizer
from app.models.ingestion_run import IngestionRun
from app.services import event_service, qdrant_service
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _get_ingester(source: str):  # type: ignore[return]
    """Instantiate the right ingester for a given source name."""
    from app.ingestion.eventbrite import EventbriteIngester
    from app.ingestion.facebook import FacebookIngester
    from app.ingestion.meetup import MeetupIngester
    from app.ingestion.serpapi import SerpApiIngester

    ingesters = {
        # "eventbrite": lambda: EventbriteIngester(settings.eventbrite_api_key),
        # "meetup": lambda: MeetupIngester(settings.meetup_api_key),
        # "facebook": lambda: FacebookIngester(""),  # stub
        "serpapi": lambda: SerpApiIngester(settings.serpapi_api_key),
    }
    factory = ingesters.get(source)
    if factory is None:
        raise ValueError(f"Unknown ingestion source: {source!r}")
    return factory()


async def _run_ingestion_async(source: str, run_id: str | None = None) -> dict:
    """
    Core async ingestion logic.  Called by the Celery task via asyncio.run().
    """
    now = datetime.now(tz=timezone.utc)
    since = now - timedelta(hours=7)  # overlap by 1h

    # Accept events from the start of this calendar year up to 2 years out.
    # The upper bound catches date-parser errors that produce far-future dates;
    # the lower bound blocks stale data that upstream APIs sometimes return.
    _year_floor = datetime(now.year, 1, 1, tzinfo=timezone.utc)
    _year_ceil = datetime(now.year + 2, 1, 1, tzinfo=timezone.utc)

    ingester = _get_ingester(source)
    qdrant_client = qdrant_service.get_qdrant_client()

    stats = {"events_fetched": 0, "events_new": 0, "events_deduped": 0, "events_skipped": 0}

    async with AsyncSessionLocal() as db:
        raw_events = await ingester.fetch_events(since)
        stats["events_fetched"] = len(raw_events)

        for raw in raw_events:
            try:
                unified = normalizer.normalize(raw)
            except Exception:
                logger.exception("Normalise error for %s/%s", raw.source, raw.external_id)
                continue

            # Drop events outside the accepted date window
            if not (_year_floor <= unified.start_at < _year_ceil):
                logger.debug(
                    "Skipping out-of-range event %s/%s (start_at=%s)",
                    raw.source,
                    raw.external_id,
                    unified.start_at.date(),
                )
                stats["events_skipped"] += 1
                continue

            try:
                unified = await dedup_service.process(unified, qdrant_client)
            except Exception:
                logger.exception("Dedup error for %s/%s", raw.source, raw.external_id)
                continue

            if unified.canonical_id:
                stats["events_deduped"] += 1

            try:
                event_id, is_new = await event_service.upsert(db, unified)
                if is_new:
                    stats["events_new"] += 1
                # Back-fill the Qdrant payload now that we have the DB UUID
                qdrant_point_id = dedup_service.make_qdrant_id(unified.source, unified.external_id)
                await dedup_service.update_event_id(
                    unified.source, unified.external_id, event_id, qdrant_client
                )
            except Exception:
                logger.exception("DB upsert error for %s/%s", raw.source, raw.external_id)
                await db.rollback()
                continue

        await db.commit()

    return stats


@celery_app.task(
    name="app.tasks.ingest_task.run_ingestion",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
)
def run_ingestion(self, source: str) -> dict:  # type: ignore[misc]
    """
    Celery task: run one full ingestion cycle for the given source.

    Records an IngestionRun in the DB for observability / admin status endpoint.
    """
    started_at = datetime.now(tz=timezone.utc)
    run = IngestionRun(source=source, started_at=started_at)

    async def _execute() -> dict:
        async with AsyncSessionLocal() as db:
            db.add(run)
            await db.flush()

        try:
            stats = await _run_ingestion_async(source)
            run.events_fetched = stats["events_fetched"]
            run.events_new = stats["events_new"]
            run.events_deduped = stats["events_deduped"]
            run.finished_at = datetime.now(tz=timezone.utc)
        except Exception as exc:
            run.error = str(exc)
            run.finished_at = datetime.now(tz=timezone.utc)
            raise
        finally:
            async with AsyncSessionLocal() as db:
                await db.merge(run)
                await db.commit()

        return stats

    stats = asyncio.run(_execute())
    logger.info(
        "Ingestion complete: source=%s fetched=%d new=%d deduped=%d skipped=%d",
        source,
        stats["events_fetched"],
        stats["events_new"],
        stats["events_deduped"],
        stats.get("events_skipped", 0),
    )
    return stats
