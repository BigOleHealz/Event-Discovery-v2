"""
Admin endpoints — require X-Admin-API-Key header.

POST /api/v1/admin/ingest/trigger  — kick off an ingestion run for one source
GET  /api/v1/admin/ingest/status   — last run stats + queue depth per source
"""
import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.ingestion_run import IngestionRun

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

_api_key_header = APIKeyHeader(name="X-Admin-API-Key", auto_error=False)

_KNOWN_SOURCES = (
    # "eventbrite",
    # "meetup",
    # "facebook",
    "serpapi"
    )


def _require_admin_key(api_key: str | None = Depends(_api_key_header)) -> None:
    if not api_key or api_key != settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing X-Admin-API-Key header.",
        )


AdminAuth = Annotated[None, Depends(_require_admin_key)]


class TriggerResponse(BaseModel):
    task_id: str
    source: str
    queued_at: datetime


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    result: dict | str | None


class RunSummary(BaseModel):
    source: str
    started_at: datetime | None
    finished_at: datetime | None
    events_fetched: int
    events_new: int
    events_deduped: int
    error: str | None
    duration_s: float | None


class StatusResponse(BaseModel):
    runs: list[RunSummary]


@router.post(
    "/ingest/trigger",
    response_model=TriggerResponse,
    summary="Manually trigger an ingestion run",
)
async def trigger_ingestion(
    _: AdminAuth,
    source: str = Query(
        ...,
        description="Source to ingest",
        pattern="^(eventbrite|meetup|facebook|serpapi)$",
    ),
) -> TriggerResponse:
    from app.tasks.ingest_task import run_ingestion

    task = run_ingestion.apply_async(args=[source])
    logger.info("Admin triggered ingestion for source=%s task_id=%s", source, task.id)

    return TriggerResponse(
        task_id=task.id,
        source=source,
        queued_at=datetime.now(tz=timezone.utc),
    )


@router.get(
    "/ingest/task/{task_id}",
    response_model=TaskStatusResponse,
    summary="Poll the result of a specific ingestion task",
)
async def task_status(_: AdminAuth, task_id: str) -> TaskStatusResponse:
    from celery.result import AsyncResult

    from app.tasks.celery_app import celery_app

    result = AsyncResult(task_id, app=celery_app)
    raw = None
    if result.ready():
        try:
            raw = result.get(propagate=False)
        except Exception as exc:
            raw = str(exc)

    return TaskStatusResponse(
        task_id=task_id,
        status=result.status,
        result=raw,
    )


@router.get(
    "/ingest/status",
    response_model=StatusResponse,
    summary="Last ingestion run stats per source",
)
async def ingestion_status(
    _: AdminAuth,
    db: AsyncSession = Depends(get_db),
) -> StatusResponse:
    runs: list[RunSummary] = []

    for source in _KNOWN_SOURCES:
        stmt = (
            select(IngestionRun)
            .where(IngestionRun.source == source)
            .order_by(IngestionRun.started_at.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        run = result.scalar_one_or_none()

        if run:
            duration = None
            if run.finished_at and run.started_at:
                duration = (run.finished_at - run.started_at).total_seconds()

            runs.append(
                RunSummary(
                    source=run.source,
                    started_at=run.started_at,
                    finished_at=run.finished_at,
                    events_fetched=run.events_fetched,
                    events_new=run.events_new,
                    events_deduped=run.events_deduped,
                    error=run.error,
                    duration_s=duration,
                )
            )
        else:
            runs.append(
                RunSummary(
                    source=source,
                    started_at=None,
                    finished_at=None,
                    events_fetched=0,
                    events_new=0,
                    events_deduped=0,
                    error=None,
                    duration_s=None,
                )
            )

    return StatusResponse(runs=runs)
