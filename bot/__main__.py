from __future__ import annotations

import asyncio
import logging
from asyncio import CancelledError

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.base import DefaultKeyBuilder
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BotCommand
from redis.asyncio import Redis

from bot import handlers
from bot.db.base import close_db, create_db_session_pool, init_db
from bot.middlewares.throw_session import ThrowDBSessionMiddleware
from bot.middlewares.throw_user import ThrowUserMiddleware
from bot.services.worker import start_worker
from bot.settings import se

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    redis = Redis.from_url(se.redis_url())

    bot = Bot(
        token=se.bot_token,
        session=AiohttpSession(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    storage = RedisStorage(
        redis=redis,
        key_builder=DefaultKeyBuilder(with_bot_id=True, with_destiny=True),
    )

    dp = Dispatcher(
        storage=storage,
        events_isolation=SimpleEventIsolation(),
    )

    # БД
    engine, db_pool = await create_db_session_pool(se)
    await init_db(engine)

    # Middlewares
    dp.update.outer_middleware(ThrowDBSessionMiddleware())
    dp.update.outer_middleware(ThrowUserMiddleware())

    # Shared данные для хендлеров
    dp.workflow_data.update(db_pool=db_pool, redis=redis, se=se)

    dp.include_router(handlers.router)

    await bot.set_my_commands([BotCommand(command="start", description="Главное меню")])
    await bot.delete_webhook(drop_pending_updates=True)

    # Background воркер
    worker_task = await start_worker(db_pool=db_pool, redis=redis, bot=bot)

    try:
        logger.info("Bot started")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
        await close_db(engine)
        await redis.aclose()
        logger.info("Bot stopped")


if __name__ == "__main__":
    try:
        import uvloop
        loop_factory = uvloop.new_event_loop
    except ModuleNotFoundError:
        loop_factory = asyncio.new_event_loop

    try:
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            runner.run(main())
    except (CancelledError, KeyboardInterrupt):
        pass
