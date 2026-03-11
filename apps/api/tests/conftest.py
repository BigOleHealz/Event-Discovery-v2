"""
Shared pytest fixtures.

Session-scoped setup:
  - Creates `eventdb_test` database if it doesn't exist
  - Installs PostGIS extension
  - Creates all tables via SQLAlchemy metadata (idempotent alongside Alembic)

Per-test isolation:
  - Each test gets a fresh connection with an open transaction that is always
    rolled back at teardown, so tests never leak state.
  - Engine is created per-test to avoid asyncpg event-loop binding issues
    that arise when a single engine is shared across function-scoped loops.
"""

import asyncio
import os
from collections.abc import AsyncGenerator, Generator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.database import Base, get_db
from app.main import create_app

# ---------------------------------------------------------------------------
# Test database URL
# In CI, DATABASE_URL is set to point at the service container (port 5432).
# Locally it defaults to port 5433 (the docker-compose mapping).
# ---------------------------------------------------------------------------
TEST_DB_URL: str = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5433/eventdb_test",
)


def _admin_url(db_url: str) -> str:
    """Replace the database name segment with 'postgres' for admin operations."""
    return db_url.rsplit("/", 1)[0] + "/postgres"


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> Generator[None, None, None]:
    """Create test DB, install PostGIS, and create all tables once per session."""

    async def _setup() -> None:
        db_name = TEST_DB_URL.rsplit("/", 1)[-1]
        admin_engine = create_async_engine(_admin_url(TEST_DB_URL), isolation_level="AUTOCOMMIT")
        async with admin_engine.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": db_name},
            )
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        await admin_engine.dispose()

        engine = create_async_engine(TEST_DB_URL)
        async with engine.begin() as conn:
            # Schema-level reset handles FK ordering and any out-of-band tables
            # (e.g. oauth_tokens) that may exist without being in Base.metadata.
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
            await conn.execute(text("GRANT ALL ON SCHEMA public TO postgres"))
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(_setup())
    yield


@pytest.fixture
async def db_session(setup_test_database: Any) -> AsyncGenerator[AsyncSession, None]:
    """
    Per-test async session.

    Creates a fresh engine + connection per test to avoid asyncpg event-loop
    binding issues.  The connection is always rolled back at teardown so tests
    never leave data in the database.

    Tests that check IntegrityError must call `await db_session.rollback()`
    after catching the exception to clear PostgreSQL's aborted-transaction
    state before the fixture teardown runs.
    """
    engine = create_async_engine(TEST_DB_URL, pool_size=1, max_overflow=0)
    conn = await engine.connect()
    await conn.begin()
    session = AsyncSession(bind=conn, expire_on_commit=False)

    yield session

    await session.close()
    await conn.rollback()
    await conn.close()
    await engine.dispose()


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """FastAPI test client with the database dependency overridden."""

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
