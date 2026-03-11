import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Import all models so Alembic can detect schema changes
from app.database import Base  # noqa: F401

# Models imported here for autogenerate support — all Phase 1 entities
# NOTE: oauth_tokens table is owned by Prisma (apps/web). Do NOT import OAuthToken here
# or Alembic will try to manage the table and conflict with Prisma schema changes.
from app.models import Category, Event, Invite, User, UserEvent, Venue  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Whitelist: only manage tables we define in our models.
# This prevents Alembic from ever touching PostGIS / tiger geocoder / topology
# tables that live in the public schema alongside our tables.
_OUR_TABLES: frozenset[str] = frozenset(target_metadata.tables.keys())


def include_object(
    obj: object,  # type: ignore[type-arg]
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: object,  # type: ignore[type-arg]
) -> bool:
    """Return True only for objects that belong to our application schema."""
    if type_ == "table":
        # Only manage tables we explicitly defined in models
        return name in _OUR_TABLES
    if type_ == "index":
        # Only manage indexes on our tables
        table = getattr(obj, "table", None)
        table_name = getattr(table, "name", None) if table is not None else None
        return table_name in _OUR_TABLES
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
