"""quick_item.py — handlers for quick WB item preview/add/reviews/search."""

from __future__ import annotations

import logging
from decimal import Decimal
from html import escape
from typing import TYPE_CHECKING

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, LinkPreviewOptions
from sqlalchemy import select

from bot.callbacks import QuickAction, QuickActionCb, QuickModeCb
from bot.enums import FeatureName, SearchMode
from bot.db.models import TrackModel
from bot.db.redis import (
    FeatureUsageDailyRD,
    QuickReviewInsightsCacheRD,
    QuickSimilarItemRD,
    QuickSimilarSearchCacheRD,
    WbSimilarItemRD,
)
from bot import text as tx
from bot.keyboards.inline import (
    format_track_text,
    quick_back_preview_kb,
    quick_back_search_kb,
    quick_item_kb,
    quick_search_mode_kb,
)
from bot.services.repository import (
    count_user_tracks,
    create_track,
    get_or_create_monitor_user,
    get_runtime_config,
    get_user_tracks,
    runtime_config_view,
)
from bot.services.review_analysis import (
    ReviewAnalysisConfigError,
    ReviewAnalysisError,
    ReviewAnalysisRateLimitError,
    ReviewInsights,
    analyze_reviews_with_llm,
)
from bot.services.wb_client import (
    fetch_product,
    search_similar_cheaper_title_only,
)
from bot.services.similar_filter import (
    _live_filter_cheaper_in_stock,
    _filter_candidates_by_numeric_tokens,
    _normalize_brand,
    _is_same_brand,
)
from bot.settings import se

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession

from bot.handlers._shared import (
    _feature_limit,
    _feature_period,
    _feature_period_title,
    _feature_usage_snapshot,
    _is_paid_plan,
    _model_signature,
    _track_limit,
    _track_kb_with_usage,
)

router = Router()
logger = logging.getLogger(__name__)


async def _quick_item_kb_with_usage(
    *,
    session: "AsyncSession",
    redis: "Redis",
    user_tg_id: int,
    user_plan: str,
    wb_item_id: int,
    already_tracked: bool,
) -> InlineKeyboardMarkup:
    usage = await _feature_usage_snapshot(
        session=session,
        redis=redis,
        user_tg_id=user_tg_id,
        user_plan=user_plan,
    )
    return quick_item_kb(
        wb_item_id,
        already_tracked=already_tracked,
        reviews_btn_text=tx.button_with_usage(
            tx.QUICK_REVIEWS_BTN,
            used=usage.reviews_used,
            limit=usage.reviews_limit,
        ),
        search_btn_text=tx.button_with_usage(
            tx.QUICK_SEARCH_BTN,
            used=usage.cheap_used,
            limit=usage.cheap_limit,
        ),
    )


def _quick_preview_text(*, product: object, already_tracked: bool) -> str:
    price = getattr(product, "price", None)
    rating = getattr(product, "rating", None)
    reviews = getattr(product, "reviews", None)
    in_stock = bool(getattr(product, "in_stock", False))
    title = str(getattr(product, "title", "Товар"))
    brand = str(getattr(product, "brand", "") or "").strip()

    price_text = f"{price}₽" if price else tx.TRACK_ADDED_PRICE_UNKNOWN
    rating_text = (
        tx.TRACK_ADDED_RATING_WITH_REVIEWS.format(rating=rating, reviews=reviews or 0)
        if rating is not None
        else tx.TRACK_ADDED_RATING_UNKNOWN
    )
    in_stock_text = (
        tx.TRACK_ADDED_IN_STOCK_YES if in_stock else tx.TRACK_ADDED_IN_STOCK_NO
    )

    text = tx.QUICK_ITEM_PREVIEW_TEMPLATE.format(
        title=title,
        price=price_text,
        rating=rating_text,
        in_stock=in_stock_text,
    )
    if brand:
        text = f"{text}\n🏷 Бренд: {escape(brand)}"
    if already_tracked:
        text = f"{text}\n\n{tx.QUICK_ALREADY_TRACKED}"
    return text


@router.callback_query(QuickActionCb.filter(F.action == QuickAction.PREVIEW))
async def wb_quick_preview_cb(
    cb: CallbackQuery,
    callback_data: QuickActionCb,
    session: "AsyncSession",
    redis: "Redis",
) -> None:
    wb_item_id = callback_data.wb_item_id
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username, redis=redis
    )
    existing = await session.scalar(
        select(TrackModel).where(
            TrackModel.user_id == user.id,
            TrackModel.wb_item_id == wb_item_id,
            TrackModel.is_deleted.is_(False),
        )
    )
    product = await fetch_product(redis, wb_item_id, use_cache=False)
    if not product:
        await cb.answer(tx.PRODUCT_FETCH_ERROR, show_alert=True)
        return
    await cb.answer()
    await cb.message.edit_text(
        _quick_preview_text(product=product, already_tracked=bool(existing)),
        reply_markup=await _quick_item_kb_with_usage(
            session=session,
            redis=redis,
            user_tg_id=cb.from_user.id,
            user_plan=user.plan,
            wb_item_id=wb_item_id,
            already_tracked=bool(existing),
        ),
    )


@router.callback_query(QuickActionCb.filter(F.action == QuickAction.ADD))
async def wb_quick_add_cb(
    cb: CallbackQuery,
    callback_data: QuickActionCb,
    session: "AsyncSession",
    redis: "Redis",
) -> None:
    wb_item_id = callback_data.wb_item_id
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username, redis=redis
    )

    existing = await session.scalar(
        select(TrackModel).where(
            TrackModel.user_id == user.id,
            TrackModel.wb_item_id == wb_item_id,
            TrackModel.is_deleted.is_(False),
        )
    )
    if existing:
        await cb.answer(tx.QUICK_ALREADY_TRACKED, show_alert=True)
        return

    track_count = await count_user_tracks(session, user.id, active_only=True)
    limit = _track_limit(user.plan)
    if track_count >= limit:
        await cb.answer(tx.TRACK_LIMIT_REACHED.format(limit=limit), show_alert=True)
        return

    product = await fetch_product(redis, wb_item_id)
    if not product:
        await cb.answer(tx.PRODUCT_FETCH_ERROR, show_alert=True)
        return

    cfg = runtime_config_view(await get_runtime_config(session))
    interval = (
        cfg.pro_interval_min if _is_paid_plan(user.plan) else cfg.free_interval_min
    )
    track_url = f"https://www.wildberries.ru/catalog/{wb_item_id}/detail.aspx"
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

    tracks = await get_user_tracks(session, user.id)
    page = next((idx for idx, t in enumerate(tracks) if t.id == track.id), 0)

    await cb.answer("✅ Добавил в товары")
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


@router.callback_query(QuickActionCb.filter(F.action == QuickAction.REVIEWS))
async def wb_quick_reviews_cb(
    cb: CallbackQuery,
    callback_data: QuickActionCb,
    session: "AsyncSession",
    redis: "Redis",
) -> None:
    wb_item_id = callback_data.wb_item_id
    product = await fetch_product(redis, wb_item_id, use_cache=False)
    if not product:
        await cb.answer(tx.PRODUCT_FETCH_ERROR, show_alert=True)
        return

    back_preview_kb = quick_back_preview_kb(wb_item_id)

    await cb.answer()
    await cb.message.edit_text(
        tx.REVIEWS_ANALYSIS_PROGRESS.format(title=escape(product.title)),
        reply_markup=back_preview_kb,
    )

    review_limit = 50
    model = se.agentplatform_model.strip()
    model_signature = _model_signature(model, review_limit)

    cached = await QuickReviewInsightsCacheRD.get(
        redis, wb_item_id=wb_item_id, model_signature=model_signature
    )
    if cached is not None:
        insights = ReviewInsights(
            strengths=list(cached.strengths),
            weaknesses=list(cached.weaknesses),
            positive_samples=int(cached.positive_samples),
            negative_samples=int(cached.negative_samples),
            positive_total=int(cached.positive_total),
            negative_total=int(cached.negative_total),
            sample_limit_per_side=int(cached.sample_limit_per_side),
        )
    else:
        user = await get_or_create_monitor_user(
            session, cb.from_user.id, cb.from_user.username
        )
        period = _feature_period(user.plan)
        period_title = _feature_period_title(period)
        feature_limit = _feature_limit(user.plan, "reviews")
        allowed, _used = await FeatureUsageDailyRD.try_consume(
            redis,
            tg_user_id=cb.from_user.id,
            feature=FeatureName.REVIEWS,
            limit=feature_limit,
            period=period,
            session=session,
        )
        if not allowed:
            await cb.message.edit_text(
                tx.FEATURE_LIMIT_REVIEWS_REACHED.format(
                    limit=feature_limit, period=period_title
                ),
                reply_markup=back_preview_kb,
            )
            return

        try:
            insights = await analyze_reviews_with_llm(
                wb_item_id=wb_item_id,
                product_title=product.title,
                api_key=se.agentplatform_api_key,
                model=model,
                api_base_url=se.agentplatform_base_url,
                sample_limit_per_side=review_limit,
            )
        except (
            ReviewAnalysisConfigError,
            ReviewAnalysisError,
            ReviewAnalysisRateLimitError,
        ) as exc:
            await cb.message.edit_text(str(exc), reply_markup=back_preview_kb)
            return
        except Exception:
            logger.exception("quick-reviews failed: wb_item_id=%s", wb_item_id)
            await cb.message.edit_text(
                "❌ Не удалось выполнить анализ отзывов. Попробуй ещё раз через минуту.",
                reply_markup=back_preview_kb,
            )
            return

        await QuickReviewInsightsCacheRD(
            wb_item_id=wb_item_id,
            model_signature=model_signature,
            strengths=list(insights.strengths),
            weaknesses=list(insights.weaknesses),
            positive_samples=int(insights.positive_samples),
            negative_samples=int(insights.negative_samples),
            positive_total=int(insights.positive_total),
            negative_total=int(insights.negative_total),
            sample_limit_per_side=int(insights.sample_limit_per_side),
        ).save(redis)

    from bot.services.review_analysis import review_insights_text as _rit

    await cb.message.edit_text(
        _rit(product.title, insights)
        if callable(_rit)
        else tx.review_insights_text(product.title, insights),
        reply_markup=back_preview_kb,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


@router.callback_query(QuickActionCb.filter(F.action == QuickAction.SEARCH))
async def wb_quick_search_cb(
    cb: CallbackQuery,
    callback_data: QuickActionCb,
    session: "AsyncSession",
    redis: "Redis",
) -> None:
    wb_item_id = callback_data.wb_item_id
    product = await fetch_product(redis, wb_item_id)
    if not product:
        await cb.answer(tx.PRODUCT_FETCH_ERROR, show_alert=True)
        return
    await cb.answer()
    await cb.message.edit_text(
        tx.SEARCH_MODE_PROMPT,
        reply_markup=quick_search_mode_kb(wb_item_id),
    )


@router.callback_query(QuickModeCb.filter())
async def wb_quick_searchmode_cb(
    cb: CallbackQuery,
    callback_data: QuickModeCb,
    session: "AsyncSession",
    redis: "Redis",
) -> None:
    mode = callback_data.mode
    wb_item_id = callback_data.wb_item_id

    product = await fetch_product(redis, wb_item_id, use_cache=False)
    if not product or product.price is None:
        await cb.answer(tx.PRODUCT_FETCH_ERROR, show_alert=True)
        return

    back_quick_kb = quick_back_search_kb(wb_item_id)

    cached_search = await QuickSimilarSearchCacheRD.get(
        redis, wb_item_id=wb_item_id, mode=mode.value
    )
    alternatives: list[WbSimilarItemRD] = []
    current_price_text = str(product.price)
    if cached_search is not None and (
        mode != SearchMode.CHEAP or cached_search.base_price == current_price_text
    ):
        alternatives = [
            WbSimilarItemRD(
                wb_item_id=item.wb_item_id,
                title=item.title,
                price=item.price,
                url=item.url,
                brand=item.brand,
            )
            for item in cached_search.items
        ]

    color_relaxed = False
    await cb.answer()

    if not alternatives:
        user = await get_or_create_monitor_user(
            session, cb.from_user.id, cb.from_user.username
        )
        period = _feature_period(user.plan)
        period_title = _feature_period_title(period)
        feature_limit = _feature_limit(user.plan, "cheap")
        allowed, _used = await FeatureUsageDailyRD.try_consume(
            redis,
            tg_user_id=cb.from_user.id,
            feature=FeatureName.CHEAP,
            limit=feature_limit,
            period=period,
            session=session,
        )
        if not allowed:
            await cb.answer(
                tx.FEATURE_LIMIT_CHEAP_REACHED.format(
                    limit=feature_limit, period=period_title
                ),
                show_alert=True,
            )
            return

        progress_text = (
            tx.FIND_CHEAPER_PROGRESS.format(title=escape(product.title))
            if mode == SearchMode.CHEAP
            else tx.FIND_SIMILAR_PROGRESS.format(title=escape(product.title))
        )
        await cb.message.edit_text(progress_text, reply_markup=back_quick_kb)

        found = await search_similar_cheaper_title_only(
            base_title=product.title,
            base_brand=product.brand,
            base_subject_id=product.subject_id,
            match_percent_threshold=None,
            max_price=product.price
            if mode == SearchMode.CHEAP
            else Decimal("99999999"),
            exclude_wb_item_id=wb_item_id,
            limit=20,
        )
        live_confirmed = await _live_filter_cheaper_in_stock(
            redis,
            found,
            current_price=product.price,
            base_kind_id=product.kind_id,
            base_subject_id=product.subject_id,
            base_brand=product.brand,
            base_colors=product.colors,
            enforce_color=True,
            require_cheaper=(mode == SearchMode.CHEAP),
            limit=10,
            log_prefix=f"quick_id={wb_item_id} mode={mode.value}",
        )
        if mode == SearchMode.CHEAP and not live_confirmed:
            live_confirmed = await _live_filter_cheaper_in_stock(
                redis,
                found,
                current_price=product.price,
                base_kind_id=product.kind_id,
                base_subject_id=product.subject_id,
                base_brand=product.brand,
                base_colors=product.colors,
                enforce_color=False,
                require_cheaper=True,
                limit=10,
                log_prefix=f"quick_id={wb_item_id} mode={mode.value} color_relaxed",
            )
            if live_confirmed:
                color_relaxed = True

        live_confirmed = _filter_candidates_by_numeric_tokens(
            base_title=product.title,
            candidates=live_confirmed,
        )
        alternatives = [
            WbSimilarItemRD(
                wb_item_id=item.wb_item_id,
                title=item.title,
                price=str(item.price),
                url=item.url,
                brand=item.brand,
            )
            for item in live_confirmed[:10]
        ]
        await QuickSimilarSearchCacheRD(
            wb_item_id=wb_item_id,
            mode=mode.value,
            base_price=current_price_text,
            items=[
                QuickSimilarItemRD(
                    wb_item_id=item.wb_item_id,
                    title=item.title,
                    price=item.price,
                    url=item.url,
                    brand=item.brand,
                )
                for item in alternatives
            ],
        ).save(redis)

    if not alternatives:
        text = (
            tx.FIND_CHEAPER_EMPTY.format(
                title=escape(product.title), price=current_price_text
            )
            if mode == SearchMode.CHEAP
            else tx.FIND_SIMILAR_EMPTY.format(title=escape(product.title))
        )
        await cb.message.edit_text(text, reply_markup=back_quick_kb)
        return

    base_brand = _normalize_brand(getattr(product, "brand", None))
    alternatives = sorted(
        alternatives,
        key=lambda x: (
            0 if _is_same_brand(base_brand, getattr(x, "brand", None)) else 1,
            Decimal(str(x.price)),
        ),
    )
    header = (
        tx.FIND_CHEAPER_HEADER.format(
            price=current_price_text, title=escape(product.title)
        )
        if mode == SearchMode.CHEAP
        else tx.FIND_SIMILAR_HEADER.format(title=escape(product.title))
    )
    lines = [header, ""]
    if color_relaxed:
        lines.append(
            "ℹ️ Для расширения выдачи ослабил фильтр по цвету (остальные проверки сохранены)."
        )
        lines.append("")

    mixed_brand_output = False
    for idx, item in enumerate(alternatives, start=1):
        title_text = escape(item.title)
        item_brand = _normalize_brand(getattr(item, "brand", None))
        if item_brand and item_brand != base_brand:
            mixed_brand_output = True
        if getattr(item, "brand", None):
            title_text = f"{escape(str(item.brand))} · {title_text}"
        lines.append(
            f'{idx}. <a href="{item.url}">{title_text}</a> — <b>{item.price} ₽</b>'
        )
    lines.append("")
    if mixed_brand_output:
        lines.append("ℹ️ В выдаче есть товары других брендов, чтобы расширить выбор.")
    lines.append(tx.FIND_CHEAPER_TIP)
    await cb.message.edit_text(
        "\n".join(lines),
        reply_markup=back_quick_kb,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
