"""find_cheaper.py — wb_find_cheaper_cb and wb_reviews_analysis_cb handlers."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html import escape
from typing import TYPE_CHECKING

from aiogram import Router, F
from aiogram.types import CallbackQuery, LinkPreviewOptions

from bot.callbacks import TrackAction, TrackActionCb, TrackModeCb
from bot.enums import FeatureName, SearchMode
from bot.db.redis import (
    FeatureUsageDailyRD,
    WbReviewInsightsCacheRD,
    WbSimilarItemRD,
    WbSimilarSearchCacheRD,
)
from bot import text as tx
from bot.keyboards.inline import (
    reviews_back_to_track_kb,
    track_search_back_kb,
    track_search_mode_kb,
)
from bot.services.repository import (
    get_or_create_monitor_user,
    get_runtime_config,
    get_user_track_by_id,
    log_event,
    runtime_config_view,
)
from bot.services.review_analysis import (
    ReviewAnalysisConfigError,
    ReviewAnalysisError,
    ReviewAnalysisRateLimitError,
    ReviewInsights,
    analyze_reviews_with_llm,
)
from bot.services.cheap_ai import rerank_similar_with_llm
from bot.services.wb_client import (
    WbSimilarProduct,
    fetch_product,
    search_similar_cheaper_title_only,
)

try:
    from bot.services.wb_similar_selenium import fetch_similar_products
except ModuleNotFoundError:  # optional dependency on server
    fetch_similar_products = None
from bot.services.similar_filter import (
    _live_filter_cheaper_in_stock,
    _filter_candidates_by_numeric_tokens,
    _normalize_brand,
    _is_same_brand,
    _search_wb_loose_alternatives,
)
from bot.settings import se

import os

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession

from bot.handlers._shared import (
    _feature_limit,
    _feature_period,
    _feature_period_title,
    _model_signature,
    _progress_spinner,
    _stop_spinner,
)

_WB_ENABLE_SELENIUM_SIMILAR = os.getenv(
    "WB_ENABLE_SELENIUM_SIMILAR", "0"
).strip().lower() in {"1", "true", "yes", "on"}

router = Router()
logger = logging.getLogger(__name__)


async def _get_user_and_track(
    *,
    session: "AsyncSession",
    tg_user_id: int,
    username: str | None,
    track_id: int,
):
    user = await get_or_create_monitor_user(session, tg_user_id, username)
    track = await get_user_track_by_id(session, track_id, user_id=user.id)
    return user, track


@router.callback_query(TrackActionCb.filter(F.action == TrackAction.CHEAP))
async def wb_search_mode_cb(
    cb: CallbackQuery,
    callback_data: TrackActionCb,
    session: "AsyncSession",
) -> None:
    track_id = callback_data.track_id
    _user, track = await _get_user_and_track(
        session=session,
        tg_user_id=cb.from_user.id,
        username=cb.from_user.username,
        track_id=track_id,
    )
    if not track:
        await cb.answer(tx.TRACK_NOT_FOUND, show_alert=True)
        return
    await cb.answer()
    await cb.message.edit_text(
        tx.SEARCH_MODE_PROMPT,
        reply_markup=track_search_mode_kb(track.id),
    )


@router.callback_query(TrackModeCb.filter())
async def wb_find_cheaper_cb(
    cb: CallbackQuery,
    callback_data: TrackModeCb,
    session: "AsyncSession",
    redis: "Redis",
) -> None:
    mode = callback_data.mode
    track_id = callback_data.track_id
    user, track = await _get_user_and_track(
        session=session,
        tg_user_id=cb.from_user.id,
        username=cb.from_user.username,
        track_id=track_id,
    )
    if not track:
        await cb.answer(tx.TRACK_NOT_FOUND, show_alert=True)
        return

    back_kb = track_search_back_kb(track.id)

    cfg = runtime_config_view(await get_runtime_config(session))
    color_relaxed = False
    cached = await WbSimilarSearchCacheRD.get(redis, track.id, mode=mode.value)
    base_brand: str | None = None
    if cached is None or cached.match_percent != cfg.cheap_match_percent:
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

        await log_event(
            session,
            track.id,
            "cheap_scan" if mode == SearchMode.CHEAP else "similar_scan",
            f"{mode.value}:{track.id}:{cb.from_user.id}:{datetime.now(UTC).timestamp()}",
        )
        await session.commit()

        await cb.answer(tx.FIND_CHEAPER_ANSWER)
        progress_text = (
            tx.FIND_CHEAPER_PROGRESS.format(title=escape(track.title))
            if mode == SearchMode.CHEAP
            else tx.FIND_SIMILAR_PROGRESS.format(title=escape(track.title))
        )
        spinner_task = asyncio.create_task(
            _progress_spinner(cb.message, base_text=progress_text, reply_markup=back_kb)
        )

        try:
            color_relaxed = False
            current = await fetch_product(redis, track.wb_item_id, use_cache=False)
            if not current or current.price is None:
                await cb.message.edit_text(
                    tx.FIND_CHEAPER_PRICE_ERROR, reply_markup=back_kb
                )
                return
            base_brand = _normalize_brand(current.brand)

            if _WB_ENABLE_SELENIUM_SIMILAR and fetch_similar_products is not None:
                try:
                    selenium_items = await asyncio.to_thread(
                        fetch_similar_products,
                        track.wb_item_id,
                        limit=40,
                        timeout_sec=20.0,
                        headless=True,
                    )
                except Exception:
                    logger.exception("Selenium similar parser failed")
                    selenium_items = []
            elif _WB_ENABLE_SELENIUM_SIMILAR:
                logger.warning(
                    "Selenium similar parser enabled, but selenium dependency is unavailable"
                )
                selenium_items = []
            else:
                selenium_items = []

            reranked: list[WbSimilarItemRD] = []
            if selenium_items:
                priced = [
                    i
                    for i in selenium_items
                    if i.nm_id != track.wb_item_id and i.final_price is not None
                ]
                priced.sort(key=lambda i: i.final_price)
                cheaper = [i for i in priced if i.final_price < current.price]
                selected = (
                    (cheaper[:10] if cheaper else priced[:10])
                    if mode == SearchMode.CHEAP
                    else priced[:10]
                )
                reranked = [
                    WbSimilarItemRD(
                        wb_item_id=i.nm_id,
                        title=i.title,
                        price=str(i.final_price),
                        url=i.product_url,
                    )
                    for i in selected
                ]

            if not reranked:
                found = await search_similar_cheaper_title_only(
                    base_title=current.title or track.title,
                    base_brand=current.brand,
                    base_subject_id=current.subject_id,
                    match_percent_threshold=cfg.cheap_match_percent,
                    max_price=current.price
                    if mode == SearchMode.CHEAP
                    else Decimal("99999999"),
                    exclude_wb_item_id=track.wb_item_id,
                    limit=20,
                )
                if found:
                    live_confirmed = await _live_filter_cheaper_in_stock(
                        redis,
                        found,
                        current_price=current.price,
                        base_kind_id=current.kind_id,
                        base_subject_id=current.subject_id,
                        base_brand=current.brand,
                        base_colors=current.colors,
                        enforce_color=True,
                        require_cheaper=(mode == SearchMode.CHEAP),
                        limit=20,
                        log_prefix=f"track_id={track.id} mode={mode.value} stage=search",
                    )
                    if mode == SearchMode.CHEAP and not live_confirmed:
                        live_confirmed = await _live_filter_cheaper_in_stock(
                            redis,
                            found,
                            current_price=current.price,
                            base_kind_id=current.kind_id,
                            base_subject_id=current.subject_id,
                            base_brand=current.brand,
                            base_colors=current.colors,
                            enforce_color=False,
                            require_cheaper=True,
                            limit=20,
                            log_prefix=f"track_id={track.id} mode={mode.value} stage=search_color_relaxed",
                        )
                    if mode == SearchMode.SIMILAR and len(live_confirmed) < 3:
                        relaxed = await _live_filter_cheaper_in_stock(
                            redis,
                            found,
                            current_price=current.price,
                            base_kind_id=current.kind_id,
                            base_subject_id=current.subject_id,
                            base_brand=current.brand,
                            base_colors=current.colors,
                            enforce_color=False,
                            require_cheaper=False,
                            limit=20,
                            log_prefix=f"track_id={track.id} mode={mode.value} stage=search_relaxed",
                        )
                        if len(relaxed) > len(live_confirmed):
                            live_confirmed = relaxed
                            color_relaxed = True

                    live_confirmed = _filter_candidates_by_numeric_tokens(
                        base_title=current.title or track.title,
                        candidates=live_confirmed,
                    )
                    if live_confirmed and len(live_confirmed) > 3:
                        llm_ranked = await rerank_similar_with_llm(
                            api_key=se.agentplatform_api_key,
                            model=se.agentplatform_model,
                            api_base_url=se.agentplatform_base_url,
                            base_title=current.title or track.title,
                            base_price=str(current.price),
                            base_entity=current.entity,
                            base_subject_id=current.subject_id,
                            base_brand=current.brand,
                            candidates=live_confirmed,
                            limit=10,
                        )
                        reranked = [
                            WbSimilarItemRD(
                                wb_item_id=i.wb_item_id,
                                title=i.title,
                                price=str(i.price),
                                url=i.url,
                            )
                            for i in llm_ranked[:10]
                        ]
                    elif live_confirmed:
                        reranked = [
                            WbSimilarItemRD(
                                wb_item_id=i.wb_item_id,
                                title=i.title,
                                price=str(i.price),
                                url=i.url,
                            )
                            for i in live_confirmed[:10]
                        ]

            if not reranked:
                reranked = await _search_wb_loose_alternatives(
                    base_title=current.title or track.title,
                    exclude_wb_item_id=track.wb_item_id,
                    max_price=current.price if mode == SearchMode.CHEAP else None,
                    limit=8,
                )

            if reranked:
                live_input = [
                    WbSimilarProduct(
                        wb_item_id=i.wb_item_id,
                        title=i.title,
                        price=Decimal(str(i.price)),
                        url=i.url,
                    )
                    for i in reranked
                ]
                live_confirmed = await _live_filter_cheaper_in_stock(
                    redis,
                    live_input,
                    current_price=current.price,
                    base_kind_id=current.kind_id,
                    base_subject_id=current.subject_id,
                    base_brand=current.brand,
                    base_colors=current.colors,
                    enforce_color=True,
                    require_cheaper=(mode == SearchMode.CHEAP),
                    limit=10,
                    log_prefix=f"track_id={track.id} mode={mode.value} stage=final",
                )
                if mode == SearchMode.CHEAP and not live_confirmed:
                    live_confirmed = await _live_filter_cheaper_in_stock(
                        redis,
                        live_input,
                        current_price=current.price,
                        base_kind_id=current.kind_id,
                        base_subject_id=current.subject_id,
                        base_brand=current.brand,
                        base_colors=current.colors,
                        enforce_color=False,
                        require_cheaper=True,
                        limit=10,
                        log_prefix=f"track_id={track.id} mode={mode.value} stage=final_color_relaxed",
                    )
                if mode == SearchMode.SIMILAR and len(live_confirmed) < 3:
                    relaxed_final = await _live_filter_cheaper_in_stock(
                        redis,
                        live_input,
                        current_price=current.price,
                        base_kind_id=current.kind_id,
                        base_subject_id=current.subject_id,
                        base_brand=current.brand,
                        base_colors=current.colors,
                        enforce_color=False,
                        require_cheaper=False,
                        limit=10,
                        log_prefix=f"track_id={track.id} mode={mode.value} stage=final_relaxed",
                    )
                    if len(relaxed_final) > len(live_confirmed):
                        live_confirmed = relaxed_final
                        color_relaxed = True
                live_confirmed = _filter_candidates_by_numeric_tokens(
                    base_title=current.title or track.title,
                    candidates=live_confirmed,
                )
                reranked = [
                    WbSimilarItemRD(
                        wb_item_id=i.wb_item_id,
                        title=i.title,
                        price=str(i.price),
                        url=i.url,
                    )
                    for i in live_confirmed[:10]
                ]
        finally:
            await _stop_spinner(spinner_task)

        alternatives = reranked
        current_price_text = str(current.price)
        await WbSimilarSearchCacheRD(
            track_id=track.id,
            mode=mode.value,
            base_price=current_price_text,
            match_percent=cfg.cheap_match_percent,
            items=alternatives,
        ).save(redis)
    else:
        await cb.answer()
        alternatives = cached.items
        current_price_text = cached.base_price

    if not alternatives:
        empty_text = (
            tx.FIND_CHEAPER_EMPTY.format(
                title=escape(track.title), price=current_price_text
            )
            if mode == SearchMode.CHEAP
            else tx.FIND_SIMILAR_EMPTY.format(title=escape(track.title))
        )
        await cb.message.edit_text(empty_text, reply_markup=back_kb)
        return

    lines = [
        (
            tx.FIND_CHEAPER_HEADER.format(
                price=current_price_text, title=escape(track.title)
            )
            if mode == SearchMode.CHEAP
            else tx.FIND_SIMILAR_HEADER.format(title=escape(track.title))
        ),
        "",
    ]
    if color_relaxed:
        lines.append(
            "ℹ️ Для расширения выдачи ослабил фильтр по цвету (остальные проверки сохранены)."
        )
        lines.append("")

    try:
        current_price_decimal = Decimal(current_price_text)
    except (InvalidOperation, TypeError):
        current_price_decimal = None

    if mode == SearchMode.CHEAP and current_price_decimal is not None:
        has_cheaper = any(
            True
            for item in alternatives
            if _safe_decimal(item.price) < current_price_decimal  # type: ignore[arg-type]
        )
        if not has_cheaper:
            lines.append("ℹ️ Дешевле не нашлось — показываю ближайшие похожие по цене.")
            lines.append("")

    alternatives = sorted(
        alternatives,
        key=lambda item: (
            0 if _is_same_brand(base_brand, item.brand) else 1,
            _safe_decimal(item.price),
        ),
    )

    mixed_brand_output = False
    for idx, item in enumerate(alternatives, start=1):
        item_brand = _normalize_brand(item.brand)
        if item_brand and not _is_same_brand(base_brand, item.brand):
            mixed_brand_output = True
        title_text = escape(item.title)
        if item.brand:
            title_text = f"{escape(item.brand)} {title_text}"
        lines.append(
            f'{idx}. <a href="{item.url}">{title_text}</a> — <b>{item.price} ₽</b>'
        )
    lines.append("")
    if mixed_brand_output:
        lines.append("ℹ️ В выдаче есть товары других брендов, чтобы расширить выбор.")
    lines.append(tx.FIND_CHEAPER_TIP)
    await cb.message.edit_text(
        "\n".join(lines),
        reply_markup=back_kb,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


def _safe_decimal(price: object) -> Decimal:
    try:
        return Decimal(str(price))
    except (InvalidOperation, TypeError):
        return Decimal("999999999")


@router.callback_query(TrackActionCb.filter(F.action == TrackAction.REVIEWS))
async def wb_reviews_analysis_cb(
    cb: CallbackQuery,
    callback_data: TrackActionCb,
    session: "AsyncSession",
    redis: "Redis",
) -> None:
    track_id = callback_data.track_id
    user, track = await _get_user_and_track(
        session=session,
        tg_user_id=cb.from_user.id,
        username=cb.from_user.username,
        track_id=track_id,
    )
    if not track:
        await cb.answer(tx.TRACK_NOT_FOUND, show_alert=True)
        return

    back_kb = reviews_back_to_track_kb(track.id)

    product = await fetch_product(redis, track.wb_item_id)
    reviews_count: int | None = None
    if product is not None and product.reviews is not None:
        reviews_count = int(product.reviews)
    elif track.last_reviews is not None:
        reviews_count = int(track.last_reviews)

    if reviews_count is not None and reviews_count <= 0:
        await cb.answer()
        await cb.message.edit_text(tx.REVIEWS_ANALYSIS_NO_REVIEWS, reply_markup=back_kb)
        return

    cfg = runtime_config_view(await get_runtime_config(session))
    primary_model = (cfg.analysis_model or "").strip() or se.agentplatform_model.strip()
    review_limit = max(10, min(int(cfg.review_sample_limit_per_side), 200))
    model_signature = _model_signature(primary_model, review_limit)

    cached = await WbReviewInsightsCacheRD.get(redis, track.wb_item_id, model_signature)

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
            period = _feature_period(user.plan)
            period_title = _feature_period_title(period)
            feature_limit = _feature_limit(user.plan, "reviews")
            allowed, _used = await FeatureUsageDailyRD.try_consume(
                redis,
                tg_user_id=cb.from_user.id,
                feature=FeatureName.REVIEWS,
                limit=feature_limit,
                period=period,
            )
            if not allowed:
                await cb.answer(
                    tx.FEATURE_LIMIT_REVIEWS_REACHED.format(
                        limit=feature_limit, period=period_title
                    ),
                    show_alert=True,
                )
                return

            await log_event(
                session,
                track.id,
                "reviews_scan",
                f"reviews:{track.id}:{cb.from_user.id}:{datetime.now(UTC).timestamp()}",
            )
            await session.commit()

            await cb.answer(tx.REVIEWS_ANALYSIS_ANSWER)
            spinner_task = asyncio.create_task(
                _progress_spinner(
                    cb.message,
                    base_text=tx.REVIEWS_ANALYSIS_PROGRESS.format(
                        title=escape(track.title)
                    ),
                    reply_markup=back_kb,
                )
            )
            try:
                insights = await analyze_reviews_with_llm(
                    wb_item_id=track.wb_item_id,
                    product_title=track.title,
                    api_key=se.agentplatform_api_key,
                    model=primary_model,
                    api_base_url=se.agentplatform_base_url,
                    sample_limit_per_side=review_limit,
                )
            finally:
                await _stop_spinner(spinner_task)

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
        await cb.message.edit_text(tx.REVIEWS_ANALYSIS_FAILED, reply_markup=back_kb)
        return

    await cb.message.edit_text(
        tx.review_insights_text(track.title, insights),
        reply_markup=back_kb,
    )
