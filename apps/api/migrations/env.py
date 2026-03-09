import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Import all models so Alembic can detect schema changes
from app.database import Base  # noqa: F401

# Models imported here for autogenerate support — all Phase 1 entities
from app.models import Category, Event, Invite, User, UserEvent, Venue  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# PostGIS ships with its own schemas (tiger, tiger_data, topology) plus the
# spatial_ref_sys table in public.  Exclude them so autogenerate never tries
# to drop or recreate PostGIS internals.
_EXCLUDED_SCHEMAS = {"tiger", "tiger_data", "topology"}
_EXCLUDED_TABLES = {"spatial_ref_sys"}


def include_object(
    obj: object,  # type: ignore[type-arg]
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: object,  # type: ignore[type-arg]
) -> bool:
    if type_ == "table":
        schema = getattr(obj, "schema", None)
        if schema in _EXCLUDED_SCHEMAS:
            return False
        if name in _EXCLUDED_TABLES:
            return False
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
