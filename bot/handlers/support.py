"""support.py — support ticket handlers for users and admins."""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from aiogram import Bot, Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)

from bot import text as tx
from bot.keyboards.inline import (
    admin_support_ticket_kb,
    dashboard_kb,
    support_cancel_kb,
    support_kb,
    support_media_confirmation_kb,
)
from bot.services.repository import (
    add_ticket_photo,
    close_ticket,
    count_open_tickets,
    create_support_ticket,
    get_open_tickets,
    get_or_create_monitor_user,
    get_ticket_by_id,
    reply_to_ticket,
)
from bot.services.utils import is_admin
from bot.settings import se

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from bot.handlers._shared import SupportState, _can_use_compare

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "wbm:help:0")
async def wb_help_cb(cb: CallbackQuery, session: "AsyncSession") -> None:
    is_admin_flag = is_admin(cb.from_user.id, se)
    if is_admin_flag:
        open_count = await count_open_tickets(session)
        text = (
            tx.HELP_TEXT_ADMIN.format(open_tickets=open_count)
            if hasattr(tx, "HELP_TEXT_ADMIN")
            else tx.HELP_TEXT
        )
    else:
        text = getattr(tx, "HELP_TEXT", "📨 Нажмите кнопку ниже, чтобы написать в поддержку.")
    await cb.message.edit_text(text, reply_markup=support_kb())


@router.callback_query(F.data == "wbm:support:start")
async def wb_support_start_cb(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SupportState.waiting_for_message_or_media)
    await state.update_data(photos=[], message_text=None)
    await cb.message.edit_text(tx.SUPPORT_PROMPT_WITH_MEDIA, reply_markup=support_cancel_kb())


@router.callback_query(F.data == "wbm:support:cancel")
async def wb_support_cancel_cb(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.message.edit_text(tx.SUPPORT_CANCELLED)
    await asyncio.sleep(1)
    # Return to dashboard via home callback simulation
    from bot.handlers.wb_monitor import wb_home_cb
    await wb_home_cb(cb)


@router.message(SupportState.waiting_for_message_or_media, F.text)
async def wb_support_text_msg(msg: Message, state: FSMContext, session: "AsyncSession") -> None:
    data = await state.get_data()
    photos = data.get("photos", [])
    await state.update_data(message_text=msg.text)
    await state.set_state(SupportState.waiting_for_media_confirmation)
    if photos:
        await msg.answer(tx.SUPPORT_MEDIA_ADDED.format(count=len(photos)),
                         reply_markup=support_media_confirmation_kb())
    else:
        await msg.answer(
            f"📝 <b>Сообщение:</b>\n{msg.text[:500]}{'...' if len(msg.text) > 500 else ''}\n\n"
            f"{tx.SUPPORT_CONFIRM_SEND}",
            reply_markup=support_media_confirmation_kb(),
        )


@router.message(SupportState.waiting_for_message_or_media, F.photo)
async def wb_support_photo_msg(msg: Message, state: FSMContext, session: "AsyncSession") -> None:
    data = await state.get_data()
    photos = data.get("photos", [])
    message_text = data.get("message_text")
    photo = msg.photo[-1]
    photos.append({
        "file_id": photo.file_id, "file_unique_id": photo.file_unique_id,
        "width": photo.width, "height": photo.height, "file_size": photo.file_size,
    })
    await state.update_data(photos=photos)
    await state.set_state(SupportState.waiting_for_media_confirmation)
    text = tx.SUPPORT_MEDIA_ADDED.format(count=len(photos))
    if message_text:
        text = f"📝 <b>Сообщение:</b>\n{message_text[:300]}{'...' if len(message_text) > 300 else ''}\n\n{text}"
    await msg.answer(text, reply_markup=support_media_confirmation_kb())


@router.message(SupportState.waiting_for_message_or_media)
async def wb_support_invalid_msg(msg: Message) -> None:
    await msg.answer(tx.SUPPORT_NO_TEXT_NO_MEDIA)


@router.callback_query(F.data == "wbm:support:add_more")
async def wb_support_add_more_cb(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SupportState.waiting_for_message_or_media)
    await cb.message.edit_text(
        tx.SUPPORT_PROMPT_WITH_MEDIA + "\n\n<i>Отправьте следующее фото:</i>",
        reply_markup=support_cancel_kb(),
    )


@router.callback_query(F.data == "wbm:support:send")
async def wb_support_send_cb(
    cb: CallbackQuery, state: FSMContext, session: "AsyncSession", bot: Bot,
) -> None:
    data = await state.get_data()
    photos = data.get("photos", [])
    message_text = data.get("message_text")
    if not message_text and not photos:
        await cb.answer(tx.SUPPORT_NO_TEXT_NO_MEDIA, show_alert=True)
        return
    user = await get_or_create_monitor_user(session, cb.from_user.id, cb.from_user.username)
    ticket = await create_support_ticket(
        session, user_id=user.id, tg_user_id=cb.from_user.id,
        username=cb.from_user.username, message=message_text or "(без текста, только фото)",
    )
    for photo_data in photos:
        await add_ticket_photo(session, ticket_id=ticket.id, file_id=photo_data["file_id"],
                                file_unique_id=photo_data["file_unique_id"],
                                width=photo_data["width"], height=photo_data["height"],
                                file_size=photo_data["file_size"])
    await state.clear()
    await cb.message.edit_text(tx.SUPPORT_SENT)
    admin = is_admin(cb.from_user.id, se)
    await cb.message.answer(
        tx.SUPPORT_SENT,
        reply_markup=dashboard_kb(admin, show_compare=_can_use_compare(plan=user.plan, admin=admin)),
    )
    # Notify admins
    admin_ids = se.admin_ids_list or {se.developer_id}
    for admin_id in admin_ids:
        try:
            username_display = f"@{ticket.username}" if ticket.username else f"ID:{ticket.tg_user_id}"
            if photos:
                if len(photos) > 1:
                    media_group = [InputMediaPhoto(media=p["file_id"]) for p in photos[:10]]
                    await bot.send_media_group(admin_id, media=media_group)
                else:
                    await bot.send_photo(admin_id, photo=photos[0]["file_id"])
            await bot.send_message(
                admin_id,
                tx.SUPPORT_ADMIN_NOTIFY.format(
                    username=username_display, user_id=ticket.tg_user_id,
                    created_at=ticket.created_at.strftime("%d.%m.%Y %H:%M"),
                    message=ticket.message,
                ),
                reply_markup=admin_support_ticket_kb(ticket.id),
            )
        except Exception as e:
            logger.warning("Failed to notify admin %s: %s", admin_id, e)
    ticket.admin_notified = True
    await session.commit()


@router.callback_query(F.data.regexp(r"wbm:support:admin:reply:(\d+)"))
async def wb_support_admin_reply_cb(cb: CallbackQuery, state: FSMContext, session: "AsyncSession") -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return
    ticket_id = int(cb.data.split(":")[4])
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
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ Отмена", callback_data="wbm:support:admin:cancel")
        ]]),
    )
    await cb.answer()


@router.message(SupportState.waiting_for_admin_reply, F.text)
async def wb_support_admin_reply_msg(
    msg: Message, state: FSMContext, session: "AsyncSession", bot: Bot,
) -> None:
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    reply_to_user_id = data.get("reply_to_user_id")
    if not ticket_id:
        await state.clear()
        return
    ticket = await reply_to_ticket(session, ticket_id=ticket_id, response=msg.text,
                                    responded_by_tg_id=msg.from_user.id)
    await state.clear()
    if ticket:
        try:
            await bot.send_message(reply_to_user_id, tx.SUPPORT_USER_REPLY.format(response=ticket.response))
        except Exception as e:
            logger.warning("Failed to send reply to user %s: %s", reply_to_user_id, e)
        await msg.answer(tx.SUPPORT_ADMIN_REPLY_SENT)
    else:
        await msg.answer("❌ Ошибка: тикет не найден")


@router.callback_query(F.data.regexp(r"wbm:support:admin:close:(\d+)"))
async def wb_support_admin_close_cb(cb: CallbackQuery, session: "AsyncSession") -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return
    ticket_id = int(cb.data.split(":")[4])
    success = await close_ticket(session, ticket_id)
    if success:
        await cb.answer(tx.SUPPORT_TICKET_CLOSED)
        await cb.message.edit_text(f"{cb.message.text}\n\n🔒 Тикет #{ticket_id} закрыт")
    else:
        await cb.answer("❌ Тикет не найден", show_alert=True)


@router.callback_query(F.data == "wbm:support:admin:cancel")
async def wb_support_admin_cancel_cb(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.message.delete()
    await cb.answer("Отменено")
