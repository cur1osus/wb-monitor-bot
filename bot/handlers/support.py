"""support.py — support ticket handlers for users and admins."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from aiogram import Bot, Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InputMediaPhoto, Message

from bot.callbacks import NavAction, NavCb, SupportAction, SupportActionCb, SupportTicketAction, SupportTicketActionCb
from bot import text as tx
from bot.keyboards.inline import (
    admin_support_ticket_kb,
    support_cancel_kb,
    support_admin_reply_cancel_kb,
    support_kb,
    support_media_confirmation_kb,
)
from bot.services.repository import (
    close_ticket,
    count_open_tickets,
    create_support_ticket_with_photos,
    get_or_create_monitor_user,
    get_ticket_by_id,
    reply_to_ticket,
)
from bot.services.utils import is_admin
from bot.settings import se

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from bot.handlers._shared import SupportState
from bot.handlers._dashboard import build_dashboard_view

router = Router()
logger = logging.getLogger(__name__)


async def _notify_admin_about_ticket(
    *,
    bot: Bot,
    admin_id: int,
    ticket_id: int,
    username: str | None,
    tg_user_id: int,
    created_at: str,
    message: str,
    photos: list[dict[str, object]],
) -> None:
    username_display = f"@{username}" if username else f"ID:{tg_user_id}"
    if photos:
        if len(photos) > 1:
            media_group = [
                InputMediaPhoto(media=str(photo["file_id"])) for photo in photos[:10]
            ]
            await bot.send_media_group(admin_id, media=media_group)
        else:
            await bot.send_photo(admin_id, photo=str(photos[0]["file_id"]))

    await bot.send_message(
        admin_id,
        tx.SUPPORT_ADMIN_NOTIFY.format(
            username=username_display,
            user_id=tg_user_id,
            created_at=created_at,
            message=message,
        ),
        reply_markup=admin_support_ticket_kb(ticket_id),
    )


@router.callback_query(NavCb.filter(F.action == NavAction.HELP))
async def wb_help_cb(
    cb: CallbackQuery,
    callback_data: NavCb,
    session: "AsyncSession",
) -> None:
    is_admin_flag = is_admin(cb.from_user.id, se)
    if is_admin_flag:
        open_count = await count_open_tickets(session)
        text = (
            tx.HELP_TEXT_ADMIN.format(open_tickets=open_count)
            if hasattr(tx, "HELP_TEXT_ADMIN")
            else tx.HELP_TEXT
        )
    else:
        text = getattr(
            tx, "HELP_TEXT", "📨 Нажмите кнопку ниже, чтобы написать в поддержку."
        )
    await cb.message.edit_text(text, reply_markup=support_kb())


@router.callback_query(SupportActionCb.filter(F.action == SupportAction.START))
async def wb_support_start_cb(
    cb: CallbackQuery,
    callback_data: SupportActionCb,
    state: FSMContext,
) -> None:
    await state.set_state(SupportState.waiting_for_message_or_media)
    await state.update_data(photos=[], message_text=None)
    await cb.message.edit_text(
        tx.SUPPORT_PROMPT_WITH_MEDIA, reply_markup=support_cancel_kb()
    )


@router.callback_query(SupportActionCb.filter(F.action == SupportAction.CANCEL))
async def wb_support_cancel_cb(
    cb: CallbackQuery,
    callback_data: SupportActionCb,
    state: FSMContext,
    session: "AsyncSession",
) -> None:
    await state.clear()
    _user, text, reply_markup = await build_dashboard_view(
        session=session,
        tg_user_id=cb.from_user.id,
        username=cb.from_user.username,
    )
    await cb.message.edit_text(
        f"{tx.SUPPORT_CANCELLED}\n\n{text}", reply_markup=reply_markup
    )


@router.message(SupportState.waiting_for_message_or_media, F.text)
async def wb_support_text_msg(
    msg: Message, state: FSMContext, session: "AsyncSession"
) -> None:
    data = await state.get_data()
    photos = data.get("photos", [])
    await state.update_data(message_text=msg.text)
    await state.set_state(SupportState.waiting_for_media_confirmation)
    if photos:
        await msg.answer(
            tx.SUPPORT_MEDIA_ADDED.format(count=len(photos)),
            reply_markup=support_media_confirmation_kb(),
        )
    else:
        await msg.answer(
            f"📝 <b>Сообщение:</b>\n{msg.text[:500]}{'...' if len(msg.text) > 500 else ''}\n\n"
            f"{tx.SUPPORT_CONFIRM_SEND}",
            reply_markup=support_media_confirmation_kb(),
        )


@router.message(SupportState.waiting_for_message_or_media, F.photo)
async def wb_support_photo_msg(
    msg: Message, state: FSMContext, session: "AsyncSession"
) -> None:
    data = await state.get_data()
    photos = data.get("photos", [])
    message_text = data.get("message_text")
    photo = msg.photo[-1]
    photos.append(
        {
            "file_id": photo.file_id,
            "file_unique_id": photo.file_unique_id,
            "width": photo.width,
            "height": photo.height,
            "file_size": photo.file_size,
        }
    )
    await state.update_data(photos=photos)
    await state.set_state(SupportState.waiting_for_media_confirmation)
    text = tx.SUPPORT_MEDIA_ADDED.format(count=len(photos))
    if message_text:
        text = f"📝 <b>Сообщение:</b>\n{message_text[:300]}{'...' if len(message_text) > 300 else ''}\n\n{text}"
    await msg.answer(text, reply_markup=support_media_confirmation_kb())


@router.message(SupportState.waiting_for_message_or_media)
async def wb_support_invalid_msg(msg: Message) -> None:
    await msg.answer(tx.SUPPORT_NO_TEXT_NO_MEDIA)


@router.callback_query(SupportActionCb.filter(F.action == SupportAction.ADD_MORE))
async def wb_support_add_more_cb(
    cb: CallbackQuery,
    callback_data: SupportActionCb,
    state: FSMContext,
) -> None:
    await state.set_state(SupportState.waiting_for_message_or_media)
    await cb.message.edit_text(
        tx.SUPPORT_PROMPT_WITH_MEDIA + "\n\n<i>Отправьте следующее фото:</i>",
        reply_markup=support_cancel_kb(),
    )


@router.callback_query(SupportActionCb.filter(F.action == SupportAction.SEND))
async def wb_support_send_cb(
    cb: CallbackQuery,
    callback_data: SupportActionCb,
    state: FSMContext,
    session: "AsyncSession",
    bot: Bot,
) -> None:
    data = await state.get_data()
    photos = data.get("photos", [])
    message_text = data.get("message_text")
    if not message_text and not photos:
        await cb.answer(tx.SUPPORT_NO_TEXT_NO_MEDIA, show_alert=True)
        return
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    ticket = await create_support_ticket_with_photos(
        session,
        user_id=user.id,
        tg_user_id=cb.from_user.id,
        username=cb.from_user.username,
        message=message_text or "(без текста, только фото)",
        photos=photos,
    )
    await state.clear()
    _user, dashboard_text, reply_markup = await build_dashboard_view(
        session=session,
        tg_user_id=cb.from_user.id,
        username=cb.from_user.username,
    )
    await cb.message.answer(
        f"{tx.SUPPORT_SENT}\n\n{dashboard_text}",
        reply_markup=reply_markup,
    )
    await cb.message.edit_text(tx.SUPPORT_SENT)

    # Notify admins
    admin_ids = se.admin_ids_list or {se.developer_id}
    notification_tasks = [
        _notify_admin_about_ticket(
            bot=bot,
            admin_id=admin_id,
            ticket_id=ticket.id,
            username=ticket.username,
            tg_user_id=ticket.tg_user_id,
            created_at=ticket.created_at.strftime("%d.%m.%Y %H:%M"),
            message=ticket.message,
            photos=photos,
        )
        for admin_id in admin_ids
    ]
    results = await asyncio.gather(*notification_tasks, return_exceptions=True)
    for admin_id, result in zip(admin_ids, results, strict=False):
        if isinstance(result, Exception):
            logger.warning("Failed to notify admin %s: %s", admin_id, result)

    ticket.admin_notified = any(not isinstance(result, Exception) for result in results)
    await session.commit()


@router.callback_query(SupportTicketActionCb.filter(F.action == SupportTicketAction.REPLY))
async def wb_support_admin_reply_cb(
    cb: CallbackQuery,
    callback_data: SupportTicketActionCb,
    state: FSMContext,
    session: "AsyncSession",
) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return
    ticket_id = callback_data.ticket_id
    ticket = await get_ticket_by_id(session, ticket_id)
    if not ticket:
        await cb.answer("❌ Тикет не найден", show_alert=True)
        return
    if ticket.status == "closed":
        await cb.answer("❌ Тикет уже закрыт", show_alert=True)
        return
    await state.update_data(ticket_id=ticket_id, reply_to_user_id=ticket.tg_user_id)
    await state.set_state(SupportState.waiting_for_admin_reply)
    await cb.message.answer(
        f"✍️ Ответ на тикет #{ticket_id}\n\n"
        f"👤 Пользователь: @{ticket.username or ticket.tg_user_id}\n"
        f"📝 Сообщение: {ticket.message[:200]}...\n\nНапишите ваш ответ:",
        reply_markup=support_admin_reply_cancel_kb(),
    )
    await cb.answer()


@router.message(SupportState.waiting_for_admin_reply, F.text)
async def wb_support_admin_reply_msg(
    msg: Message,
    state: FSMContext,
    session: "AsyncSession",
    bot: Bot,
) -> None:
    if not msg.from_user or not is_admin(msg.from_user.id, se):
        await state.clear()
        await msg.answer(tx.NO_ACCESS)
        return
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    reply_to_user_id = data.get("reply_to_user_id")
    if not ticket_id:
        await state.clear()
        return
    ticket = await reply_to_ticket(
        session,
        ticket_id=ticket_id,
        response=msg.text,
        responded_by_tg_id=msg.from_user.id,
    )
    await state.clear()
    if ticket:
        try:
            await bot.send_message(
                reply_to_user_id, tx.SUPPORT_USER_REPLY.format(response=ticket.response)
            )
        except Exception as e:
            logger.warning("Failed to send reply to user %s: %s", reply_to_user_id, e)
        await msg.answer(tx.SUPPORT_ADMIN_REPLY_SENT)
    else:
        await msg.answer("❌ Ошибка: тикет не найден")


@router.callback_query(SupportTicketActionCb.filter(F.action == SupportTicketAction.CLOSE))
async def wb_support_admin_close_cb(
    cb: CallbackQuery,
    callback_data: SupportTicketActionCb,
    session: "AsyncSession",
) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return
    ticket_id = callback_data.ticket_id
    success = await close_ticket(session, ticket_id)
    if success:
        await cb.answer(tx.SUPPORT_TICKET_CLOSED)
        await cb.message.edit_text(f"{cb.message.text}\n\n🔒 Тикет #{ticket_id} закрыт")
    else:
        await cb.answer("❌ Тикет не найден", show_alert=True)


@router.callback_query(SupportActionCb.filter(F.action == SupportAction.ADMIN_CANCEL))
async def wb_support_admin_cancel_cb(
    cb: CallbackQuery,
    callback_data: SupportActionCb,
    state: FSMContext,
) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return
    await state.clear()
    await cb.message.delete()
    await cb.answer("Отменено")
