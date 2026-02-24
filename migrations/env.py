import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

import sys
from pathlib import Path

# Add bot directory to sys.path so we can import bot.*
sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.settings import Settings, se
from bot.db.base import Base
from bot.db import models  # noqa: F401 — регистрируем все модели

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations(settings: Settings) -> None:
    engine: AsyncEngine = create_async_engine(
        url=settings.psql_dsn(is_migration=True),
        connect_args={"ssl": False},
    )
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations(se))


def run_migrations_offline() -> None:
    asyncio.run(run_async_migrations(se))


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
