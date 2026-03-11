"""
FastAPI application — ingestion admin only.

All user-facing API endpoints (auth, users, invites, contacts) are handled by
Next.js API routes in apps/web. This service exposes only:

  POST /api/v1/admin/ingest/trigger   — manually trigger an ingestion run
  GET  /api/v1/admin/ingest/status    — queue depth / last run stats
  GET  /health                        — liveness probe
"""
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import admin


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Event Discovery — Ingestion API",
        version="0.1.0",
        docs_url="/api/docs" if settings.debug else None,
        redoc_url="/api/redoc" if settings.debug else None,
        openapi_url="/api/openapi.json" if settings.debug else None,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.app_base_url],  # set APP_BASE_URL = NEXTAUTH_URL in prod
        allow_credentials=True,
        allow_methods=["POST", "GET"],
        allow_headers=["*"],
    )

    app.include_router(admin.router, prefix="/api/v1")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
