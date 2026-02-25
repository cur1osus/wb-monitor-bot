from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from html import escape
from typing import TYPE_CHECKING

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
    PreCheckoutQuery,
)
from sqlalchemy import select

from bot.db.models import TrackModel
from bot.db.redis import MonitorUserRD, WbSimilarItemRD, WbSimilarSearchCacheRD
from bot.keyboards.inline import (
    add_item_prompt_kb,
    back_to_dashboard_kb,
    dashboard_kb,
    dashboard_text,
    format_track_text,
    admin_grant_pro_kb,
    admin_panel_kb,
    plan_kb,
    paged_track_kb,
    ref_kb,
    settings_kb,
)
from bot.services.repository import (
    count_user_tracks,
    create_track,
    get_or_create_monitor_user,
    get_user_track_by_id,
    get_user_tracks,
    toggle_track_active,
    delete_track,
    add_referral_reward_once,
    get_monitor_user_by_tg_id,
    get_admin_stats,
    set_user_tracks_interval,
)
from bot.services.utils import is_admin
from bot.services.wb_client import (
    extract_wb_item_id,
    fetch_product,
    search_similar_cheaper,
)
from bot.services.config import FREE_INTERVAL, PRO_INTERVAL
from bot.settings import se

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession

    from bot.services.repository import AdminStats

router = Router()
logger = logging.getLogger(__name__)


class SettingsState(StatesGroup):
    waiting_for_price = State()
    waiting_for_drop = State()
    waiting_for_sizes = State()
    waiting_for_pro_grant = State()


@router.callback_query(F.data == "wbm:home:0")
async def wb_home_cb(cb: CallbackQuery, session: AsyncSession, redis: "Redis") -> None:
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    used = await count_user_tracks(session, user.id, active_only=True)
    await cb.message.edit_text(
        dashboard_text(user.plan, used),
        reply_markup=dashboard_kb(is_admin(cb.from_user.id, se)),
    )


@router.callback_query(F.data == "wbm:add:0")
async def wb_add_cb(cb: CallbackQuery) -> None:
    await cb.message.edit_text(
        "📎 Отправьте ссылку на товар Wildberries или его артикул (6-12 цифр).",
        reply_markup=add_item_prompt_kb(),
    )


@router.message(StateFilter(None), F.text.regexp(r"https?://.*wildberries.*|\d{6,12}"))
async def wb_add_item_from_text(
    msg: Message,
    session: AsyncSession,
    redis: "Redis",
) -> None:
    url_or_text = msg.text.strip()
    wb_item_id = extract_wb_item_id(url_or_text)

    if not wb_item_id:
        await msg.answer(
            "❌ Не удалось распознать ссылку WB. Отправьте корректную ссылку."
        )
        return

    user = await get_or_create_monitor_user(
        session, msg.from_user.id, msg.from_user.username, redis=redis
    )

    existing = await session.scalar(
        select(TrackModel).where(
            TrackModel.user_id == user.id,
            TrackModel.wb_item_id == wb_item_id,
            TrackModel.is_deleted.is_(False),
        )
    )
    if existing:
        await msg.answer("⚠️ Вы уже отслеживаете этот товар.")
        return

    track_count = await count_user_tracks(session, user.id, active_only=True)
    limit = 50 if user.plan == "pro" else 5
    if track_count >= limit:
        await msg.answer(f"❌ Достигнут лимит треков ({limit}). Обновитесь до Pro!")
        return

    product = await fetch_product(redis, wb_item_id)
    if not product:
        await msg.answer("❌ Не удалось получить данные о товаре. Проверьте ссылку.")
        return

    interval = PRO_INTERVAL if user.plan == "pro" else FREE_INTERVAL
    track_url = (
        url_or_text
        if url_or_text.startswith("http")
        else f"https://www.wildberries.ru/catalog/{wb_item_id}/detail.aspx"
    )
    track = await create_track(
        session,
        user.id,
        wb_item_id,
        track_url,
        product.title,
        product.price,
        product.in_stock,
        product.total_qty,
        product.sizes,
        interval,
    )
    await session.commit()

    await msg.answer(
        f"✅ Товар добавлен в отслеживание!\n\n"
        f"📦 {product.title}\n"
        f"💰 Цена: {f'{product.price}₽' if product.price else 'не указана'}\n"
        f"📦 В наличии: {'да' if product.in_stock else 'нет'}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔎 Найти дешевле", callback_data=f"wbm:cheap:{track.id}"
                    )
                ],
                [InlineKeyboardButton(text="📦 Мои треки", callback_data="wbm:list:0")],
                [InlineKeyboardButton(text="◀ В меню", callback_data="wbm:home:0")],
            ]
        ),
    )


@router.callback_query(F.data == "wbm:list:0")
async def wb_list_cb(cb: CallbackQuery, session: AsyncSession) -> None:
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    tracks = await get_user_tracks(session, user.id)
    if not tracks:
        await cb.answer("У вас нет активных треков", show_alert=True)
        return
    track = tracks[0]
    await cb.message.edit_text(
        format_track_text(track), reply_markup=paged_track_kb(track, 0, len(tracks))
    )


@router.callback_query(F.data.regexp(r"wbm:page:(\d+)"))
async def wb_page_cb(cb: CallbackQuery, session: AsyncSession) -> None:
    page = int(cb.data.split(":")[2])
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    tracks = await get_user_tracks(session, user.id)
    if not tracks or page >= len(tracks):
        await cb.answer("Недействительная страница", show_alert=True)
        return
    track = tracks[page]
    await cb.message.edit_text(
        format_track_text(track), reply_markup=paged_track_kb(track, page, len(tracks))
    )


@router.callback_query(F.data.regexp(r"wbm:pause:(\d+)"))
async def wb_pause_cb(cb: CallbackQuery, session: AsyncSession) -> None:
    track_id = int(cb.data.split(":")[2])
    await toggle_track_active(session, track_id, False)
    await session.commit()
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    tracks = await get_user_tracks(session, user.id)
    for idx, track in enumerate(tracks):
        if track.id == track_id:
            await cb.message.edit_text(
                format_track_text(track),
                reply_markup=paged_track_kb(track, idx, len(tracks)),
            )
            break


@router.callback_query(F.data.regexp(r"wbm:resume:(\d+)"))
async def wb_resume_cb(cb: CallbackQuery, session: AsyncSession) -> None:
    track_id = int(cb.data.split(":")[2])
    await toggle_track_active(session, track_id, True)
    await session.commit()
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    tracks = await get_user_tracks(session, user.id)
    for idx, track in enumerate(tracks):
        if track.id == track_id:
            await cb.message.edit_text(
                format_track_text(track),
                reply_markup=paged_track_kb(track, idx, len(tracks)),
            )
            break


@router.callback_query(F.data.regexp(r"wbm:remove:(\d+)"))
async def wb_remove_cb(cb: CallbackQuery, session: AsyncSession) -> None:
    track_id = int(cb.data.split(":")[2])
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    tracks = await get_user_tracks(session, user.id)
    for idx, track in enumerate(tracks):
        if track.id == track_id:
            await cb.message.edit_text(
                format_track_text(track),
                reply_markup=paged_track_kb(
                    track,
                    idx,
                    len(tracks),
                    confirm_remove=True,
                ),
            )
            await cb.answer("Подтвердите удаление")
            return
    await cb.answer("Трек не найден", show_alert=True)


@router.callback_query(F.data.regexp(r"wbm:remove_no:(\d+)"))
async def wb_remove_no_cb(cb: CallbackQuery, session: AsyncSession) -> None:
    track_id = int(cb.data.split(":")[2])
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    tracks = await get_user_tracks(session, user.id)
    for idx, track in enumerate(tracks):
        if track.id == track_id:
            await cb.message.edit_text(
                format_track_text(track),
                reply_markup=paged_track_kb(track, idx, len(tracks)),
            )
            await cb.answer("Удаление отменено")
            return
    await cb.answer("Трек не найден", show_alert=True)


@router.callback_query(F.data.regexp(r"wbm:remove_yes:(\d+)"))
async def wb_remove_yes_cb(cb: CallbackQuery, session: AsyncSession) -> None:
    track_id = int(cb.data.split(":")[2])
    await delete_track(session, track_id)
    await session.commit()
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    used = await count_user_tracks(session, user.id, active_only=True)
    await cb.message.edit_text(
        dashboard_text(user.plan, used),
        reply_markup=dashboard_kb(is_admin(cb.from_user.id, se)),
    )
    await cb.answer("Трек удален")


@router.callback_query(F.data.regexp(r"wbm:cheap:(\d+)"))
async def wb_find_cheaper_cb(
    cb: CallbackQuery, session: AsyncSession, redis: "Redis"
) -> None:
    track_id = int(cb.data.split(":")[2])
    track = await get_user_track_by_id(session, track_id)
    if not track:
        await cb.answer("Трек не найден", show_alert=True)
        return

    back_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀️ К моим товарам", callback_data="wbm:list:0")]
        ]
    )

    await cb.message.edit_text(
        f"🔎 Ищу похожие товары дешевле для <b>{escape(track.title)}</b>...",
        reply_markup=back_kb,
    )
    await cb.answer("Ищу варианты...")

    cached = await WbSimilarSearchCacheRD.get(redis, track.id)
    if cached is None:
        current = await fetch_product(redis, track.wb_item_id, use_cache=False)
        if not current or current.price is None:
            await cb.message.edit_text(
                "❌ Не удалось получить текущую цену товара.",
                reply_markup=back_kb,
            )
            return

        found = await search_similar_cheaper(
            base_title=current.title or track.title,
            base_entity=current.entity,
            base_brand=current.brand,
            base_subject_id=current.subject_id,
            max_price=current.price,
            exclude_wb_item_id=track.wb_item_id,
            limit=5,
        )
        alternatives = [
            WbSimilarItemRD(
                wb_item_id=item.wb_item_id,
                title=item.title,
                price=str(item.price),
                url=item.url,
            )
            for item in found
        ]
        current_price_text = str(current.price)
        await WbSimilarSearchCacheRD(
            track_id=track.id,
            base_price=current_price_text,
            items=alternatives,
        ).save(redis)
    else:
        alternatives = cached.items
        current_price_text = cached.base_price

    if not alternatives:
        await cb.message.edit_text(
            f"🔎 Для <b>{escape(track.title)}</b> не нашлось похожих товаров дешевле <b>{current_price_text} ₽</b>.",
            reply_markup=back_kb,
        )
        return

    lines = [
        f"🔎 Похожие товары дешевле <b>{current_price_text} ₽</b> для <b>{escape(track.title)}</b>",
        "",
    ]
    for idx, item in enumerate(alternatives, start=1):
        lines.append(
            f'{idx}. <a href="{item.url}">{escape(item.title)}</a> — <b>{item.price} ₽</b>'
        )
    lines.append("")
    lines.append("⚠️ Сверяйте характеристики перед покупкой.")

    await cb.message.edit_text(
        "\n".join(lines),
        reply_markup=back_kb,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


@router.callback_query(F.data.regexp(r"wbm:settings:(\d+)"))
async def wb_settings_cb(cb: CallbackQuery, session: AsyncSession) -> None:
    track_id = int(cb.data.split(":")[2])
    track = await get_user_track_by_id(session, track_id)
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    if not track:
        await cb.answer("Трек не найден", show_alert=True)
        return
    await cb.message.edit_text(
        format_track_text(track) + "\n\n⚙️ Настройки:",
        reply_markup=settings_kb(
            track_id,
            has_sizes=bool(track.watch_sizes),
            pro_plan=user.plan == "pro",
            qty_on=track.watch_qty,
        ),
    )


@router.callback_query(F.data == "wbm:plan:0")
async def wb_plan_cb(cb: CallbackQuery, session: AsyncSession) -> None:
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    used = await count_user_tracks(session, user.id, active_only=True)
    limit = 50 if user.plan == "pro" else 5
    interval = 60 if user.plan == "pro" else 360

    is_pro = user.plan == "pro"
    expires_str = (
        user.pro_expires_at.strftime("%d.%m.%Y")
        if (is_pro and user.pro_expires_at)
        else None
    )
    text = (
        f"💳 <b>Ваш тариф: {user.plan.upper()}</b>\n\n"
        f"📦 Треков: {used}/{limit}\n"
        f"⏱ Интервал проверок: {interval} мин\n\n"
    )
    if is_pro:
        text += "✅ Pro активен\n"
    else:
        text += "🚀 Обновитесь до <b>PRO</b> — 50 треков, проверка каждый час!"

    await cb.message.edit_text(text, reply_markup=plan_kb(is_pro, expires_str))


@router.callback_query(F.data == "wbm:pay:stars")
async def wb_pay_stars_cb(cb: CallbackQuery, session: AsyncSession) -> None:
    from aiogram.types import LabeledPrice

    await get_or_create_monitor_user(session, cb.from_user.id, cb.from_user.username)
    await cb.message.answer_invoice(
        title="WB Monitor Pro",
        description="Доступ к Pro на 30 дней",
        payload="wbm_pro_30d",
        currency="XTR",
        prices=[LabeledPrice(label="Pro (30 дней)", amount=150)],
        provider_token="",
    )


@router.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery) -> None:
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment_handler(
    msg: Message, session: AsyncSession, redis: "Redis"
) -> None:
    payment = msg.successful_payment
    user = await get_or_create_monitor_user(
        session, msg.from_user.id, msg.from_user.username
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    base_expiry = (
        user.pro_expires_at
        if user.pro_expires_at and user.pro_expires_at > now
        else now
    )
    user.plan = "pro"
    user.pro_expires_at = base_expiry + timedelta(days=30)
    await set_user_tracks_interval(session, user.id, PRO_INTERVAL)

    referral_bonus_applied = False
    if user.referred_by_tg_user_id and payment.telegram_payment_charge_id:
        referrer = await get_monitor_user_by_tg_id(session, user.referred_by_tg_user_id)
        if referrer:
            created = await add_referral_reward_once(
                session,
                referrer_user_id=referrer.id,
                invited_user_id=user.id,
                invited_tg_user_id=user.tg_user_id,
                payment_charge_id=payment.telegram_payment_charge_id,
                rewarded_days=7,
            )
            if created:
                ref_base = (
                    referrer.pro_expires_at
                    if referrer.pro_expires_at and referrer.pro_expires_at > now
                    else now
                )
                referrer.plan = "pro"
                referrer.pro_expires_at = ref_base + timedelta(days=7)
                await set_user_tracks_interval(session, referrer.id, PRO_INTERVAL)
                referral_bonus_applied = True
                # Инвалидируем кэш реферера
                await MonitorUserRD.invalidate(redis, referrer.tg_user_id)
                try:
                    await msg.bot.send_message(
                        referrer.tg_user_id, "🎉 По рефералке начислено +7 дней Pro!"
                    )
                except Exception:
                    pass

    await session.commit()

    # Инвалидируем кэш текущего пользователя (план изменился)
    await MonitorUserRD.invalidate(redis, msg.from_user.id)

    text = "✅ Pro активирован. Доступ продлен на 30 дней."
    if referral_bonus_applied:
        text += "\n🎁 Реферальный бонус пригласившему (+7 дней) начислен."
    await msg.answer(text)


@router.callback_query(F.data == "wbm:ref:0")
async def wb_ref_cb(cb: CallbackQuery, session: AsyncSession) -> None:
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    await session.commit()
    bot_me = await cb.bot.me()
    ref_link = f"https://t.me/{bot_me.username}?start=ref_{user.referral_code}"
    await cb.message.edit_text(
        f"👥 <b>Реферальная программа</b>\n\nПриглашайте друзей и получайте <b>+7 дней Pro</b> за каждую оплату!\n\nВаша ссылка:\n<code>{ref_link}</code>",
        reply_markup=ref_kb(ref_link),
    )


@router.callback_query(F.data == "wbm:help:0")
async def wb_help_cb(cb: CallbackQuery) -> None:
    await cb.message.edit_text(
        "❓ <b>Помощь WB Monitor</b>\n\n/start - Главное меню\n\nБот отслеживает цены и наличие товаров на Wildberries.\nПросто отправьте ссылку на товар или его артикул.",
        reply_markup=back_to_dashboard_kb(is_admin(cb.from_user.id, se)),
    )


@router.callback_query(F.data == "wbm:admin:0")
async def wb_admin_cb(
    cb: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer("❌ Нет доступа", show_alert=True)
        return

    await state.clear()

    stats = await get_admin_stats(session, days=7)
    await cb.message.edit_text(
        _admin_stats_text(stats),
        reply_markup=admin_panel_kb(selected_days=7),
    )


def _admin_stats_text(stats: "AdminStats") -> str:
    return (
        f"🛠 <b>Админ панель</b>\n"
        f"Период: <b>{stats.days} дней</b>\n\n"
        f"👥 Пользователи: <b>{stats.total_users}</b> (новых: +{stats.new_users})\n"
        f"⭐ PRO активных: <b>{stats.pro_users}</b>\n"
        f"📦 Треки: <b>{stats.total_tracks}</b> (активных: {stats.active_tracks}, новых: +{stats.new_tracks})\n"
        f"🔁 Проверок (snapshots): <b>{stats.checks_count}</b>\n"
        f"🔔 Уведомлений: <b>{stats.alerts_count}</b>"
    )


@router.callback_query(F.data.regexp(r"wbm:admin:stats:(\d+)"))
async def wb_admin_stats_cb(
    cb: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer("❌ Нет доступа", show_alert=True)
        return

    await state.clear()

    days = int(cb.data.split(":")[3])
    if days not in {7, 14, 30}:
        await cb.answer("Недоступный период", show_alert=True)
        return

    stats = await get_admin_stats(session, days=days)
    await cb.message.edit_text(
        _admin_stats_text(stats),
        reply_markup=admin_panel_kb(selected_days=days),
    )


@router.callback_query(F.data == "wbm:admin:grantpro")
async def wb_admin_grant_pro_cb(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer("❌ Нет доступа", show_alert=True)
        return

    await state.set_state(SettingsState.waiting_for_pro_grant)
    await cb.message.edit_text(
        "🎁 <b>Выдать PRO</b>\n\n"
        "Отправьте данные в формате:\n"
        "<code>tg_id дни</code>\n\n"
        "Пример:\n"
        "<code>123456789 30</code>",
        reply_markup=admin_grant_pro_kb(),
    )


def _parse_grant_pro_payload(text: str) -> tuple[int, int] | None:
    parts = text.replace(",", " ").split()
    if len(parts) != 2:
        return None
    try:
        tg_user_id = int(parts[0])
        days = int(parts[1])
    except ValueError:
        return None
    if tg_user_id <= 0 or not (1 <= days <= 365):
        return None
    return tg_user_id, days


@router.message(SettingsState.waiting_for_pro_grant, F.text)
async def wb_admin_grant_pro_msg(
    msg: Message,
    state: FSMContext,
    session: AsyncSession,
    redis: "Redis",
) -> None:
    if not msg.from_user:
        await state.clear()
        return

    if not is_admin(msg.from_user.id, se):
        await state.clear()
        return

    parsed = _parse_grant_pro_payload(msg.text.strip())
    if parsed is None:
        await msg.answer(
            "❌ Неверный формат. Используйте: <code>tg_id дни</code> (дни от 1 до 365).",
            reply_markup=admin_grant_pro_kb(),
        )
        return

    tg_user_id, days = parsed
    user = await get_monitor_user_by_tg_id(session, tg_user_id)
    if not user:
        await msg.answer(
            "❌ Пользователь не найден. Он должен хотя бы один раз запустить бота (/start).",
            reply_markup=admin_grant_pro_kb(),
        )
        return

    now = datetime.now(UTC).replace(tzinfo=None)
    base_expiry = (
        user.pro_expires_at
        if user.pro_expires_at and user.pro_expires_at > now
        else now
    )
    user.plan = "pro"
    user.pro_expires_at = base_expiry + timedelta(days=days)
    await set_user_tracks_interval(session, user.id, PRO_INTERVAL)
    await session.commit()
    await MonitorUserRD.invalidate(redis, user.tg_user_id)

    await state.clear()
    await msg.answer(
        f"✅ Пользователю <code>{user.tg_user_id}</code> выдан PRO на <b>{days}</b> дн.\n"
        f"Действует до: <b>{user.pro_expires_at.strftime('%d.%m.%Y %H:%M')}</b>",
        reply_markup=admin_panel_kb(selected_days=7),
    )

    try:
        await msg.bot.send_message(
            user.tg_user_id,
            f"🎉 Вам активирован PRO на <b>{days}</b> дн.\n"
            f"Действует до: <b>{user.pro_expires_at.strftime('%d.%m.%Y %H:%M')}</b>",
        )
    except Exception:
        pass


@router.callback_query(F.data == "wbm:cancel:0")
async def wb_cancel_cb(
    cb: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    await state.clear()
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    used = await count_user_tracks(session, user.id, active_only=True)
    await cb.message.edit_text(
        dashboard_text(user.plan, used),
        reply_markup=dashboard_kb(is_admin(cb.from_user.id, se)),
    )


@router.callback_query(F.data.regexp(r"wbm:back:(\d+)"))
async def wb_back_cb(cb: CallbackQuery, session: AsyncSession) -> None:
    track_id = int(cb.data.split(":")[2])
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    tracks = await get_user_tracks(session, user.id)
    for idx, track in enumerate(tracks):
        if track.id == track_id:
            await cb.message.edit_text(
                format_track_text(track),
                reply_markup=paged_track_kb(track, idx, len(tracks)),
            )
            break


# ─── Settings Handlers ───────────────────────────────────────────────────────


async def _hide_settings_prompt_keyboard(msg: Message, state: FSMContext) -> None:
    data = await state.get_data()
    prompt_message_id = data.get("prompt_message_id")
    if not isinstance(prompt_message_id, int):
        return
    try:
        await msg.bot.edit_message_reply_markup(
            chat_id=msg.chat.id,
            message_id=prompt_message_id,
            reply_markup=None,
        )
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.regexp(r"wbm:price:(\d+)"))
async def wb_settings_price_cb(cb: CallbackQuery, state: FSMContext) -> None:
    track_id = int(cb.data.split(":")[2])
    await state.update_data(track_id=track_id, prompt_message_id=cb.message.message_id)
    await state.set_state(SettingsState.waiting_for_price)

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отмена", callback_data=f"wbm:settings:{track_id}"
                )
            ]
        ]
    )
    await cb.message.edit_text(
        "🎯 Введите желаемую цену (в рублях):\nНапример: 1500 или 1500.50",
        reply_markup=cancel_kb,
    )


@router.message(SettingsState.waiting_for_price, F.text)
async def wb_settings_price_msg(
    msg: Message, state: FSMContext, session: AsyncSession
) -> None:
    if not msg.from_user:
        await state.clear()
        return

    data = await state.get_data()
    track_id = data.get("track_id")
    if not track_id:
        await state.clear()
        return

    try:
        new_price = float(msg.text.strip().replace(",", "."))
        if new_price < 0:
            raise ValueError
    except ValueError:
        await msg.answer("❌ Некорректная цена. Введите положительное число.")
        return

    track = await get_user_track_by_id(session, track_id)
    if track:
        track.target_price = new_price
        user = await get_or_create_monitor_user(
            session, msg.from_user.id, msg.from_user.username
        )
        await session.commit()
        await _hide_settings_prompt_keyboard(msg, state)
        await msg.answer(
            f"✅ Целевая цена для <b>{track.title}</b> установлена: {new_price} ₽",
            reply_markup=settings_kb(
                track_id,
                has_sizes=bool(track.last_sizes),
                pro_plan=user.plan == "pro",
                qty_on=track.watch_qty,
            ),
        )

    await state.clear()


@router.callback_query(F.data.regexp(r"wbm:drop:(\d+)"))
async def wb_settings_drop_cb(cb: CallbackQuery, state: FSMContext) -> None:
    track_id = int(cb.data.split(":")[2])
    await state.update_data(track_id=track_id, prompt_message_id=cb.message.message_id)
    await state.set_state(SettingsState.waiting_for_drop)

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отмена", callback_data=f"wbm:settings:{track_id}"
                )
            ]
        ]
    )
    await cb.message.edit_text(
        "📉 Введите процент падения (например, 10 для 10%):", reply_markup=cancel_kb
    )


@router.message(SettingsState.waiting_for_drop, F.text)
async def wb_settings_drop_msg(
    msg: Message, state: FSMContext, session: AsyncSession
) -> None:
    if not msg.from_user:
        await state.clear()
        return

    data = await state.get_data()
    track_id = data.get("track_id")
    if not track_id:
        await state.clear()
        return

    try:
        new_drop = int(msg.text.strip())
        if new_drop < 1 or new_drop > 99:
            raise ValueError
    except ValueError:
        await msg.answer("❌ Некорректный процент. Введите число от 1 до 99.")
        return

    track = await get_user_track_by_id(session, track_id)
    if track:
        track.target_drop_percent = new_drop
        user = await get_or_create_monitor_user(
            session, msg.from_user.id, msg.from_user.username
        )
        await session.commit()
        await _hide_settings_prompt_keyboard(msg, state)
        await msg.answer(
            f"✅ Уведомление о падении цены на {new_drop}% для <b>{track.title}</b> включено.",
            reply_markup=settings_kb(
                track_id,
                has_sizes=bool(track.last_sizes),
                pro_plan=user.plan == "pro",
                qty_on=track.watch_qty,
            ),
        )

    await state.clear()


@router.callback_query(F.data.regexp(r"wbm:price_reset:(\d+)"))
async def wb_settings_price_reset_cb(cb: CallbackQuery, session: AsyncSession) -> None:
    track_id = int(cb.data.split(":")[2])
    track = await get_user_track_by_id(session, track_id)
    if not track:
        await cb.answer("Трек не найден", show_alert=True)
        return

    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    track.target_price = None
    await session.commit()

    await cb.message.edit_text(
        format_track_text(track) + "\n\n⚙️ Настройки:",
        reply_markup=settings_kb(
            track_id,
            has_sizes=bool(track.last_sizes),
            pro_plan=user.plan == "pro",
            qty_on=track.watch_qty,
        ),
    )
    await cb.answer("Цель цены сброшена")


@router.callback_query(F.data.regexp(r"wbm:drop_reset:(\d+)"))
async def wb_settings_drop_reset_cb(cb: CallbackQuery, session: AsyncSession) -> None:
    track_id = int(cb.data.split(":")[2])
    track = await get_user_track_by_id(session, track_id)
    if not track:
        await cb.answer("Трек не найден", show_alert=True)
        return

    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    track.target_drop_percent = None
    await session.commit()

    await cb.message.edit_text(
        format_track_text(track) + "\n\n⚙️ Настройки:",
        reply_markup=settings_kb(
            track_id,
            has_sizes=bool(track.last_sizes),
            pro_plan=user.plan == "pro",
            qty_on=track.watch_qty,
        ),
    )
    await cb.answer("Порог падения сброшен")


@router.callback_query(F.data.regexp(r"wbm:qty:(\d+)"))
async def wb_settings_qty_cb(cb: CallbackQuery, session: AsyncSession) -> None:
    track_id = int(cb.data.split(":")[2])
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )

    if user.plan != "pro":
        await cb.answer("⭐️ Доступно только на тарифе PRO", show_alert=True)
        return

    track = await get_user_track_by_id(session, track_id)
    if not track:
        await cb.answer("Трек не найден", show_alert=True)
        return

    track.watch_qty = not track.watch_qty
    await session.commit()

    try:
        await cb.message.edit_text(
            format_track_text(track) + "\n\n⚙️ Настройки:",
            reply_markup=settings_kb(
                track_id,
                has_sizes=bool(track.watch_sizes),
                pro_plan=True,
                qty_on=track.watch_qty,
            ),
        )
    except TelegramBadRequest:
        pass

    await cb.answer(f"Остаток: {'ВКЛ' if track.watch_qty else 'ВЫКЛ'}")


@router.callback_query(F.data.regexp(r"wbm:sizes:(\d+)"))
async def wb_settings_sizes_cb(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    track_id = int(cb.data.split(":")[2])
    track = await get_user_track_by_id(session, track_id)

    if not track or not track.last_sizes:
        await cb.answer("У этого товара нет размеров", show_alert=True)
        return

    await state.update_data(track_id=track_id)
    await state.set_state(SettingsState.waiting_for_sizes)

    sizes_str = ", ".join(track.last_sizes)
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отмена", callback_data=f"wbm:settings:{track_id}"
                )
            ]
        ]
    )
    await cb.message.edit_text(
        f"📏 Доступные размеры: {sizes_str}\n\nВведите размеры через запятую, которые хотите отслеживать (или отправьте '0' чтобы очистить фильтр):",
        reply_markup=cancel_kb,
    )


@router.message(SettingsState.waiting_for_sizes, F.text)
async def wb_settings_sizes_msg(
    msg: Message, state: FSMContext, session: AsyncSession
) -> None:
    data = await state.get_data()
    track_id = data.get("track_id")
    if not track_id:
        await state.clear()
        return

    track = await get_user_track_by_id(session, track_id)
    if not track:
        await state.clear()
        return

    text = msg.text.strip()
    if text == "0" or text.lower() == "все":
        track.watch_sizes = track.last_sizes or []
    else:
        sizes = [s.strip() for s in text.split(",")]
        # Filter sizes to only valid ones if we have them
        if track.last_sizes:
            sizes = [s for s in sizes if s in track.last_sizes]
        track.watch_sizes = sizes

    await session.commit()
    await msg.answer(
        f"✅ Размеры для отслеживания обновлены: {', '.join(track.watch_sizes) if track.watch_sizes else 'Нет'}"
    )
    await state.clear()
