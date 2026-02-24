from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import MonitorUserModel


async def get_user_by_tg_id(session: AsyncSession, tg_user_id: int) -> MonitorUserModel | None:
    return await session.scalar(
        select(MonitorUserModel).where(MonitorUserModel.tg_user_id == tg_user_id)
    )
