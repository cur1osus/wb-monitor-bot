from __future__ import annotations

import logging
import re
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
from bot.db.redis import (
    FeatureUsageDailyRD,
    MonitorUserRD,
    WbReviewInsightsCacheRD,
    WbSimilarItemRD,
    WbSimilarSearchCacheRD,
)
from bot import text as tx
from bot.keyboards.inline import (
    add_item_prompt_kb,
    back_to_dashboard_kb,
    dashboard_kb,
    dashboard_text,
    format_track_text,
    admin_grant_pro_kb,
    admin_config_input_kb,
    admin_config_kb,
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
    get_runtime_config,
    runtime_config_view,
    apply_runtime_intervals,
    set_user_tracks_interval,
)
from bot.services.review_analysis import (
    ReviewAnalysisConfigError,
    ReviewAnalysisError,
    ReviewInsights,
    ReviewAnalysisRateLimitError,
    analyze_reviews_with_llm,
)
from bot.services.utils import is_admin
from bot.services.wb_client import (
    extract_wb_item_id,
    fetch_product,
    search_similar_cheaper,
)
from bot.settings import se

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession

    from bot.services.repository import AdminStats, RuntimeConfigView

router = Router()
logger = logging.getLogger(__name__)
_LIKELY_WB_INPUT_RE = re.compile(r"wildberries|wb\.ru|\d{6,15}", re.IGNORECASE)


def _model_signature(model: str, review_limit: int) -> str:
    return f"{model}|limit:{review_limit}"


def _daily_feature_limit(plan: str, cfg: "RuntimeConfigView") -> int:
    return cfg.pro_daily_ai_limit if plan == "pro" else cfg.free_daily_ai_limit


def _format_review_insights_text(track_title: str, insights: ReviewInsights) -> str:
    return tx.review_insights_text(track_title, insights)


async def _track_kb_with_usage(
    *,
    session: AsyncSession,
    redis: "Redis",
    user_tg_id: int,
    user_plan: str,
    track: TrackModel,
    page: int,
    total: int,
    confirm_remove: bool = False,
) -> InlineKeyboardMarkup:
    cfg = runtime_config_view(await get_runtime_config(session))
    limit = _daily_feature_limit(user_plan, cfg)
    cheap_used = await FeatureUsageDailyRD.get_used(
        redis,
        tg_user_id=user_tg_id,
        feature="cheap",
    )
    reviews_used = await FeatureUsageDailyRD.get_used(
        redis,
        tg_user_id=user_tg_id,
        feature="reviews",
    )

    return paged_track_kb(
        track,
        page,
        total,
        confirm_remove=confirm_remove,
        cheap_btn_text=tx.button_with_usage(
            tx.BTN_FIND_CHEAPER,
            used=cheap_used,
            limit=limit,
        ),
        reviews_btn_text=tx.button_with_usage(
            tx.BTN_REVIEW_ANALYSIS,
            used=reviews_used,
            limit=limit,
        ),
    )


class SettingsState(StatesGroup):
    waiting_for_price = State()
    waiting_for_drop = State()
    waiting_for_sizes = State()
    waiting_for_pro_grant = State()
    waiting_for_free_interval = State()
    waiting_for_pro_interval = State()
    waiting_for_cheap_threshold = State()
    waiting_for_free_ai_limit = State()
    waiting_for_pro_ai_limit = State()
    waiting_for_review_sample_limit = State()
    waiting_for_analysis_model = State()


@router.callback_query(F.data == "wbm:home:0")
async def wb_home_cb(cb: CallbackQuery, session: AsyncSession, redis: "Redis") -> None:
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    used = await count_user_tracks(session, user.id, active_only=True)
    cfg = runtime_config_view(await get_runtime_config(session))
    await cb.message.edit_text(
        dashboard_text(
            user.plan,
            used,
            free_interval_min=cfg.free_interval_min,
            pro_interval_min=cfg.pro_interval_min,
        ),
        reply_markup=dashboard_kb(is_admin(cb.from_user.id, se)),
    )


@router.callback_query(F.data == "wbm:noop:0")
async def wb_noop_cb(cb: CallbackQuery) -> None:
    await cb.answer()


@router.callback_query(F.data == "wbm:add:0")
async def wb_add_cb(cb: CallbackQuery) -> None:
    await cb.message.edit_text(
        tx.ADD_ITEM_PROMPT,
        reply_markup=add_item_prompt_kb(),
    )


@router.message(
    StateFilter(None),
    F.text,
)
async def wb_add_item_from_text(
    msg: Message,
    session: AsyncSession,
    redis: "Redis",
) -> None:
    url_or_text = msg.text.strip()
    if not _LIKELY_WB_INPUT_RE.search(url_or_text):
        return

    wb_item_id = extract_wb_item_id(url_or_text)

    if not wb_item_id:
        await msg.answer(tx.WB_LINK_PARSE_ERROR)
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
        await msg.answer(tx.TRACK_ALREADY_EXISTS)
        return

    track_count = await count_user_tracks(session, user.id, active_only=True)
    limit = 50 if user.plan == "pro" else 5
    if track_count >= limit:
        await msg.answer(tx.TRACK_LIMIT_REACHED.format(limit=limit))
        return

    product = await fetch_product(redis, wb_item_id)
    if not product:
        await msg.answer(tx.PRODUCT_FETCH_ERROR)
        return

    cfg = runtime_config_view(await get_runtime_config(session))
    interval = cfg.pro_interval_min if user.plan == "pro" else cfg.free_interval_min
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
        product.rating,
        product.reviews,
        interval,
    )
    await session.commit()

    price_text = f"{product.price}₽" if product.price else tx.TRACK_ADDED_PRICE_UNKNOWN
    rating_text = (
        tx.TRACK_ADDED_RATING_WITH_REVIEWS.format(
            rating=product.rating,
            reviews=product.reviews or 0,
        )
        if product.rating is not None
        else tx.TRACK_ADDED_RATING_UNKNOWN
    )
    in_stock_text = (
        tx.TRACK_ADDED_IN_STOCK_YES if product.in_stock else tx.TRACK_ADDED_IN_STOCK_NO
    )

    await msg.answer(
        tx.TRACK_ADDED_TEMPLATE.format(
            title=product.title,
            price=price_text,
            rating=rating_text,
            in_stock=in_stock_text,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=tx.TRACK_ADDED_FIND_CHEAPER_BTN,
                        callback_data=f"wbm:cheap:{track.id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=tx.TRACK_ADDED_MY_TRACKS_BTN,
                        callback_data="wbm:list:0",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=tx.TRACK_ADDED_BACK_MENU_BTN,
                        callback_data="wbm:home:0",
                    )
                ],
            ]
        ),
    )


@router.callback_query(F.data == "wbm:list:0")
async def wb_list_cb(cb: CallbackQuery, session: AsyncSession, redis: "Redis") -> None:
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    tracks = await get_user_tracks(session, user.id)
    if not tracks:
        await cb.answer(tx.NO_ACTIVE_TRACKS, show_alert=True)
        return
    track = tracks[0]
    await cb.message.edit_text(
        format_track_text(track),
        reply_markup=await _track_kb_with_usage(
            session=session,
            redis=redis,
            user_tg_id=cb.from_user.id,
            user_plan=user.plan,
            track=track,
            page=0,
            total=len(tracks),
        ),
    )


@router.callback_query(F.data.regexp(r"wbm:page:(\d+)"))
async def wb_page_cb(cb: CallbackQuery, session: AsyncSession, redis: "Redis") -> None:
    page = int(cb.data.split(":")[2])
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    tracks = await get_user_tracks(session, user.id)
    if not tracks or page >= len(tracks):
        await cb.answer(tx.INVALID_PAGE, show_alert=True)
        return
    track = tracks[page]
    await cb.message.edit_text(
        format_track_text(track),
        reply_markup=await _track_kb_with_usage(
            session=session,
            redis=redis,
            user_tg_id=cb.from_user.id,
            user_plan=user.plan,
            track=track,
            page=page,
            total=len(tracks),
        ),
    )


@router.callback_query(F.data.regexp(r"wbm:pause:(\d+)"))
async def wb_pause_cb(cb: CallbackQuery, session: AsyncSession, redis: "Redis") -> None:
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
                reply_markup=await _track_kb_with_usage(
                    session=session,
                    redis=redis,
                    user_tg_id=cb.from_user.id,
                    user_plan=user.plan,
                    track=track,
                    page=idx,
                    total=len(tracks),
                ),
            )
            break


@router.callback_query(F.data.regexp(r"wbm:resume:(\d+)"))
async def wb_resume_cb(
    cb: CallbackQuery, session: AsyncSession, redis: "Redis"
) -> None:
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
                reply_markup=await _track_kb_with_usage(
                    session=session,
                    redis=redis,
                    user_tg_id=cb.from_user.id,
                    user_plan=user.plan,
                    track=track,
                    page=idx,
                    total=len(tracks),
                ),
            )
            break


@router.callback_query(F.data.regexp(r"wbm:remove:(\d+)"))
async def wb_remove_cb(
    cb: CallbackQuery, session: AsyncSession, redis: "Redis"
) -> None:
    track_id = int(cb.data.split(":")[2])
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    tracks = await get_user_tracks(session, user.id)
    for idx, track in enumerate(tracks):
        if track.id == track_id:
            await cb.message.edit_text(
                format_track_text(track),
                reply_markup=await _track_kb_with_usage(
                    session=session,
                    redis=redis,
                    user_tg_id=cb.from_user.id,
                    user_plan=user.plan,
                    track=track,
                    page=idx,
                    total=len(tracks),
                    confirm_remove=True,
                ),
            )
            await cb.answer(tx.REMOVE_CONFIRM)
            return
    await cb.answer(tx.TRACK_NOT_FOUND, show_alert=True)


@router.callback_query(F.data.regexp(r"wbm:remove_no:(\d+)"))
async def wb_remove_no_cb(
    cb: CallbackQuery,
    session: AsyncSession,
    redis: "Redis",
) -> None:
    track_id = int(cb.data.split(":")[2])
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    tracks = await get_user_tracks(session, user.id)
    for idx, track in enumerate(tracks):
        if track.id == track_id:
            await cb.message.edit_text(
                format_track_text(track),
                reply_markup=await _track_kb_with_usage(
                    session=session,
                    redis=redis,
                    user_tg_id=cb.from_user.id,
                    user_plan=user.plan,
                    track=track,
                    page=idx,
                    total=len(tracks),
                ),
            )
            await cb.answer(tx.REMOVE_CANCELLED)
            return
    await cb.answer(tx.TRACK_NOT_FOUND, show_alert=True)


@router.callback_query(F.data.regexp(r"wbm:remove_yes:(\d+)"))
async def wb_remove_yes_cb(cb: CallbackQuery, session: AsyncSession) -> None:
    track_id = int(cb.data.split(":")[2])
    await delete_track(session, track_id)
    await session.commit()
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    cfg = runtime_config_view(await get_runtime_config(session))
    used = await count_user_tracks(session, user.id, active_only=True)
    await cb.message.edit_text(
        dashboard_text(
            user.plan,
            used,
            free_interval_min=cfg.free_interval_min,
            pro_interval_min=cfg.pro_interval_min,
        ),
        reply_markup=dashboard_kb(is_admin(cb.from_user.id, se)),
    )
    await cb.answer(tx.TRACK_DELETED)


@router.callback_query(F.data.regexp(r"wbm:cheap:(\d+)"))
async def wb_find_cheaper_cb(
    cb: CallbackQuery, session: AsyncSession, redis: "Redis"
) -> None:
    track_id = int(cb.data.split(":")[2])
    track = await get_user_track_by_id(session, track_id)
    if not track:
        await cb.answer(tx.TRACK_NOT_FOUND, show_alert=True)
        return

    back_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=tx.FIND_CHEAPER_TO_LIST_BTN,
                    callback_data="wbm:list:0",
                )
            ]
        ]
    )

    cfg = runtime_config_view(await get_runtime_config(session))
    cached = await WbSimilarSearchCacheRD.get(redis, track.id)
    if cached is None or cached.match_percent != cfg.cheap_match_percent:
        user = await get_or_create_monitor_user(
            session,
            cb.from_user.id,
            cb.from_user.username,
        )
        daily_limit = _daily_feature_limit(user.plan, cfg)
        allowed, _used = await FeatureUsageDailyRD.try_consume(
            redis,
            tg_user_id=cb.from_user.id,
            feature="cheap",
            daily_limit=daily_limit,
        )
        if not allowed:
            await cb.answer(
                tx.FEATURE_LIMIT_CHEAP_REACHED.format(limit=daily_limit),
                show_alert=True,
            )
            return

        await cb.answer(tx.FIND_CHEAPER_ANSWER)
        await cb.message.edit_text(
            tx.FIND_CHEAPER_PROGRESS.format(title=escape(track.title)),
            reply_markup=back_kb,
        )

        current = await fetch_product(redis, track.wb_item_id, use_cache=False)
        if not current or current.price is None:
            await cb.message.edit_text(
                tx.FIND_CHEAPER_PRICE_ERROR,
                reply_markup=back_kb,
            )
            return

        found = await search_similar_cheaper(
            base_title=current.title or track.title,
            base_entity=current.entity,
            base_brand=current.brand,
            base_subject_id=current.subject_id,
            match_percent_threshold=cfg.cheap_match_percent,
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
            match_percent=cfg.cheap_match_percent,
            items=alternatives,
        ).save(redis)
    else:
        await cb.answer()
        alternatives = cached.items
        current_price_text = cached.base_price

    if not alternatives:
        await cb.message.edit_text(
            tx.FIND_CHEAPER_EMPTY.format(
                title=escape(track.title),
                price=current_price_text,
            ),
            reply_markup=back_kb,
        )
        return

    lines = [
        tx.FIND_CHEAPER_HEADER.format(
            price=current_price_text,
            title=escape(track.title),
        ),
        "",
    ]
    for idx, item in enumerate(alternatives, start=1):
        lines.append(
            f'{idx}. <a href="{item.url}">{escape(item.title)}</a> — <b>{item.price} ₽</b>'
        )
    lines.append("")
    lines.append(tx.FIND_CHEAPER_TIP)

    await cb.message.edit_text(
        "\n".join(lines),
        reply_markup=back_kb,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


@router.callback_query(F.data.regexp(r"wbm:reviews:(\d+)"))
async def wb_reviews_analysis_cb(
    cb: CallbackQuery,
    session: AsyncSession,
    redis: "Redis",
) -> None:
    track_id = int(cb.data.split(":")[2])
    track = await get_user_track_by_id(session, track_id)
    if not track:
        await cb.answer(tx.TRACK_NOT_FOUND, show_alert=True)
        return

    back_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=tx.REVIEWS_BACK_TO_TRACK_BTN,
                    callback_data=f"wbm:back:{track.id}",
                )
            ]
        ]
    )

    product = await fetch_product(redis, track.wb_item_id)
    reviews_count: int | None = None
    if product is not None and product.reviews is not None:
        reviews_count = int(product.reviews)
    elif track.last_reviews is not None:
        reviews_count = int(track.last_reviews)

    if reviews_count is not None and reviews_count <= 0:
        await cb.answer()
        await cb.message.edit_text(
            tx.REVIEWS_ANALYSIS_NO_REVIEWS,
            reply_markup=back_kb,
        )
        return

    cfg = runtime_config_view(await get_runtime_config(session))
    primary_model = (cfg.analysis_model or "").strip() or se.agentplatform_model.strip()
    review_limit = max(10, min(int(cfg.review_sample_limit_per_side), 200))
    model_signature = _model_signature(primary_model, review_limit)

    cached = await WbReviewInsightsCacheRD.get(
        redis,
        track.wb_item_id,
        model_signature,
    )

    try:
        if cached is not None:
            await cb.answer()
            insights = ReviewInsights(
                strengths=list(cached.strengths),
                weaknesses=list(cached.weaknesses),
                positive_samples=cached.positive_samples,
                negative_samples=cached.negative_samples,
                positive_total=cached.positive_total,
                negative_total=cached.negative_total,
                sample_limit_per_side=cached.sample_limit_per_side,
            )
        else:
            user = await get_or_create_monitor_user(
                session,
                cb.from_user.id,
                cb.from_user.username,
            )
            daily_limit = _daily_feature_limit(user.plan, cfg)
            allowed, _used = await FeatureUsageDailyRD.try_consume(
                redis,
                tg_user_id=cb.from_user.id,
                feature="reviews",
                daily_limit=daily_limit,
            )
            if not allowed:
                await cb.answer(
                    tx.FEATURE_LIMIT_REVIEWS_REACHED.format(limit=daily_limit),
                    show_alert=True,
                )
                return

            await cb.answer(tx.REVIEWS_ANALYSIS_ANSWER)
            await cb.message.edit_text(
                tx.REVIEWS_ANALYSIS_PROGRESS.format(title=escape(track.title)),
                reply_markup=back_kb,
            )

            insights = await analyze_reviews_with_llm(
                wb_item_id=track.wb_item_id,
                product_title=track.title,
                api_key=se.agentplatform_api_key,
                model=primary_model,
                api_base_url=se.agentplatform_base_url,
                sample_limit_per_side=review_limit,
            )
            await WbReviewInsightsCacheRD(
                wb_item_id=track.wb_item_id,
                model_signature=model_signature,
                strengths=list(insights.strengths),
                weaknesses=list(insights.weaknesses),
                positive_samples=insights.positive_samples,
                negative_samples=insights.negative_samples,
                positive_total=insights.positive_total,
                negative_total=insights.negative_total,
                sample_limit_per_side=insights.sample_limit_per_side,
            ).save(redis)
    except ReviewAnalysisConfigError as exc:
        await cb.message.edit_text(f"❌ {escape(str(exc))}", reply_markup=back_kb)
        return
    except ReviewAnalysisRateLimitError as exc:
        await cb.message.edit_text(f"⏳ {escape(str(exc))}", reply_markup=back_kb)
        return
    except ReviewAnalysisError as exc:
        await cb.message.edit_text(f"❌ {escape(str(exc))}", reply_markup=back_kb)
        return
    except Exception:
        logger.exception("Unexpected error during reviews analysis")
        await cb.message.edit_text(
            tx.REVIEWS_ANALYSIS_FAILED,
            reply_markup=back_kb,
        )
        return

    await cb.message.edit_text(
        _format_review_insights_text(track.title, insights),
        reply_markup=back_kb,
    )


@router.callback_query(F.data.regexp(r"wbm:settings:(\d+)"))
async def wb_settings_cb(cb: CallbackQuery, session: AsyncSession) -> None:
    track_id = int(cb.data.split(":")[2])
    track = await get_user_track_by_id(session, track_id)
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    if not track:
        await cb.answer(tx.TRACK_NOT_FOUND, show_alert=True)
        return
    await cb.message.edit_text(
        format_track_text(track) + tx.SETTINGS_SUFFIX,
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
    cfg = runtime_config_view(await get_runtime_config(session))
    used = await count_user_tracks(session, user.id, active_only=True)
    limit = 50 if user.plan == "pro" else 5
    interval = cfg.pro_interval_min if user.plan == "pro" else cfg.free_interval_min

    is_pro = user.plan == "pro"
    expires_str = (
        user.pro_expires_at.strftime("%d.%m.%Y")
        if (is_pro and user.pro_expires_at)
        else None
    )
    text = tx.PLAN_TEXT.format(
        plan=user.plan.upper(),
        used=used,
        limit=limit,
        interval=interval,
    )
    if is_pro:
        text += tx.PLAN_PRO_ACTIVE
    else:
        text += tx.PLAN_PRO_UPSELL.format(interval=cfg.pro_interval_min)

    await cb.message.edit_text(text, reply_markup=plan_kb(is_pro, expires_str))


@router.callback_query(F.data == "wbm:pay:stars")
async def wb_pay_stars_cb(cb: CallbackQuery, session: AsyncSession) -> None:
    from aiogram.types import LabeledPrice

    await get_or_create_monitor_user(session, cb.from_user.id, cb.from_user.username)
    await cb.message.answer_invoice(
        title=tx.PAYMENT_TITLE,
        description=tx.PAYMENT_DESCRIPTION,
        payload="wbm_pro_30d",
        currency="XTR",
        prices=[LabeledPrice(label=tx.PAYMENT_LABEL, amount=150)],
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
    cfg = runtime_config_view(await get_runtime_config(session))
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
    await set_user_tracks_interval(session, user.id, cfg.pro_interval_min)

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
                await set_user_tracks_interval(
                    session, referrer.id, cfg.pro_interval_min
                )
                referral_bonus_applied = True
                # Инвалидируем кэш реферера
                await MonitorUserRD.invalidate(redis, referrer.tg_user_id)
                try:
                    await msg.bot.send_message(
                        referrer.tg_user_id,
                        tx.REFERRAL_REWARD_NOTIFY,
                    )
                except Exception:
                    pass

    await session.commit()

    # Инвалидируем кэш текущего пользователя (план изменился)
    await MonitorUserRD.invalidate(redis, msg.from_user.id)

    text = tx.PRO_ACTIVATED
    if referral_bonus_applied:
        text += tx.PRO_ACTIVATED_WITH_REFERRAL
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
        tx.REFERRAL_TEXT.format(ref_link=ref_link),
        reply_markup=ref_kb(ref_link),
    )


@router.callback_query(F.data == "wbm:help:0")
async def wb_help_cb(cb: CallbackQuery) -> None:
    await cb.message.edit_text(
        tx.HELP_TEXT,
        reply_markup=back_to_dashboard_kb(is_admin(cb.from_user.id, se)),
    )


@router.callback_query(F.data == "wbm:admin:0")
async def wb_admin_cb(
    cb: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return

    await state.clear()

    stats = await get_admin_stats(session, days=7)
    await cb.message.edit_text(
        _admin_stats_text(stats),
        reply_markup=admin_panel_kb(selected_days=7),
    )


def _admin_stats_text(stats: "AdminStats") -> str:
    return tx.admin_stats_text(stats)


def _admin_runtime_config_text(cfg: "RuntimeConfigView") -> str:
    return tx.admin_runtime_config_text(cfg)


@router.callback_query(F.data == "wbm:admin:cfg")
async def wb_admin_cfg_cb(
    cb: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return

    await state.clear()
    cfg = runtime_config_view(await get_runtime_config(session))
    await cb.message.edit_text(
        _admin_runtime_config_text(cfg),
        reply_markup=admin_config_kb(),
    )


@router.callback_query(F.data == "wbm:admin:cfg:free")
async def wb_admin_cfg_free_cb(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return

    await state.set_state(SettingsState.waiting_for_free_interval)
    await cb.message.edit_text(
        tx.ADMIN_FREE_PROMPT,
        reply_markup=admin_config_input_kb(),
    )


@router.callback_query(F.data == "wbm:admin:cfg:pro")
async def wb_admin_cfg_pro_cb(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return

    await state.set_state(SettingsState.waiting_for_pro_interval)
    await cb.message.edit_text(
        tx.ADMIN_PRO_PROMPT,
        reply_markup=admin_config_input_kb(),
    )


@router.callback_query(F.data == "wbm:admin:cfg:cheap")
async def wb_admin_cfg_cheap_cb(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return

    await state.set_state(SettingsState.waiting_for_cheap_threshold)
    await cb.message.edit_text(
        tx.ADMIN_CHEAP_PROMPT,
        reply_markup=admin_config_input_kb(),
    )


@router.callback_query(F.data == "wbm:admin:cfg:ai_free")
async def wb_admin_cfg_ai_free_cb(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return

    await state.set_state(SettingsState.waiting_for_free_ai_limit)
    await cb.message.edit_text(
        tx.ADMIN_FREE_AI_LIMIT_PROMPT,
        reply_markup=admin_config_input_kb(),
    )


@router.callback_query(F.data == "wbm:admin:cfg:ai_pro")
async def wb_admin_cfg_ai_pro_cb(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return

    await state.set_state(SettingsState.waiting_for_pro_ai_limit)
    await cb.message.edit_text(
        tx.ADMIN_PRO_AI_LIMIT_PROMPT,
        reply_markup=admin_config_input_kb(),
    )


@router.callback_query(F.data == "wbm:admin:cfg:reviews_limit")
async def wb_admin_cfg_review_limit_cb(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return

    await state.set_state(SettingsState.waiting_for_review_sample_limit)
    await cb.message.edit_text(
        tx.ADMIN_REVIEW_SAMPLE_LIMIT_PROMPT,
        reply_markup=admin_config_input_kb(),
    )


@router.callback_query(F.data == "wbm:admin:cfg:analysis_model")
async def wb_admin_cfg_analysis_model_cb(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return

    await state.set_state(SettingsState.waiting_for_analysis_model)
    await cb.message.edit_text(
        tx.ADMIN_ANALYSIS_MODEL_PROMPT,
        reply_markup=admin_config_input_kb(),
    )


@router.message(SettingsState.waiting_for_free_interval, F.text)
async def wb_admin_cfg_free_msg(
    msg: Message, state: FSMContext, session: AsyncSession
) -> None:
    if not msg.from_user or not is_admin(msg.from_user.id, se):
        await state.clear()
        return

    try:
        value = int(msg.text.strip())
    except ValueError:
        await msg.answer(tx.ADMIN_FREE_INT_ERROR)
        return
    if value < 5 or value > 1440:
        await msg.answer(tx.ADMIN_FREE_RANGE_ERROR)
        return

    cfg = await get_runtime_config(session)
    cfg.free_interval_min = value
    cfg.updated_at = datetime.now(UTC).replace(tzinfo=None)
    await apply_runtime_intervals(
        session,
        free_interval_min=cfg.free_interval_min,
        pro_interval_min=cfg.pro_interval_min,
    )
    await session.commit()
    await state.clear()

    await msg.answer(
        _admin_runtime_config_text(runtime_config_view(cfg)),
        reply_markup=admin_config_kb(),
    )


@router.message(SettingsState.waiting_for_pro_interval, F.text)
async def wb_admin_cfg_pro_msg(
    msg: Message, state: FSMContext, session: AsyncSession
) -> None:
    if not msg.from_user or not is_admin(msg.from_user.id, se):
        await state.clear()
        return

    try:
        value = int(msg.text.strip())
    except ValueError:
        await msg.answer(tx.ADMIN_PRO_INT_ERROR)
        return
    if value < 1 or value > 1440:
        await msg.answer(tx.ADMIN_PRO_RANGE_ERROR)
        return

    cfg = await get_runtime_config(session)
    cfg.pro_interval_min = value
    cfg.updated_at = datetime.now(UTC).replace(tzinfo=None)
    await apply_runtime_intervals(
        session,
        free_interval_min=cfg.free_interval_min,
        pro_interval_min=cfg.pro_interval_min,
    )
    await session.commit()
    await state.clear()

    await msg.answer(
        _admin_runtime_config_text(runtime_config_view(cfg)),
        reply_markup=admin_config_kb(),
    )


@router.message(SettingsState.waiting_for_cheap_threshold, F.text)
async def wb_admin_cfg_cheap_msg(
    msg: Message, state: FSMContext, session: AsyncSession
) -> None:
    if not msg.from_user or not is_admin(msg.from_user.id, se):
        await state.clear()
        return

    try:
        value = int(msg.text.strip())
    except ValueError:
        await msg.answer(tx.ADMIN_CHEAP_INT_ERROR)
        return
    if value < 10 or value > 95:
        await msg.answer(tx.ADMIN_CHEAP_RANGE_ERROR)
        return

    cfg = await get_runtime_config(session)
    cfg.cheap_match_percent = value
    cfg.updated_at = datetime.now(UTC).replace(tzinfo=None)
    await session.commit()
    await state.clear()

    await msg.answer(
        _admin_runtime_config_text(runtime_config_view(cfg)),
        reply_markup=admin_config_kb(),
    )


@router.message(SettingsState.waiting_for_free_ai_limit, F.text)
async def wb_admin_cfg_free_ai_limit_msg(
    msg: Message, state: FSMContext, session: AsyncSession
) -> None:
    if not msg.from_user or not is_admin(msg.from_user.id, se):
        await state.clear()
        return

    try:
        value = int(msg.text.strip())
    except ValueError:
        await msg.answer(tx.ADMIN_FREE_AI_INT_ERROR)
        return
    if value < 1 or value > 50:
        await msg.answer(tx.ADMIN_FREE_AI_RANGE_ERROR)
        return

    cfg = await get_runtime_config(session)
    cfg.free_daily_ai_limit = value
    cfg.updated_at = datetime.now(UTC).replace(tzinfo=None)
    await session.commit()
    await state.clear()

    await msg.answer(
        _admin_runtime_config_text(runtime_config_view(cfg)),
        reply_markup=admin_config_kb(),
    )


@router.message(SettingsState.waiting_for_pro_ai_limit, F.text)
async def wb_admin_cfg_pro_ai_limit_msg(
    msg: Message, state: FSMContext, session: AsyncSession
) -> None:
    if not msg.from_user or not is_admin(msg.from_user.id, se):
        await state.clear()
        return

    try:
        value = int(msg.text.strip())
    except ValueError:
        await msg.answer(tx.ADMIN_PRO_AI_INT_ERROR)
        return
    if value < 1 or value > 200:
        await msg.answer(tx.ADMIN_PRO_AI_RANGE_ERROR)
        return

    cfg = await get_runtime_config(session)
    cfg.pro_daily_ai_limit = value
    cfg.updated_at = datetime.now(UTC).replace(tzinfo=None)
    await session.commit()
    await state.clear()

    await msg.answer(
        _admin_runtime_config_text(runtime_config_view(cfg)),
        reply_markup=admin_config_kb(),
    )


@router.message(SettingsState.waiting_for_review_sample_limit, F.text)
async def wb_admin_cfg_review_sample_limit_msg(
    msg: Message, state: FSMContext, session: AsyncSession
) -> None:
    if not msg.from_user or not is_admin(msg.from_user.id, se):
        await state.clear()
        return

    try:
        value = int(msg.text.strip())
    except ValueError:
        await msg.answer(tx.ADMIN_REVIEW_SAMPLE_LIMIT_INT_ERROR)
        return
    if value < 10 or value > 200:
        await msg.answer(tx.ADMIN_REVIEW_SAMPLE_LIMIT_RANGE_ERROR)
        return

    cfg = await get_runtime_config(session)
    cfg.review_sample_limit_per_side = value
    cfg.updated_at = datetime.now(UTC).replace(tzinfo=None)
    await session.commit()
    await state.clear()

    await msg.answer(
        _admin_runtime_config_text(runtime_config_view(cfg)),
        reply_markup=admin_config_kb(),
    )


@router.message(SettingsState.waiting_for_analysis_model, F.text)
async def wb_admin_cfg_analysis_model_msg(
    msg: Message, state: FSMContext, session: AsyncSession
) -> None:
    if not msg.from_user or not is_admin(msg.from_user.id, se):
        await state.clear()
        return

    model = msg.text.strip()
    if not model:
        await msg.answer(tx.ADMIN_MODEL_EMPTY_ERROR)
        return

    cfg = await get_runtime_config(session)
    cfg.analysis_model = model
    cfg.updated_at = datetime.now(UTC).replace(tzinfo=None)
    await session.commit()
    await state.clear()

    await msg.answer(
        _admin_runtime_config_text(runtime_config_view(cfg)),
        reply_markup=admin_config_kb(),
    )


@router.callback_query(F.data.regexp(r"wbm:admin:stats:(\d+)"))
async def wb_admin_stats_cb(
    cb: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return

    await state.clear()

    days = int(cb.data.split(":")[3])
    if days not in {7, 14, 30}:
        await cb.answer(tx.ADMIN_INVALID_PERIOD, show_alert=True)
        return

    stats = await get_admin_stats(session, days=days)
    await cb.message.edit_text(
        _admin_stats_text(stats),
        reply_markup=admin_panel_kb(selected_days=days),
    )


@router.callback_query(F.data == "wbm:admin:grantpro")
async def wb_admin_grant_pro_cb(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return

    await state.set_state(SettingsState.waiting_for_pro_grant)
    await cb.message.edit_text(
        tx.ADMIN_GRANT_PRO_PROMPT,
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
            tx.ADMIN_GRANT_PRO_FORMAT_ERROR,
            reply_markup=admin_grant_pro_kb(),
        )
        return

    tg_user_id, days = parsed
    user = await get_monitor_user_by_tg_id(session, tg_user_id)
    if not user:
        await msg.answer(
            tx.ADMIN_GRANT_PRO_USER_NOT_FOUND,
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
    cfg = runtime_config_view(await get_runtime_config(session))
    await set_user_tracks_interval(session, user.id, cfg.pro_interval_min)
    await session.commit()
    await MonitorUserRD.invalidate(redis, user.tg_user_id)

    await state.clear()
    await msg.answer(
        tx.ADMIN_GRANT_PRO_DONE.format(
            tg_user_id=user.tg_user_id,
            days=days,
            expires=user.pro_expires_at.strftime("%d.%m.%Y %H:%M"),
        ),
        reply_markup=admin_panel_kb(selected_days=7),
    )

    try:
        await msg.bot.send_message(
            user.tg_user_id,
            tx.ADMIN_GRANT_PRO_USER_NOTIFY.format(
                days=days,
                expires=user.pro_expires_at.strftime("%d.%m.%Y %H:%M"),
            ),
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
    cfg = runtime_config_view(await get_runtime_config(session))
    used = await count_user_tracks(session, user.id, active_only=True)
    await cb.message.edit_text(
        dashboard_text(
            user.plan,
            used,
            free_interval_min=cfg.free_interval_min,
            pro_interval_min=cfg.pro_interval_min,
        ),
        reply_markup=dashboard_kb(is_admin(cb.from_user.id, se)),
    )


@router.callback_query(F.data.regexp(r"wbm:back:(\d+)"))
async def wb_back_cb(cb: CallbackQuery, session: AsyncSession, redis: "Redis") -> None:
    track_id = int(cb.data.split(":")[2])
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    tracks = await get_user_tracks(session, user.id)
    for idx, track in enumerate(tracks):
        if track.id == track_id:
            await cb.message.edit_text(
                format_track_text(track),
                reply_markup=await _track_kb_with_usage(
                    session=session,
                    redis=redis,
                    user_tg_id=cb.from_user.id,
                    user_plan=user.plan,
                    track=track,
                    page=idx,
                    total=len(tracks),
                ),
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
                    text=tx.SETTINGS_CANCEL_BTN,
                    callback_data=f"wbm:settings:{track_id}",
                )
            ]
        ]
    )
    await cb.message.edit_text(
        tx.SETTINGS_PRICE_PROMPT,
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
        await msg.answer(tx.SETTINGS_PRICE_ERROR)
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
            tx.SETTINGS_PRICE_DONE.format(title=track.title, price=new_price),
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
                    text=tx.SETTINGS_CANCEL_BTN,
                    callback_data=f"wbm:settings:{track_id}",
                )
            ]
        ]
    )
    await cb.message.edit_text(
        tx.SETTINGS_DROP_PROMPT,
        reply_markup=cancel_kb,
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
        await msg.answer(tx.SETTINGS_DROP_ERROR)
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
            tx.SETTINGS_DROP_DONE.format(drop=new_drop, title=track.title),
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
        await cb.answer(tx.TRACK_NOT_FOUND, show_alert=True)
        return

    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    track.target_price = None
    await session.commit()

    await cb.message.edit_text(
        format_track_text(track) + tx.SETTINGS_SUFFIX,
        reply_markup=settings_kb(
            track_id,
            has_sizes=bool(track.last_sizes),
            pro_plan=user.plan == "pro",
            qty_on=track.watch_qty,
        ),
    )
    await cb.answer(tx.SETTINGS_PRICE_RESET_DONE)


@router.callback_query(F.data.regexp(r"wbm:drop_reset:(\d+)"))
async def wb_settings_drop_reset_cb(cb: CallbackQuery, session: AsyncSession) -> None:
    track_id = int(cb.data.split(":")[2])
    track = await get_user_track_by_id(session, track_id)
    if not track:
        await cb.answer(tx.TRACK_NOT_FOUND, show_alert=True)
        return

    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    track.target_drop_percent = None
    await session.commit()

    await cb.message.edit_text(
        format_track_text(track) + tx.SETTINGS_SUFFIX,
        reply_markup=settings_kb(
            track_id,
            has_sizes=bool(track.last_sizes),
            pro_plan=user.plan == "pro",
            qty_on=track.watch_qty,
        ),
    )
    await cb.answer(tx.SETTINGS_DROP_RESET_DONE)


@router.callback_query(F.data.regexp(r"wbm:qty:(\d+)"))
async def wb_settings_qty_cb(cb: CallbackQuery, session: AsyncSession) -> None:
    track_id = int(cb.data.split(":")[2])
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )

    if user.plan != "pro":
        await cb.answer(tx.SETTINGS_QTY_PRO_ONLY, show_alert=True)
        return

    track = await get_user_track_by_id(session, track_id)
    if not track:
        await cb.answer(tx.TRACK_NOT_FOUND, show_alert=True)
        return

    track.watch_qty = not track.watch_qty
    await session.commit()

    try:
        await cb.message.edit_text(
            format_track_text(track) + tx.SETTINGS_SUFFIX,
            reply_markup=settings_kb(
                track_id,
                has_sizes=bool(track.watch_sizes),
                pro_plan=True,
                qty_on=track.watch_qty,
            ),
        )
    except TelegramBadRequest:
        pass

    await cb.answer(
        tx.SETTINGS_QTY_ANSWER.format(
            state=(
                tx.SETTINGS_QTY_STATE_ON
                if track.watch_qty
                else tx.SETTINGS_QTY_STATE_OFF
            )
        )
    )


@router.callback_query(F.data.regexp(r"wbm:sizes:(\d+)"))
async def wb_settings_sizes_cb(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    track_id = int(cb.data.split(":")[2])
    track = await get_user_track_by_id(session, track_id)

    if not track or not track.last_sizes:
        await cb.answer(tx.SETTINGS_NO_SIZES, show_alert=True)
        return

    await state.update_data(track_id=track_id)
    await state.set_state(SettingsState.waiting_for_sizes)

    sizes_str = ", ".join(track.last_sizes)
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=tx.SETTINGS_CANCEL_BTN,
                    callback_data=f"wbm:settings:{track_id}",
                )
            ]
        ]
    )
    await cb.message.edit_text(
        tx.SETTINGS_SIZES_PROMPT.format(sizes=sizes_str),
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
    if text == "0" or text.lower() == tx.SETTINGS_SIZES_ALL_KEYWORD:
        track.watch_sizes = track.last_sizes or []
    else:
        sizes = [s.strip() for s in text.split(",")]
        # Filter sizes to only valid ones if we have them
        if track.last_sizes:
            sizes = [s for s in sizes if s in track.last_sizes]
        track.watch_sizes = sizes

    await session.commit()
    await msg.answer(
        tx.SETTINGS_SIZES_DONE.format(
            sizes=(
                ", ".join(track.watch_sizes)
                if track.watch_sizes
                else tx.SETTINGS_SIZES_NONE
            )
        )
    )
    await state.clear()
