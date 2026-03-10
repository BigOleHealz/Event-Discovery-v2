"""
Celery application + Beat schedule.

Broker: Redis (REDIS_URL).  In production, swap to SQS via kombu — same task
code, different broker URL.

Beat schedule: each source runs every 6 hours, staggered by 15 min to spread
API load.
"""
from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery_app = Celery(
    "event_discovery",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.ingest_task"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,       # re-queue on worker crash
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,  # one task at a time per worker (heavy tasks)
    result_expires=3600,       # 1h TTL for task results in Redis
)

celery_app.conf.beat_schedule = {
    # Stagger sources by 15 min to avoid concurrent API floods
    "ingest-eventbrite-every-6h": {
        "task": "app.tasks.ingest_task.run_ingestion",
        "schedule": crontab(minute=0, hour="*/6"),
        "args": ("eventbrite",),
    },
    "ingest-meetup-every-6h": {
        "task": "app.tasks.ingest_task.run_ingestion",
        "schedule": crontab(minute=15, hour="*/6"),
        "args": ("meetup",),
    },
    "ingest-facebook-every-6h": {
        "task": "app.tasks.ingest_task.run_ingestion",
        "schedule": crontab(minute=30, hour="*/6"),
        "args": ("facebook",),
    },
    # SerpApi (Google Events) — runs every 6h, staggered at :45
    "ingest-serpapi-every-6h": {
        "task": "app.tasks.ingest_task.run_ingestion",
        "schedule": crontab(minute=45, hour="*/6"),
        "args": ("serpapi",),
    },
}
