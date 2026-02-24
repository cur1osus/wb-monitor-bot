from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from aiogram import BaseMiddleware

from bot.db.func import get_user_by_tg_id
from bot.db.redis import MonitorUserRD

if TYPE_CHECKING:
    from aiogram.types import TelegramObject, User
    from collections.abc import Awaitable, Callable
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession

TG_SERVICE_USER_ID: Final[int] = 777000


class ThrowUserMiddleware(BaseMiddleware):
    """
    Инжектирует модель пользователя в data['user'].

    Порядок: Redis-кэш → PostgreSQL.
    Если пользователь найден в Redis — запрос к БД не делается.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")

        if not user or user.is_bot or user.id == TG_SERVICE_USER_ID:
            return await handler(event, data)

        redis: Redis = data["redis"]
        session: AsyncSession = data["session"]

        # 1. Попробовать Redis-кэш
        cached = await MonitorUserRD.get(redis, user.id)
        if cached:
            data["user"] = cached
            return await handler(event, data)

        # 2. Запрос в PostgreSQL (только при промахе кэша)
        db_user = await get_user_by_tg_id(session, user.id)
        if db_user:
            rd = MonitorUserRD.from_model(db_user)
            await rd.save(redis)   # прогреть кэш
            data["user"] = rd

        return await handler(event, data)
