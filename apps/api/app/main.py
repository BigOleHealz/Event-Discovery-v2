from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.config import settings
from app.routers import admin

limiter = Limiter(key_func=get_remote_address, storage_uri=settings.redis_url)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Startup: initialize connections, verify DB, etc.
    yield
    # Shutdown: close connections


def create_app() -> FastAPI:
    app = FastAPI(
        title="Event Discovery API",
        version="0.1.0",
        docs_url="/api/docs" if settings.debug else None,
        redoc_url="/api/redoc" if settings.debug else None,
        openapi_url="/api/openapi.json" if settings.debug else None,
        lifespan=lifespan,
    )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],  # expand in prod via settings
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(admin.router, prefix="/api/v1")

    # Routers registered in later phases
    # from app.routers import events, users, invites, auth
    # app.include_router(events.router, prefix="/api/v1")
    # app.include_router(users.router, prefix="/api/v1")
    # app.include_router(invites.router, prefix="/api/v1")
    # app.include_router(auth.router, prefix="/api/v1")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
