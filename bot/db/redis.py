"""
Redis ORM — лёгкий кэш-слой для часто запрашиваемых данных.

Структура:
  MonitorUserRD  — кэш пользователя (plan, pro_expires_at, referral_code, …)
  WbItemCacheRD  — кэш WB-товара (price, in_stock, sizes, …)
  WbSimilarSearchCacheRD — кэш похожих товаров для кнопки «Найти дешевле»
  WorkerStateRD  — состояние background-воркера (heartbeat, длительность цикла)

Использование:
  user = await MonitorUserRD.get(redis, tg_user_id)
  if user is None:
      user = await get_or_create_monitor_user(session, tg_user_id, username)
      await MonitorUserRD.from_model(user).save(redis)
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final

import msgspec
from redis.asyncio import Redis

if TYPE_CHECKING:
    from bot.db.models import MonitorUserModel

# ─── Shared encoder (thread-safe, reusable) ──────────────────────────────────
_ENC: Final[msgspec.msgpack.Encoder] = msgspec.msgpack.Encoder()


# ─── Base mixin ──────────────────────────────────────────────────────────────
class _RDBase(msgspec.Struct, kw_only=True, array_like=True):
    """Base class — provides save/get/delete helpers via msgpack + Redis."""

    # Subclasses MUST define:
    #   key_prefix: ClassVar[str]  — unique key prefix
    #   ttl: ClassVar[int]         — TTL in seconds

    @classmethod
    def _key(cls, *parts: int | str) -> str:
        return f"{cls.__name__}:" + ":".join(str(p) for p in parts)

    @classmethod
    async def _get_raw(cls, redis: Redis, *parts: int | str) -> bytes | None:
        return await redis.get(cls._key(*parts))

    async def _save_raw(self, redis: Redis, *parts: int | str, ttl: int) -> None:
        await redis.setex(self._key(*parts), ttl, _ENC.encode(self))

    @classmethod
    async def _delete_raw(cls, redis: Redis, *parts: int | str) -> None:
        await redis.delete(cls._key(*parts))


# ─── MonitorUserRD ────────────────────────────────────────────────────────────
_USER_TTL: Final[int] = int(timedelta(hours=2).total_seconds())


class MonitorUserRD(_RDBase):
    """Кэш пользователя в Redis. Обновляется при каждом изменении плана."""

    tg_user_id: int
    username: str | None = None
    plan: str = "free"
    pro_expires_at: str | None = None  # ISO-формат datetime
    referral_code: str | None = None
    referred_by_tg_user_id: int | None = None

    # ── фабрика из SQLAlchemy-модели ─────────────────────────────────────────
    @classmethod
    def from_model(cls, m: "MonitorUserModel") -> "MonitorUserRD":
        return cls(
            tg_user_id=m.tg_user_id,
            username=m.username,
            plan=m.plan,
            pro_expires_at=m.pro_expires_at.isoformat() if m.pro_expires_at else None,
            referral_code=m.referral_code,
            referred_by_tg_user_id=m.referred_by_tg_user_id,
        )

    # ── Redis helpers ─────────────────────────────────────────────────────────
    @classmethod
    async def get(cls, redis: Redis, tg_user_id: int) -> "MonitorUserRD | None":
        data = await cls._get_raw(redis, tg_user_id)
        return msgspec.msgpack.decode(data, type=cls) if data else None

    async def save(self, redis: Redis) -> None:
        await self._save_raw(redis, self.tg_user_id, ttl=_USER_TTL)

    @classmethod
    async def invalidate(cls, redis: Redis, tg_user_id: int) -> None:
        """Вызывать при изменении плана/данных пользователя."""
        await cls._delete_raw(redis, tg_user_id)

    # ── удобные свойства ──────────────────────────────────────────────────────
    def is_pro(self) -> bool:
        if self.plan != "pro":
            return False
        if self.pro_expires_at:
            return datetime.fromisoformat(self.pro_expires_at) > datetime.utcnow()  # noqa: DTZ003
        return True


# ─── WbItemCacheRD ────────────────────────────────────────────────────────────
_WB_TTL: Final[int] = int(timedelta(minutes=30).total_seconds())


class WbItemCacheRD(_RDBase):
    """Кэш WB-товара. TTL 30 мин, ключ — артикул товара."""

    wb_item_id: int
    title: str | None = None
    price: str | None = None  # строковый Decimal для точности
    in_stock: bool = False
    total_qty: int | None = None
    sizes: list[str] = []

    @classmethod
    async def get(cls, redis: Redis, wb_item_id: int) -> "WbItemCacheRD | None":
        data = await cls._get_raw(redis, wb_item_id)
        return msgspec.msgpack.decode(data, type=cls) if data else None

    async def save(self, redis: Redis) -> None:
        await self._save_raw(redis, self.wb_item_id, ttl=_WB_TTL)

    @classmethod
    async def invalidate(cls, redis: Redis, wb_item_id: int) -> None:
        await cls._delete_raw(redis, wb_item_id)


# ─── WbSimilarSearchCacheRD ───────────────────────────────────────────────────
_WB_SIMILAR_TTL: Final[int] = int(timedelta(minutes=10).total_seconds())


class WbSimilarItemRD(msgspec.Struct, kw_only=True, array_like=True):
    wb_item_id: int
    title: str
    price: str
    url: str


class WbSimilarSearchCacheRD(_RDBase):
    """Кэш результата поиска «похожих дешевле». TTL 10 минут."""

    track_id: int
    base_price: str
    items: list[WbSimilarItemRD] = []

    @classmethod
    async def get(
        cls,
        redis: Redis,
        track_id: int,
    ) -> "WbSimilarSearchCacheRD | None":
        data = await cls._get_raw(redis, track_id)
        return msgspec.msgpack.decode(data, type=cls) if data else None

    async def save(self, redis: Redis) -> None:
        await self._save_raw(redis, self.track_id, ttl=_WB_SIMILAR_TTL)


# ─── WorkerStateRD ────────────────────────────────────────────────────────────
_WORKER_TTL: Final[int] = int(timedelta(hours=1).total_seconds())
_WORKER_KEY: Final[str] = "WorkerStateRD:state"


class WorkerStateRD(_RDBase):
    """Состояние фонового воркера — heartbeat и длительность цикла."""

    last_ok: str | None = None  # ISO timestamp последнего успешного цикла
    cycle_sec: float | None = None  # длительность последнего цикла в секундах

    @classmethod
    async def get(cls, redis: Redis) -> "WorkerStateRD | None":
        data = await redis.get(_WORKER_KEY)
        return msgspec.msgpack.decode(data, type=cls) if data else None

    async def save(self, redis: Redis) -> None:
        await redis.setex(_WORKER_KEY, _WORKER_TTL, _ENC.encode(self))

    @classmethod
    async def set_heartbeat(cls, redis: Redis, ts: str) -> None:
        state = await cls.get(redis) or cls()
        state.last_ok = ts
        await state.save(redis)

    @classmethod
    async def set_cycle_duration(cls, redis: Redis, sec: float) -> None:
        state = await cls.get(redis) or cls()
        state.cycle_sec = sec
        await state.save(redis)
