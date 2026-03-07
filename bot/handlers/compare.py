"""compare.py — handlers for product comparison (wbm:compare:*)."""
from __future__ import annotations

import logging
import re
from html import escape
from typing import TYPE_CHECKING

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)

from bot.db.redis import (
    FeatureUsageDailyRD,
    WbCompareCacheRD,
    WbCompareScoreRD,
)
from bot import text as tx
from bot.keyboards.inline import add_item_prompt_kb
from bot.services.repository import (
    create_compare_run,
    get_or_create_monitor_user,
    get_price_history_stats,
)
from bot.services.product_compare import compare_products_with_llm
from bot.services.utils import is_admin
from bot.services.wb_client import extract_wb_item_id, fetch_product
from bot.settings import se

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession

from bot.handlers._shared import (
    SettingsState,
    _can_use_compare,
    _COMPARE_DAILY_LIMIT,
)

router = Router()
logger = logging.getLogger(__name__)


def _compare_mode_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=tx.BTN_COMPARE_MODE_CHEAP, callback_data="wbm:compare:mode:cheap")],
            [InlineKeyboardButton(text=tx.BTN_COMPARE_MODE_QUALITY, callback_data="wbm:compare:mode:quality")],
            [InlineKeyboardButton(text=tx.BTN_COMPARE_MODE_GIFT, callback_data="wbm:compare:mode:gift")],
            [InlineKeyboardButton(text=tx.BTN_COMPARE_MODE_SAFE, callback_data="wbm:compare:mode:safe")],
            [InlineKeyboardButton(text=tx.SETTINGS_CANCEL_BTN, callback_data="wbm:cancel:0")],
        ]
    )


@router.callback_query(F.data == "wbm:compare:0")
async def wb_compare_cb(cb: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username,
        cb.from_user.first_name, cb.from_user.last_name,
    )
    admin = is_admin(cb.from_user.id, se)
    if not _can_use_compare(plan=user.plan, admin=admin):
        await cb.answer(tx.COMPARE_ACCESS_DENIED, show_alert=True)
        return
    await state.clear()
    await cb.message.edit_text(tx.COMPARE_MODE_PROMPT, reply_markup=_compare_mode_kb())


@router.callback_query(F.data.regexp(r"wbm:compare:mode:(cheap|quality|gift|safe)"))
async def wb_compare_mode_cb(cb: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username,
        cb.from_user.first_name, cb.from_user.last_name,
    )
    admin = is_admin(cb.from_user.id, se)
    if not _can_use_compare(plan=user.plan, admin=admin):
        await cb.answer(tx.COMPARE_ACCESS_DENIED, show_alert=True)
        return
    mode = cb.data.split(":")[-1]
    await state.update_data(compare_mode=mode)
    await state.set_state(SettingsState.waiting_for_compare_items)
    await cb.message.edit_text(tx.COMPARE_ITEMS_PROMPT, reply_markup=add_item_prompt_kb())


@router.message(SettingsState.waiting_for_compare_items, F.text)
async def wb_compare_from_text(
    msg: Message,
    session: AsyncSession,
    redis: "Redis",
    state: FSMContext,
) -> None:
    raw_parts = [p.strip() for p in re.split(r"[\n,;\t ]+", msg.text or "") if p.strip()]
    wb_ids: list[int] = []
    for part in raw_parts:
        nm_id = extract_wb_item_id(part)
        if not nm_id or nm_id in wb_ids:
            continue
        wb_ids.append(nm_id)

    if len(wb_ids) > 5:
        await msg.answer(tx.COMPARE_ITEMS_TOO_MANY)
        return
    if len(wb_ids) < 2:
        await msg.answer(tx.COMPARE_ITEMS_NOT_ENOUGH)
        return

    user = await get_or_create_monitor_user(
        session, msg.from_user.id, msg.from_user.username,
        msg.from_user.first_name, msg.from_user.last_name, redis=redis,
    )
    admin = is_admin(msg.from_user.id, se)

    if not admin:
        ok, _ = await FeatureUsageDailyRD.try_consume(
            redis, tg_user_id=user.tg_user_id,
            feature="compare", limit=_COMPARE_DAILY_LIMIT, period="day",
        )
        if not ok:
            await msg.answer(tx.COMPARE_LIMIT_REACHED)
            return

    await msg.answer(tx.COMPARE_ITEMS_PROGRESS)

    products = []
    for nm_id in wb_ids:
        product = await fetch_product(redis, nm_id, use_cache=False)
        if product:
            products.append(product)

    if len(products) < 2:
        await msg.answer(tx.COMPARE_ITEMS_NOT_ENOUGH)
        return

    user = await get_or_create_monitor_user(
        session, msg.from_user.id, msg.from_user.username,
        msg.from_user.first_name, msg.from_user.last_name, redis=redis,
    )

    data = await state.get_data()
    compare_mode = str(data.get("compare_mode") or "balanced")
    history = await get_price_history_stats(session, [p.wb_item_id for p in products], days=30)
    wb_ids_list = [p.wb_item_id for p in products]

    # ── Redis cache ──────────────────────────────────────────────────────────
    cached = await WbCompareCacheRD.get(redis, wb_ids_list, compare_mode)
    if cached:
        from bot.services.product_compare import CompareResult, ProductScore
        result = CompareResult(
            winner_id=cached.winner_id, ranking=cached.ranking,
            reason=cached.reason, risks=cached.risks, wait_tip=cached.wait_tip,
            scores=[
                ProductScore(
                    wb_item_id=s.wb_item_id, value=s.value, trust=s.trust,
                    risk=s.risk, availability=s.availability, overall=s.overall,
                    target_price=s.target_price,
                ) for s in cached.scores
            ],
        )
    else:
        try:
            result = await compare_products_with_llm(
                products=products, mode=compare_mode,
                api_key=se.agentplatform_api_key,
                model=se.agentplatform_compare_model,
                api_base_url=se.agentplatform_base_url,
                price_history=history,
            )
        except Exception:
            logger.exception("Compare products failed")
            if not admin:
                try:
                    await FeatureUsageDailyRD.try_consume(
                        redis, tg_user_id=user.tg_user_id,
                        feature="compare", limit=9999, period="day",
                    )
                except Exception:
                    pass
            await msg.answer(tx.COMPARE_ITEMS_FAILED)
            return

        try:
            await WbCompareCacheRD(
                item_ids_key=WbCompareCacheRD._ids_key(wb_ids_list),
                mode=compare_mode, winner_id=result.winner_id,
                ranking=result.ranking, reason=result.reason,
                risks=result.risks or [], wait_tip=result.wait_tip,
                scores=[
                    WbCompareScoreRD(
                        wb_item_id=s.wb_item_id, value=s.value, trust=s.trust,
                        risk=s.risk, availability=s.availability, overall=s.overall,
                        target_price=s.target_price,
                    ) for s in result.scores
                ],
            ).save(redis)
        except Exception:
            logger.warning("Failed to save compare cache", exc_info=True)

    # ── Build output ─────────────────────────────────────────────────────────
    by_id = {p.wb_item_id: p for p in products}
    score_by_id = {s.wb_item_id: s for s in result.scores}
    winner = by_id.get(result.winner_id) or products[0]

    ranking_lines: list[str] = []
    for idx, nm_id in enumerate(result.ranking[:5], start=1):
        p = by_id.get(nm_id)
        s = score_by_id.get(nm_id)
        if not p:
            continue
        price = f"{p.price}₽" if p.price is not None else "—"
        rating = f"{p.rating}" if p.rating is not None else "—"
        extra = f" | оценка {s.overall}/100" if s else ""
        ranking_lines.append(
            f"{idx}. <a href='https://www.wildberries.ru/catalog/{nm_id}/detail.aspx'>"
            f"{escape(p.title)}</a> — {price}, ⭐ {rating}{extra}"
        )

    winner_score = score_by_id.get(winner.wb_item_id)
    winner_price = f"{winner.price}₽" if winner.price is not None else "—"
    winner_rating = f"{winner.rating}" if winner.rating is not None else "—"
    score_block = f"📊 <b>Итоговая оценка:</b> {winner_score.overall}/100\n" if winner_score else ""

    def _replace_ids_with_titles(src: str) -> str:
        out = src
        for p in products:
            out = re.sub(rf"\b{p.wb_item_id}\b", f"«{p.title}»", out)
        return out

    def _humanize_text(src: str) -> str:
        out = src
        for en, ru in {
            "overall": "итоговая оценка", "score": "оценка",
            "target_price": "ориентир по цене", "risk": "риск",
            "trust": "надежность", "availability": "наличие", "value": "ценность",
        }.items():
            out = re.sub(rf"\b{en}\b", ru, out, flags=re.IGNORECASE)
        return out

    clean_reason = _humanize_text(_replace_ids_with_titles(result.reason))
    clean_risks = [_humanize_text(_replace_ids_with_titles(r)) for r in (result.risks or [])]
    clean_wait_tip = _humanize_text(_replace_ids_with_titles(result.wait_tip)) if result.wait_tip else None

    risks_block = ("\n" + "\n".join([f"• {escape(r)}" for r in clean_risks[:3]])) if clean_risks else ""
    wait_tip_block = ""
    if clean_wait_tip and clean_wait_tip.strip().lower() not in {"нет", "no", "none", "n/a", "-", "—"}:
        wait_tip_block = f"\n💡 <b>Рекомендация по цене:</b> {escape(clean_wait_tip)}"

    text = (
        "⚖️ <b>Сравнение товаров</b>\n\n"
        f"🏆 <b>Лучший выбор:</b> <a href='https://www.wildberries.ru/catalog/{winner.wb_item_id}/detail.aspx'>"
        f"{escape(winner.title)}</a>\n"
        f"💰 Цена: <b>{winner_price}</b>\n"
        f"⭐ Рейтинг: <b>{winner_rating}</b>\n"
        f"{score_block}"
        f"📌 Почему: {escape(clean_reason)}\n"
        f"⚠️ <b>Риски:</b>{risks_block}"
        f"{wait_tip_block}\n\n"
        "<b>Рейтинг кандидатов:</b>\n"
        + "\n".join(ranking_lines)
    )

    try:
        await create_compare_run(
            session, user_id=user.id, mode=compare_mode,
            input_item_ids=[p.wb_item_id for p in products],
            winner_item_id=winner.wb_item_id,
            result_json={
                "reason": result.reason, "ranking": result.ranking,
                "risks": result.risks, "wait_tip": result.wait_tip,
                "scores": [
                    {"id": s.wb_item_id, "overall": s.overall, "value": s.value,
                     "trust": s.trust, "risk": s.risk, "availability": s.availability,
                     "target_price": s.target_price}
                    for s in result.scores
                ],
            },
        )
        await session.commit()
    except Exception:
        logger.exception("Failed to save compare run")

    await state.clear()
    await msg.answer(text, link_preview_options=LinkPreviewOptions(is_disabled=True))
