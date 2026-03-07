from __future__ import annotations

import asyncio
import logging
import os
import re
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from html import escape
from urllib.parse import quote_plus
from typing import TYPE_CHECKING

from aiohttp import ClientSession
from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    LinkPreviewOptions,
    Message,
    PreCheckoutQuery,
)
from sqlalchemy import select

from bot.db.models import TrackModel
from bot.db.redis import (
    FeatureUsageDailyRD,
    MonitorUserRD,
    QuickReviewInsightsCacheRD,
    QuickSimilarItemRD,
    QuickSimilarSearchCacheRD,
    WbReviewInsightsCacheRD,
    WbSimilarItemRD,
    WbSimilarSearchCacheRD,
)
from bot import text as tx
from bot.keyboards.inline import (
    add_item_prompt_kb,
    admin_promo_card_kb,
    admin_promo_input_kb,
    admin_promo_kb,
    admin_promo_list_kb,
    admin_support_ticket_kb,
    back_to_dashboard_kb,
    dashboard_kb,
    dashboard_text,
    format_track_text,
    admin_grant_pro_kb,
    admin_config_input_kb,
    admin_config_kb,
    admin_panel_kb,
    plan_offer_kb,
    plan_overview_kb,
    paged_track_kb,
    ref_kb,
    settings_kb,
    support_kb,
    support_cancel_kb,
    support_media_confirmation_kb,
)
from bot.services.repository import (
    count_active_promos,
    count_promo_activations,
    create_promo_link,
    count_user_tracks,
    deactivate_promo_link,
    get_active_promos_page,
    create_track,
    get_user_active_discount,
    get_or_create_monitor_user,
    get_promo_by_id,
    get_user_track_by_id,
    get_user_tracks,
    mark_discount_activation_consumed,
    toggle_track_active,
    delete_track,
    add_referral_reward_once,
    get_monitor_user_by_tg_id,
    get_admin_stats,
    get_runtime_config,
    runtime_config_view,
    apply_runtime_intervals,
    set_user_tracks_interval,
    log_event,
    create_compare_run,
    get_price_history_stats,
)
from bot.services.review_analysis import (
    ReviewAnalysisConfigError,
    ReviewAnalysisError,
    ReviewInsights,
    ReviewAnalysisRateLimitError,
    analyze_reviews_with_llm,
)
from bot.services.cheap_ai import rerank_similar_with_llm
from bot.services.product_compare import compare_products_with_llm
from bot.services.utils import is_admin
from bot.services.wb_client import (
    extract_wb_item_id,
    fetch_product,
    search_similar_cheaper_title_only,
    WbSimilarProduct,
    WB_HTTP_HEADERS,
    WB_HTTP_PROXY,
)
from bot.services.wb_similar_selenium import fetch_similar_products
from bot.settings import se

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession

    from bot.services.repository import AdminStats, RuntimeConfigView

router = Router()
logger = logging.getLogger(__name__)
_LIKELY_WB_INPUT_RE = re.compile(r"wildberries|wb\.ru|\d{6,15}", re.IGNORECASE)
_ADMIN_PROMO_PAGE_SIZE = 8
_WB_ENABLE_SELENIUM_SIMILAR = os.getenv(
    "WB_ENABLE_SELENIUM_SIMILAR", "0"
).strip().lower() in {"1", "true", "yes", "on"}
_PLAN_PRO_CODE = "pro"
_PLAN_PRO_PLUS_CODE = "proplus"
_PLAN_DB_PRO = "pro"
_PLAN_DB_PRO_PLUS = "pro_plus"
_PAID_PLANS = {_PLAN_DB_PRO, _PLAN_DB_PRO_PLUS}
_PLAN_BASE_AMOUNT = {
    _PLAN_PRO_CODE: 150,
    _PLAN_PRO_PLUS_CODE: 250,
}
_PLAN_DAYS = {
    _PLAN_PRO_CODE: 30,
    _PLAN_PRO_PLUS_CODE: 30,
}
_FEATURE_LIMITS: dict[str, dict[str, int]] = {
    "free": {"cheap": 2, "reviews": 3},
    _PLAN_DB_PRO: {"cheap": 300, "reviews": 180},
    _PLAN_DB_PRO_PLUS: {"cheap": 600, "reviews": 360},
}
_COMPARE_DAILY_LIMIT = 2

_COLOR_ALIASES: dict[str, set[str]] = {
    "black": {"черн", "black"},
    "white": {"бел", "white"},
    "gray": {"сер", "grey", "gray"},
    "beige": {"беж", "beige"},
    "brown": {"корич", "brown"},
    "blue": {"син", "blue", "navy"},
    "light_blue": {"голуб", "light blue"},
    "green": {"зелен", "green", "khaki", "хаки"},
    "red": {"красн", "red"},
    "pink": {"розов", "pink"},
    "purple": {"фиолет", "purple", "violet"},
    "yellow": {"желт", "yellow"},
    "orange": {"оранж", "orange"},
}


def _normalize_match_text(text: str) -> str:
    return " ".join((text or "").lower().replace("ё", "е").split())


def _extract_color_groups(text: str) -> set[str]:
    t = _normalize_match_text(text)
    out: set[str] = set()
    for group, aliases in _COLOR_ALIASES.items():
        if any(alias in t for alias in aliases):
            out.add(group)
    return out


def _color_groups_from_card(colors: list[str] | None) -> set[str]:
    if not colors:
        return set()
    return _extract_color_groups(" ".join(colors))


def _extract_numeric_tokens(text: str) -> set[str]:
    # Model/version/volume-like tokens (e.g. 17, 256, 2024)
    return set(re.findall(r"\b\d{1,4}\b", text or ""))


def _filter_candidates_by_numeric_tokens(
    *,
    base_title: str,
    candidates: list[WbSimilarProduct],
) -> list[WbSimilarProduct]:
    base_nums = _extract_numeric_tokens(base_title)
    if not base_nums:
        return candidates

    out: list[WbSimilarProduct] = []
    for item in candidates:
        cand_nums = _extract_numeric_tokens(item.title)
        if cand_nums and base_nums.isdisjoint(cand_nums):
            continue
        out.append(item)
    return out


def _normalize_brand(brand: str | None) -> str:
    return (brand or "").strip().lower()


def _is_same_brand(base_brand: str | None, candidate_brand: str | None) -> bool:
    b = _normalize_brand(base_brand)
    c = _normalize_brand(candidate_brand)
    return bool(b and c and b == c)


def _sort_by_brand_then_price(
    items: list[WbSimilarProduct], *, base_brand: str | None
) -> list[WbSimilarProduct]:
    if not items:
        return items
    return sorted(
        items,
        key=lambda item: (
            0 if _is_same_brand(base_brand, item.brand) else 1,
            item.price,
        ),
    )


async def _live_filter_cheaper_in_stock(
    redis: "Redis",
    candidates: list[WbSimilarProduct],
    *,
    current_price: Decimal,
    base_kind_id: int | None = None,
    base_subject_id: int | None = None,
    base_brand: str | None = None,
    base_colors: list[str] | None = None,
    enforce_color: bool = True,
    require_cheaper: bool = True,
    limit: int = 12,
    log_prefix: str | None = None,
) -> list[WbSimilarProduct]:
    out: list[WbSimilarProduct] = []
    base_color_groups = _color_groups_from_card(base_colors)

    reason_counts = {
        "fetch_error": 0,
        "no_snapshot_or_price": 0,
        "out_of_stock": 0,
        "not_cheaper": 0,
        "subject_mismatch": 0,
        "kind_mismatch": 0,
        "color_mismatch": 0,
        "accepted": 0,
    }

    sem = asyncio.Semaphore(6)

    async def _fetch_one(item: WbSimilarProduct) -> "WbProductSnapshot | None":
        async with sem:
            try:
                return await fetch_product(redis, item.wb_item_id, use_cache=False)
            except Exception:
                return None

    snaps = await asyncio.gather(*(_fetch_one(item) for item in candidates))

    for item, snap in zip(candidates, snaps):
        if snap is None:
            reason_counts["fetch_error"] += 1
            continue
        if snap.price is None:
            reason_counts["no_snapshot_or_price"] += 1
            continue
        if not snap.in_stock:
            reason_counts["out_of_stock"] += 1
            continue
        if require_cheaper and snap.price >= current_price:
            reason_counts["not_cheaper"] += 1
            continue

        # Category-level filter: subjectId is the most precise WB category signal
        if (
            base_subject_id is not None
            and snap.subject_id is not None
            and snap.subject_id != base_subject_id
        ):
            reason_counts["subject_mismatch"] += 1
            continue

        # Card-level gender/segment proxy from WB kindId
        if (
            base_kind_id is not None
            and snap.kind_id is not None
            and snap.kind_id != base_kind_id
        ):
            reason_counts["kind_mismatch"] += 1
            continue

        # Card-level color matching
        item_color_groups = _color_groups_from_card(snap.colors)
        if (
            enforce_color
            and base_color_groups
            and item_color_groups
            and base_color_groups.isdisjoint(item_color_groups)
        ):
            reason_counts["color_mismatch"] += 1
            continue

        out.append(
            WbSimilarProduct(
                wb_item_id=item.wb_item_id,
                title=snap.title or item.title,
                price=snap.price,
                url=item.url,
                brand=snap.brand or item.brand,
            )
        )
        reason_counts["accepted"] += 1

    out = _sort_by_brand_then_price(out, base_brand=base_brand)[:limit]

    if log_prefix:
        logger.info(
            "%s live-filter stats: total=%s accepted=%s fetch_error=%s no_snapshot_or_price=%s out_of_stock=%s not_cheaper=%s subject_mismatch=%s kind_mismatch=%s color_mismatch=%s",
            log_prefix,
            len(candidates),
            reason_counts["accepted"],
            reason_counts["fetch_error"],
            reason_counts["no_snapshot_or_price"],
            reason_counts["out_of_stock"],
            reason_counts["not_cheaper"],
            reason_counts["subject_mismatch"],
            reason_counts["kind_mismatch"],
            reason_counts["color_mismatch"],
        )

    return out


def _model_signature(model: str, review_limit: int) -> str:
    return f"{model}|limit:{review_limit}"


def _normalize_offer_code(raw: str | None) -> str:
    if raw == _PLAN_PRO_PLUS_CODE:
        return _PLAN_PRO_PLUS_CODE
    return _PLAN_PRO_CODE


def _plan_db_name_from_offer(offer_code: str) -> str:
    normalized = _normalize_offer_code(offer_code)
    if normalized == _PLAN_PRO_PLUS_CODE:
        return _PLAN_DB_PRO_PLUS
    return _PLAN_DB_PRO


def _is_paid_plan(plan: str) -> bool:
    return plan in _PAID_PLANS


def _plan_base_amount(offer_code: str) -> int:
    return _PLAN_BASE_AMOUNT[_normalize_offer_code(offer_code)]


def _plan_days(offer_code: str) -> int:
    return _PLAN_DAYS[_normalize_offer_code(offer_code)]


def _plan_title(offer_code: str) -> str:
    normalized = _normalize_offer_code(offer_code)
    return (
        tx.PLAN_OFFER_PRO_PLUS_TITLE
        if normalized == _PLAN_PRO_PLUS_CODE
        else tx.PLAN_OFFER_PRO_TITLE
    )


def _plan_note(offer_code: str) -> str:
    normalized = _normalize_offer_code(offer_code)
    return (
        tx.PLAN_OFFER_PRO_PLUS_NOTE
        if normalized == _PLAN_PRO_PLUS_CODE
        else tx.PLAN_OFFER_PRO_NOTE
    )


def _discounted_amount(base_amount: int, discount: object | None) -> int:
    if not discount:
        return base_amount
    percent = int(getattr(discount, "percent", 0) or 0)
    return max(1, int(round(base_amount * (100 - percent) / 100)))


def _feature_period(plan: str) -> str:
    return "month" if _is_paid_plan(plan) else "day"


def _feature_period_phrase(period: str) -> str:
    return "в месяц" if period == "month" else "в день"


def _feature_period_title(period: str) -> str:
    return "месяц" if period == "month" else "день"


def _feature_limit(plan: str, feature: str) -> int:
    plan_key = plan if plan in _FEATURE_LIMITS else "free"
    limits = _FEATURE_LIMITS.get(plan_key, _FEATURE_LIMITS["free"])
    return int(limits.get(feature, 0))


def _track_limit(plan: str) -> int:
    if plan == _PLAN_DB_PRO_PLUS:
        return 100
    if plan == _PLAN_DB_PRO:
        return 50
    return 5


def _can_use_compare(*, plan: str, admin: bool) -> bool:
    return admin or plan in _PAID_PLANS


def _has_active_subscription(user: object, *, now: datetime) -> bool:
    plan = str(getattr(user, "plan", "free"))
    if not _is_paid_plan(plan):
        return False
    expires_at = getattr(user, "pro_expires_at", None)
    if expires_at is None:
        return True
    return expires_at >= now


def _build_payment_payload(
    *,
    offer_code: str,
    days: int,
    amount: int,
    discount_activation_id: int | None,
) -> str:
    plan = _normalize_offer_code(offer_code)
    activation = discount_activation_id or 0
    return f"wbm_sub:{plan}:{days}:{activation}:{amount}"


def _parse_payment_payload(payload: str | None) -> tuple[int, int, str, int] | None:
    if not payload:
        return None

    parts = payload.split(":")

    # Legacy payload format.
    if len(parts) == 3 and parts[0] == "wbm_pro_30d":
        try:
            activation_id = int(parts[1])
            amount = int(parts[2])
        except ValueError:
            return None
        if activation_id < 0 or amount <= 0:
            return None
        return activation_id, amount, _PLAN_PRO_CODE, 30

    if len(parts) != 5 or parts[0] != "wbm_sub":
        return None

    offer_code = _normalize_offer_code(parts[1])
    try:
        days = int(parts[2])
        activation_id = int(parts[3])
        amount = int(parts[4])
    except ValueError:
        return None
    if activation_id < 0 or amount <= 0 or days <= 0:
        return None
    return activation_id, amount, offer_code, days


def _parse_promo_create_payload(text: str) -> tuple[int, int] | None:
    parts = text.replace(",", " ").split()
    if len(parts) != 2:
        return None
    try:
        first = int(parts[0])
        second = int(parts[1])
    except ValueError:
        return None
    return first, second


def _promo_kind_text(kind: str) -> str:
    if kind == "pro_days":
        return tx.ADMIN_PROMO_KIND_PRO_DAYS
    if kind == "pro_discount":
        return tx.ADMIN_PROMO_KIND_DISCOUNT
    return tx.ADMIN_PROMO_KIND_UNKNOWN


def _promo_value_text(kind: str, value: int) -> str:
    if kind == "pro_days":
        return tx.ADMIN_PROMO_VALUE_DAYS.format(value=value)
    if kind == "pro_discount":
        return tx.ADMIN_PROMO_VALUE_PERCENT.format(value=value)
    return tx.ADMIN_PROMO_VALUE_RAW.format(value=value)


def _promo_list_item_text(promo: object) -> str:
    return tx.ADMIN_PROMO_LIST_ITEM.format(
        kind="🎁" if getattr(promo, "kind") == "pro_days" else "💸",
        value=_promo_value_text(getattr(promo, "kind"), int(getattr(promo, "value"))),
        expires=getattr(promo, "expires_at").strftime("%d.%m %H:%M"),
    )


async def _show_admin_promo_list(
    message: Message,
    *,
    session: AsyncSession,
    page: int,
) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    total = await count_active_promos(session, now=now)
    if total <= 0:
        await message.edit_text(
            tx.ADMIN_PROMO_DEACTIVATE_EMPTY,
            reply_markup=admin_promo_kb(),
        )
        return

    total_pages = (total + _ADMIN_PROMO_PAGE_SIZE - 1) // _ADMIN_PROMO_PAGE_SIZE
    safe_page = min(max(0, page), total_pages - 1)

    promos = await get_active_promos_page(
        session,
        now=now,
        limit=_ADMIN_PROMO_PAGE_SIZE,
        offset=safe_page * _ADMIN_PROMO_PAGE_SIZE,
    )
    items = [(promo.id, _promo_list_item_text(promo)) for promo in promos]

    await message.edit_text(
        tx.ADMIN_PROMO_DEACTIVATE_LIST,
        reply_markup=admin_promo_list_kb(
            items,
            page=safe_page,
            total_pages=total_pages,
        ),
    )


def _promo_card_text(*, promo: object, activations: int, bot_username: str) -> str:
    expires_at = getattr(promo, "expires_at")
    now = datetime.now(UTC).replace(tzinfo=None)
    status = (
        tx.ADMIN_PROMO_STATUS_ACTIVE
        if expires_at >= now and getattr(promo, "is_active")
        else tx.ADMIN_PROMO_STATUS_EXPIRED
    )
    link = f"https://t.me/{bot_username}?start=promo_{getattr(promo, 'code')}"
    return tx.ADMIN_PROMO_CARD.format(
        kind=_promo_kind_text(getattr(promo, "kind")),
        value=_promo_value_text(getattr(promo, "kind"), int(getattr(promo, "value"))),
        status=status,
        activations=activations,
        created=getattr(promo, "created_at").strftime("%d.%m.%Y %H:%M"),
        expires=expires_at.strftime("%d.%m.%Y %H:%M"),
        link=escape(link),
    )


def _format_review_insights_text(track_title: str, insights: ReviewInsights) -> str:
    return tx.review_insights_text(track_title, insights)


def _plan_offer_text(
    *,
    offer_code: str,
    cfg: "RuntimeConfigView",
    amount: int,
) -> str:
    plan_name = _plan_db_name_from_offer(offer_code)
    period = _feature_period(plan_name)
    return tx.PLAN_OFFER_TEXT.format(
        title=_plan_title(offer_code),
        days=_plan_days(offer_code),
        tracks_limit=_track_limit(plan_name),
        interval=cfg.pro_interval_min,
        cheap_period=_feature_period_phrase(period),
        reviews_period=_feature_period_phrase(period),
        cheap_limit=_feature_limit(plan_name, "cheap"),
        reviews_limit=_feature_limit(plan_name, "reviews"),
        card_amount=amount,
        stars_amount=amount,
        note=_plan_note(offer_code),
    )


async def _progress_spinner(
    message: Message,
    *,
    base_text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    hourglass_frames = ("⏳", "⌛️")
    dots_frames = (".", "..", "...")
    clean_base = base_text.rstrip(" .…")

    idx = 0
    while True:
        dots = dots_frames[idx % len(dots_frames)]
        hourglass = hourglass_frames[idx % len(hourglass_frames)]
        try:
            await message.edit_text(
                f"{clean_base}{dots} {hourglass}",
                reply_markup=reply_markup,
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                raise
        idx += 1
        await asyncio.sleep(1.1)


async def _stop_spinner(task: asyncio.Task[None] | None) -> None:
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


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
    cheap_limit = _feature_limit(user_plan, "cheap")
    reviews_limit = _feature_limit(user_plan, "reviews")
    period = _feature_period(user_plan)
    cheap_used = await FeatureUsageDailyRD.get_used(
        redis,
        tg_user_id=user_tg_id,
        feature="cheap",
        period=period,
        session=session,
    )
    reviews_used = await FeatureUsageDailyRD.get_used(
        redis,
        tg_user_id=user_tg_id,
        feature="reviews",
        period=period,
        session=session,
    )

    return paged_track_kb(
        track,
        page,
        total,
        confirm_remove=confirm_remove,
        cheap_btn_text=tx.button_with_usage(
            tx.BTN_FIND_CHEAPER,
            used=cheap_used,
            limit=cheap_limit,
        ),
        reviews_btn_text=tx.button_with_usage(
            tx.BTN_REVIEW_ANALYSIS,
            used=reviews_used,
            limit=reviews_limit,
        ),
    )


async def _search_wb_loose_alternatives(
    *,
    base_title: str,
    exclude_wb_item_id: int,
    max_price: Decimal | None,
    limit: int = 5,
) -> list[WbSimilarItemRD]:
    tokens = [t for t in re.split(r"\s+", base_title.strip()) if len(t) >= 3]
    if not tokens:
        return []
    query = " ".join(tokens[:5])
    url = (
        "https://search.wb.ru/exactmatch/ru/common/v14/search"
        f"?appType=1&curr=rub&dest=-1257786&query={quote_plus(query)}&resultset=catalog&page=1"
    )

    try:
        async with ClientSession(headers=WB_HTTP_HEADERS) as client:
            async with client.get(url, timeout=12, proxy=WB_HTTP_PROXY) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json(content_type=None)
    except Exception:
        return []

    products = (
        data.get("data", {}).get("products", []) if isinstance(data, dict) else []
    )
    out: list[WbSimilarItemRD] = []
    for product in products:
        if not isinstance(product, dict):
            continue
        nm_id = product.get("id") or product.get("nmId")
        if not isinstance(nm_id, int) or nm_id == exclude_wb_item_id:
            continue

        sale_u = product.get("salePriceU")
        if not isinstance(sale_u, (int, float)):
            continue
        price = Decimal(str(sale_u)) / Decimal("100")

        title = str(product.get("name") or product.get("title") or f"Item {nm_id}")
        url = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"

        out.append(
            WbSimilarItemRD(
                wb_item_id=nm_id,
                title=title,
                price=str(price),
                url=url,
            )
        )

    out.sort(key=lambda item: Decimal(item.price))
    if max_price is not None:
        cheaper = [item for item in out if Decimal(item.price) < max_price]
        if cheaper:
            return cheaper[:limit]
    return out[:limit]


class SettingsState(StatesGroup):
    waiting_for_targets = State()
    waiting_for_sizes = State()
    waiting_for_compare_items = State()
    waiting_for_pro_grant = State()
    waiting_for_free_interval = State()
    waiting_for_pro_interval = State()
    waiting_for_cheap_threshold = State()
    waiting_for_free_ai_limit = State()
    waiting_for_pro_ai_limit = State()
    waiting_for_review_sample_limit = State()
    waiting_for_analysis_model = State()
    waiting_for_promo_pro = State()
    waiting_for_promo_discount = State()


class SupportState(StatesGroup):
    waiting_for_message_or_media = State()  # Ожидание текста или фото
    waiting_for_media_confirmation = State()  # Подтверждение завершения добавления фото
    waiting_for_admin_reply = State()


@router.callback_query(F.data == "wbm:home:0")
async def wb_home_cb(cb: CallbackQuery, session: AsyncSession, redis: "Redis") -> None:
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    used = await count_user_tracks(session, user.id, active_only=True)
    cfg = runtime_config_view(await get_runtime_config(session))
    admin = is_admin(cb.from_user.id, se)
    await cb.message.edit_text(
        dashboard_text(
            user.plan,
            used,
            free_interval_min=cfg.free_interval_min,
            pro_interval_min=cfg.pro_interval_min,
        ),
        reply_markup=dashboard_kb(
            admin,
            show_compare=_can_use_compare(plan=user.plan, admin=admin),
        ),
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
        session,
        cb.from_user.id,
        cb.from_user.username,
        cb.from_user.first_name,
        cb.from_user.last_name,
    )
    admin = is_admin(cb.from_user.id, se)
    if not _can_use_compare(plan=user.plan, admin=admin):
        await cb.answer(tx.COMPARE_ACCESS_DENIED, show_alert=True)
        return

    await state.clear()
    await cb.message.edit_text(tx.COMPARE_MODE_PROMPT, reply_markup=_compare_mode_kb())


@router.callback_query(F.data.regexp(r"wbm:compare:mode:(cheap|quality|gift|safe)"))
async def wb_compare_mode_cb(
    cb: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    user = await get_or_create_monitor_user(
        session,
        cb.from_user.id,
        cb.from_user.username,
        cb.from_user.first_name,
        cb.from_user.last_name,
    )
    admin = is_admin(cb.from_user.id, se)
    if not _can_use_compare(plan=user.plan, admin=admin):
        await cb.answer(tx.COMPARE_ACCESS_DENIED, show_alert=True)
        return

    mode = cb.data.split(":")[-1]
    await state.update_data(compare_mode=mode)
    await state.set_state(SettingsState.waiting_for_compare_items)
    await cb.message.edit_text(tx.COMPARE_ITEMS_PROMPT, reply_markup=add_item_prompt_kb())


def _quick_item_kb(
    wb_item_id: int,
    *,
    already_tracked: bool = False,
    reviews_btn_text: str | None = None,
    search_btn_text: str | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if not already_tracked:
        rows.append(
            [
                InlineKeyboardButton(
                    text=tx.QUICK_ADD_BTN, callback_data=f"wbm:quick:add:{wb_item_id}"
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=reviews_btn_text or tx.QUICK_REVIEWS_BTN,
                callback_data=f"wbm:quick:reviews:{wb_item_id}",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=search_btn_text or tx.QUICK_SEARCH_BTN,
                callback_data=f"wbm:quick:search:{wb_item_id}",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _quick_item_kb_with_usage(
    *,
    session: AsyncSession,
    redis: "Redis",
    user_tg_id: int,
    user_plan: str,
    wb_item_id: int,
    already_tracked: bool,
) -> InlineKeyboardMarkup:
    period = _feature_period(user_plan)
    cheap_limit = _feature_limit(user_plan, "cheap")
    reviews_limit = _feature_limit(user_plan, "reviews")
    cheap_used = await FeatureUsageDailyRD.get_used(
        redis,
        tg_user_id=user_tg_id,
        feature="cheap",
        period=period,
        session=session,
    )
    reviews_used = await FeatureUsageDailyRD.get_used(
        redis,
        tg_user_id=user_tg_id,
        feature="reviews",
        period=period,
        session=session,
    )
    return _quick_item_kb(
        wb_item_id,
        already_tracked=already_tracked,
        reviews_btn_text=tx.button_with_usage(
            tx.QUICK_REVIEWS_BTN,
            used=reviews_used,
            limit=reviews_limit,
        ),
        search_btn_text=tx.button_with_usage(
            tx.QUICK_SEARCH_BTN,
            used=cheap_used,
            limit=cheap_limit,
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
        tx.TRACK_ADDED_RATING_WITH_REVIEWS.format(
            rating=rating,
            reviews=reviews or 0,
        )
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


def _quick_search_mode_kb(wb_item_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=tx.SEARCH_MODE_CHEAPER_BTN,
                    callback_data=f"wbm:quick:searchmode:cheap:{wb_item_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=tx.SEARCH_MODE_SIMILAR_BTN,
                    callback_data=f"wbm:quick:searchmode:similar:{wb_item_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=tx.BTN_BACK, callback_data=f"wbm:quick:preview:{wb_item_id}"
                )
            ],
        ]
    )


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
        if not nm_id:
            continue
        if nm_id in wb_ids:
            continue
        wb_ids.append(nm_id)

    if len(wb_ids) > 5:
        await msg.answer(tx.COMPARE_ITEMS_TOO_MANY)
        return

    if len(wb_ids) < 2:
        await msg.answer(tx.COMPARE_ITEMS_NOT_ENOUGH)
        return

    user = await get_or_create_monitor_user(
        session,
        msg.from_user.id,
        msg.from_user.username,
        msg.from_user.first_name,
        msg.from_user.last_name,
        redis=redis,
    )
    admin = is_admin(msg.from_user.id, se)

    # Check daily limit for non-admins
    if not admin:
        ok, _ = await FeatureUsageDailyRD.try_consume(
            redis,
            tg_user_id=user.tg_user_id,
            feature="compare",
            limit=_COMPARE_DAILY_LIMIT,
            period="day",
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
        session,
        msg.from_user.id,
        msg.from_user.username,
        msg.from_user.first_name,
        msg.from_user.last_name,
        redis=redis,
    )

    data = await state.get_data()
    compare_mode = str(data.get("compare_mode") or "balanced")
    history = await get_price_history_stats(session, [p.wb_item_id for p in products], days=30)

    try:
        result = await compare_products_with_llm(
            products=products,
            mode=compare_mode,
            api_key=se.agentplatform_api_key,
            model=se.agentplatform_compare_model,
            api_base_url=se.agentplatform_base_url,
            price_history=history,
        )
    except Exception:
        logger.exception("Compare products failed")
        # Refund the limit on error for non-admins
        if not admin:
            try:
                await FeatureUsageDailyRD.try_consume(
                    redis,
                    tg_user_id=user.tg_user_id,
                    feature="compare",
                    limit=9999,
                    period="day",
                )
            except Exception:
                pass
        await msg.answer(tx.COMPARE_ITEMS_FAILED)
        return

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
        extra = ""
        if s:
            extra = f" | оценка {s.overall}/100"
        ranking_lines.append(
            f"{idx}. <a href='https://www.wildberries.ru/catalog/{nm_id}/detail.aspx'>{escape(p.title)}</a> — {price}, ⭐ {rating}{extra}"
        )

    winner_score = score_by_id.get(winner.wb_item_id)
    winner_price = f"{winner.price}₽" if winner.price is not None else "—"
    winner_rating = f"{winner.rating}" if winner.rating is not None else "—"

    score_block = ""
    if winner_score:
        score_block = f"📊 <b>Итоговая оценка:</b> {winner_score.overall}/100\n"

    def _replace_ids_with_titles(src: str) -> str:
        out = src
        for p in products:
            out = re.sub(rf"\b{p.wb_item_id}\b", f"«{p.title}»", out)
        return out

    def _humanize_text(src: str) -> str:
        out = src
        replacements = {
            "overall": "итоговая оценка",
            "score": "оценка",
            "target_price": "ориентир по цене",
            "risk": "риск",
            "trust": "надежность",
            "availability": "наличие",
            "value": "ценность",
        }
        for en, ru in replacements.items():
            out = re.sub(rf"\b{en}\b", ru, out, flags=re.IGNORECASE)
        return out

    clean_reason = _humanize_text(_replace_ids_with_titles(result.reason))
    clean_risks = [_humanize_text(_replace_ids_with_titles(r)) for r in (result.risks or [])]
    clean_wait_tip = (
        _humanize_text(_replace_ids_with_titles(result.wait_tip)) if result.wait_tip else None
    )

    risks_block = ""
    if clean_risks:
        risks_block = "\n" + "\n".join([f"• {escape(r)}" for r in clean_risks[:3]])

    wait_tip_block = ""
    if clean_wait_tip:
        normalized_wait = clean_wait_tip.strip().lower()
        if normalized_wait not in {"нет", "no", "none", "n/a", "-", "—"}:
            wait_tip_block = f"\n💡 <b>Рекомендация по цене:</b> {escape(clean_wait_tip)}"

    text = (
        "⚖️ <b>Сравнение товаров</b>\n\n"
        f"🏆 <b>Лучший выбор:</b> <a href='https://www.wildberries.ru/catalog/{winner.wb_item_id}/detail.aspx'>{escape(winner.title)}</a>\n"
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
            session,
            user_id=user.id,
            mode=compare_mode,
            input_item_ids=[p.wb_item_id for p in products],
            winner_item_id=winner.wb_item_id,
            result_json={
                "reason": result.reason,
                "ranking": result.ranking,
                "risks": result.risks,
                "wait_tip": result.wait_tip,
                "scores": [
                    {
                        "id": s.wb_item_id,
                        "overall": s.overall,
                        "value": s.value,
                        "trust": s.trust,
                        "risk": s.risk,
                        "availability": s.availability,
                        "target_price": s.target_price,
                    }
                    for s in result.scores
                ],
            },
        )
        await session.commit()
    except Exception:
        logger.exception("Failed to save compare run")

    await state.clear()
    await msg.answer(text, link_preview_options=LinkPreviewOptions(is_disabled=True))


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

    product = await fetch_product(redis, wb_item_id)
    if not product:
        await msg.answer(tx.PRODUCT_FETCH_ERROR)
        return

    text = _quick_preview_text(product=product, already_tracked=bool(existing))

    await msg.answer(
        text,
        reply_markup=await _quick_item_kb_with_usage(
            session=session,
            redis=redis,
            user_tg_id=msg.from_user.id,
            user_plan=user.plan,
            wb_item_id=wb_item_id,
            already_tracked=bool(existing),
        ),
    )


@router.callback_query(F.data.regexp(r"wbm:quick:preview:(\d+)"))
async def wb_quick_preview_cb(
    cb: CallbackQuery,
    session: AsyncSession,
    redis: "Redis",
) -> None:
    wb_item_id = int(cb.data.split(":")[3])
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


@router.callback_query(F.data.regexp(r"wbm:quick:add:(\d+)"))
async def wb_quick_add_cb(
    cb: CallbackQuery,
    session: AsyncSession,
    redis: "Redis",
) -> None:
    wb_item_id = int(cb.data.split(":")[3])
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
    page = 0
    for idx, t in enumerate(tracks):
        if t.id == track.id:
            page = idx
            break

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


@router.callback_query(F.data.regexp(r"wbm:quick:reviews:(\d+)"))
async def wb_quick_reviews_cb(
    cb: CallbackQuery,
    session: AsyncSession,
    redis: "Redis",
) -> None:
    wb_item_id = int(cb.data.split(":")[3])

    product = await fetch_product(redis, wb_item_id, use_cache=False)
    if not product:
        await cb.answer(tx.PRODUCT_FETCH_ERROR, show_alert=True)
        return

    back_preview_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=tx.BTN_BACK, callback_data=f"wbm:quick:preview:{wb_item_id}"
                )
            ],
        ]
    )

    await cb.answer()
    await cb.message.edit_text(
        tx.REVIEWS_ANALYSIS_PROGRESS.format(title=escape(product.title)),
        reply_markup=back_preview_kb,
    )
    logger.info("quick-reviews started: user_id=%s wb_item_id=%s", cb.from_user.id, wb_item_id)

    review_limit = 50
    model = se.agentplatform_model.strip()
    model_signature = _model_signature(model, review_limit)

    cached = await QuickReviewInsightsCacheRD.get(
        redis,
        wb_item_id=wb_item_id,
        model_signature=model_signature,
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
            session,
            cb.from_user.id,
            cb.from_user.username,
        )
        period = _feature_period(user.plan)
        period_title = _feature_period_title(period)
        feature_limit = _feature_limit(user.plan, "reviews")
        allowed, _used = await FeatureUsageDailyRD.try_consume(
            redis,
            tg_user_id=cb.from_user.id,
            feature="reviews",
            limit=feature_limit,
            period=period,
            session=session,
        )
        if not allowed:
            await cb.message.edit_text(
                tx.FEATURE_LIMIT_REVIEWS_REACHED.format(
                    limit=feature_limit,
                    period=period_title,
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
            logger.warning(
                "quick-reviews failed (known): user_id=%s wb_item_id=%s err=%s",
                cb.from_user.id,
                wb_item_id,
                type(exc).__name__,
            )
            await cb.message.edit_text(str(exc), reply_markup=back_preview_kb)
            return
        except Exception as exc:
            logger.exception(
                "quick-reviews failed (unexpected): user_id=%s wb_item_id=%s",
                cb.from_user.id,
                wb_item_id,
            )
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

    await cb.message.edit_text(
        _format_review_insights_text(product.title, insights),
        reply_markup=back_preview_kb,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
    logger.info("quick-reviews done: user_id=%s wb_item_id=%s", cb.from_user.id, wb_item_id)


@router.callback_query(F.data.regexp(r"wbm:quick:search:(\d+)"))
async def wb_quick_search_cb(
    cb: CallbackQuery,
    session: AsyncSession,
    redis: "Redis",
) -> None:
    wb_item_id = int(cb.data.split(":")[3])
    product = await fetch_product(redis, wb_item_id)
    if not product:
        await cb.answer(tx.PRODUCT_FETCH_ERROR, show_alert=True)
        return

    await cb.answer()
    await cb.message.edit_text(
        tx.SEARCH_MODE_PROMPT,
        reply_markup=_quick_search_mode_kb(wb_item_id),
    )


@router.callback_query(F.data.regexp(r"wbm:quick:searchmode:(cheap|similar):(\d+)"))
async def wb_quick_searchmode_cb(
    cb: CallbackQuery,
    session: AsyncSession,
    redis: "Redis",
) -> None:
    mode = cb.data.split(":")[3]
    wb_item_id = int(cb.data.split(":")[4])

    product = await fetch_product(redis, wb_item_id, use_cache=False)
    if not product or product.price is None:
        await cb.answer(tx.PRODUCT_FETCH_ERROR, show_alert=True)
        return

    back_quick_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=tx.BTN_BACK, callback_data=f"wbm:quick:search:{wb_item_id}"
                )
            ],
        ]
    )

    cached_search = await QuickSimilarSearchCacheRD.get(
        redis, wb_item_id=wb_item_id, mode=mode
    )
    alternatives: list[WbSimilarItemRD] = []
    current_price_text = str(product.price)
    if cached_search is not None and (
        mode != "cheap" or cached_search.base_price == current_price_text
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
            session,
            cb.from_user.id,
            cb.from_user.username,
        )
        period = _feature_period(user.plan)
        period_title = _feature_period_title(period)
        feature_limit = _feature_limit(user.plan, "cheap")
        allowed, _used = await FeatureUsageDailyRD.try_consume(
            redis,
            tg_user_id=cb.from_user.id,
            feature="cheap",
            limit=feature_limit,
            period=period,
            session=session,
        )
        if not allowed:
            await cb.answer(
                tx.FEATURE_LIMIT_CHEAP_REACHED.format(
                    limit=feature_limit,
                    period=period_title,
                ),
                show_alert=True,
            )
            return

        progress_text = (
            tx.FIND_CHEAPER_PROGRESS.format(title=escape(product.title))
            if mode == "cheap"
            else tx.FIND_SIMILAR_PROGRESS.format(title=escape(product.title))
        )
        await cb.message.edit_text(
            progress_text,
            reply_markup=back_quick_kb,
        )

        found = await search_similar_cheaper_title_only(
            base_title=product.title,
            base_brand=product.brand,
            base_subject_id=product.subject_id,
            match_percent_threshold=None,
            max_price=product.price if mode == "cheap" else Decimal("99999999"),
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
            require_cheaper=(mode == "cheap"),
            limit=10,
            log_prefix=f"quick_id={wb_item_id} mode={mode} stage=search",
        )
        if mode == "cheap" and not live_confirmed:
            # Fallback: если по строгому совпадению цвета пусто — пробуем без цветового фильтра.
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
                log_prefix=f"quick_id={wb_item_id} mode={mode} stage=search_color_relaxed",
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
            mode=mode,
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
            if mode == "cheap"
            else tx.FIND_SIMILAR_EMPTY.format(title=escape(product.title))
        )
        await cb.message.edit_text(text, reply_markup=back_quick_kb)
        return

    alternatives = sorted(
        alternatives,
        key=lambda x: (
            0 if _is_same_brand(getattr(product, "brand", None), getattr(x, "brand", None)) else 1,
            Decimal(str(x.price)),
        ),
    )
    header = (
        tx.FIND_CHEAPER_HEADER.format(
            price=current_price_text, title=escape(product.title)
        )
        if mode == "cheap"
        else tx.FIND_SIMILAR_HEADER.format(title=escape(product.title))
    )
    lines = [header, ""]
    if color_relaxed:
        lines.append(
            "ℹ️ Для расширения выдачи ослабил фильтр по цвету (остальные проверки сохранены)."
        )
        lines.append("")

    base_brand = _normalize_brand(getattr(product, "brand", None))
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


def _page_picker_kb(
    *,
    total: int,
    track_id: int,
    current_page: int,
    offset: int = 0,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    per_row = 5
    max_buttons = 25

    safe_total = max(1, total)
    safe_offset = max(0, min(offset, max(0, safe_total - 1)))
    end = min(safe_total, safe_offset + max_buttons)

    page_buttons: list[InlineKeyboardButton] = []
    for i in range(safe_offset, end):
        label = f"[{i + 1}]" if i == current_page else str(i + 1)
        page_buttons.append(
            InlineKeyboardButton(text=label, callback_data=f"wbm:page:{i}")
        )
        if len(page_buttons) >= per_row:
            rows.append(page_buttons)
            page_buttons = []
    if page_buttons:
        rows.append(page_buttons)

    if safe_total > max_buttons:
        nav_row: list[InlineKeyboardButton] = []
        if safe_offset > 0:
            prev_offset = max(0, safe_offset - max_buttons)
            nav_row.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"wbm:pagepick:{track_id}:{current_page}:{prev_offset}",
                )
            )
        nav_row.append(
            InlineKeyboardButton(
                text=f"{safe_offset + 1}-{end} / {safe_total}",
                callback_data="wbm:noop:0",
            )
        )
        if end < safe_total:
            nav_row.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=f"wbm:pagepick:{track_id}:{current_page}:{end}",
                )
            )
        rows.append(nav_row)

    rows.append(
        [
            InlineKeyboardButton(
                text=tx.BTN_BACK,
                callback_data=f"wbm:pagepickcancel:{track_id}:{current_page}",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.regexp(r"wbm:pagepick:(\d+):(\d+)(?::(\d+))?"))
async def wb_page_pick_cb(cb: CallbackQuery, session: AsyncSession) -> None:
    parts = cb.data.split(":")
    track_id = int(parts[2])
    current_page = int(parts[3])
    offset = int(parts[4]) if len(parts) > 4 else 0

    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    tracks = await get_user_tracks(session, user.id)
    if not tracks:
        await cb.answer(tx.NO_ACTIVE_TRACKS, show_alert=True)
        return

    if current_page < 0 or current_page >= len(tracks):
        current_page = 0

    # Ensure callback refers to an actual user track to avoid cross-user jumps.
    if all(t.id != track_id for t in tracks):
        track_id = tracks[current_page].id

    await cb.answer()
    await cb.message.edit_reply_markup(
        reply_markup=_page_picker_kb(
            total=len(tracks),
            track_id=track_id,
            current_page=current_page,
            offset=offset,
        )
    )


@router.callback_query(F.data.regexp(r"wbm:pagepickcancel:(\d+):(\d+)"))
async def wb_page_pick_cancel_cb(
    cb: CallbackQuery,
    session: AsyncSession,
    redis: "Redis",
) -> None:
    parts = cb.data.split(":")
    track_id = int(parts[2])
    current_page = int(parts[3])

    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    tracks = await get_user_tracks(session, user.id)
    if not tracks:
        await cb.answer(tx.NO_ACTIVE_TRACKS, show_alert=True)
        return

    page = current_page
    if page < 0 or page >= len(tracks):
        page = 0

    track = tracks[page]
    if track.id != track_id:
        for idx, t in enumerate(tracks):
            if t.id == track_id:
                track = t
                page = idx
                break

    await cb.answer()
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
async def wb_remove_yes_cb(
    cb: CallbackQuery,
    session: AsyncSession,
    redis: "Redis",
) -> None:
    track_id = int(cb.data.split(":")[2])

    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    tracks_before = await get_user_tracks(session, user.id)
    removed_index = 0
    for idx, t in enumerate(tracks_before):
        if t.id == track_id:
            removed_index = idx
            break

    await delete_track(session, track_id)
    await session.commit()

    tracks_after = await get_user_tracks(session, user.id)
    if tracks_after:
        target_idx = min(removed_index, len(tracks_after) - 1)
        track = tracks_after[target_idx]
        await cb.message.edit_text(
            format_track_text(track),
            reply_markup=await _track_kb_with_usage(
                session=session,
                redis=redis,
                user_tg_id=cb.from_user.id,
                user_plan=user.plan,
                track=track,
                page=target_idx,
                total=len(tracks_after),
            ),
        )
        await cb.answer(tx.TRACK_DELETED)
        return

    cfg = runtime_config_view(await get_runtime_config(session))
    used = await count_user_tracks(session, user.id, active_only=True)
    admin = is_admin(cb.from_user.id, se)
    await cb.message.edit_text(
        dashboard_text(
            user.plan,
            used,
            free_interval_min=cfg.free_interval_min,
            pro_interval_min=cfg.pro_interval_min,
        ),
        reply_markup=dashboard_kb(
            admin,
            show_compare=_can_use_compare(plan=user.plan, admin=admin),
        ),
    )
    await cb.answer(tx.TRACK_DELETED)


@router.callback_query(F.data.regexp(r"wbm:cheap:(\d+)"))
async def wb_search_mode_cb(cb: CallbackQuery, session: AsyncSession) -> None:
    track_id = int(cb.data.split(":")[2])
    track = await get_user_track_by_id(session, track_id)
    if not track:
        await cb.answer(tx.TRACK_NOT_FOUND, show_alert=True)
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=tx.SEARCH_MODE_CHEAPER_BTN,
                    callback_data=f"wbm:cheapmode:cheap:{track.id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=tx.SEARCH_MODE_SIMILAR_BTN,
                    callback_data=f"wbm:cheapmode:similar:{track.id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=tx.FIND_CHEAPER_TO_LIST_BTN,
                    callback_data=f"wbm:back:{track.id}",
                )
            ],
        ]
    )
    await cb.answer()
    await cb.message.edit_text(
        tx.SEARCH_MODE_PROMPT,
        reply_markup=kb,
    )


@router.callback_query(F.data.regexp(r"wbm:cheapmode:(cheap|similar):(\d+)"))
async def wb_find_cheaper_cb(
    cb: CallbackQuery, session: AsyncSession, redis: "Redis"
) -> None:
    mode = cb.data.split(":")[2]
    track_id = int(cb.data.split(":")[3])
    track = await get_user_track_by_id(session, track_id)
    if not track:
        await cb.answer(tx.TRACK_NOT_FOUND, show_alert=True)
        return

    back_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=tx.BTN_BACK,
                    callback_data=f"wbm:cheap:{track.id}",
                )
            ]
        ]
    )

    cfg = runtime_config_view(await get_runtime_config(session))
    color_relaxed = False
    # Кэшируем одинаково для обоих режимов (cheap/similar)
    use_cache = True
    cached = await WbSimilarSearchCacheRD.get(redis, track.id, mode=mode) if use_cache else None
    if cached is None or cached.match_percent != cfg.cheap_match_percent:
        user = await get_or_create_monitor_user(
            session,
            cb.from_user.id,
            cb.from_user.username,
        )
        period = _feature_period(user.plan)
        period_title = _feature_period_title(period)
        feature_limit = _feature_limit(user.plan, "cheap")
        allowed, _used = await FeatureUsageDailyRD.try_consume(
            redis,
            tg_user_id=cb.from_user.id,
            feature="cheap",
            limit=feature_limit,
            period=period,
            session=session,
        )
        if not allowed:
            await cb.answer(
                tx.FEATURE_LIMIT_CHEAP_REACHED.format(
                    limit=feature_limit,
                    period=period_title,
                ),
                show_alert=True,
            )
            return

        event_name = "cheap_scan" if mode == "cheap" else "similar_scan"
        await log_event(
            session,
            track.id,
            event_name,
            f"{mode}:{track.id}:{cb.from_user.id}:{datetime.now(UTC).timestamp()}",
        )
        await session.commit()

        await cb.answer(tx.FIND_CHEAPER_ANSWER)
        progress_text = (
            tx.FIND_CHEAPER_PROGRESS.format(title=escape(track.title))
            if mode == "cheap"
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
                    tx.FIND_CHEAPER_PRICE_ERROR,
                    reply_markup=back_kb,
                )
                return

            if _WB_ENABLE_SELENIUM_SIMILAR:
                try:
                    selenium_items = await asyncio.to_thread(
                        fetch_similar_products,
                        track.wb_item_id,
                        limit=40,
                        timeout_sec=20.0,
                        headless=True,
                    )
                except Exception:
                    logger.exception(
                        "Selenium similar parser failed (track_id=%s, wb_item_id=%s)",
                        track.id,
                        track.wb_item_id,
                    )
                    selenium_items = []
            else:
                selenium_items = []

            reranked: list[WbSimilarItemRD] = []
            if selenium_items:
                priced = [
                    item
                    for item in selenium_items
                    if item.nm_id != track.wb_item_id and item.final_price is not None
                ]
                priced.sort(key=lambda item: item.final_price)

                cheaper = [item for item in priced if item.final_price < current.price]
                selected = (
                    (cheaper[:10] if cheaper else priced[:10])
                    if mode == "cheap"
                    else priced[:10]
                )
                reranked = [
                    WbSimilarItemRD(
                        wb_item_id=item.nm_id,
                        title=item.title,
                        price=str(item.final_price),
                        url=item.product_url,
                    )
                    for item in selected
                ]

            if not reranked:
                found = await search_similar_cheaper_title_only(
                    base_title=current.title or track.title,
                    base_brand=current.brand,
                    base_subject_id=current.subject_id,
                    match_percent_threshold=cfg.cheap_match_percent,
                    max_price=current.price if mode == "cheap" else Decimal("99999999"),
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
                        require_cheaper=(mode == "cheap"),
                        limit=20,
                        log_prefix=f"track_id={track.id} mode={mode} stage=search",
                    )
                    if mode == "cheap" and not live_confirmed:
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
                            log_prefix=f"track_id={track.id} mode={mode} stage=search_color_relaxed",
                        )
                    if mode == "similar" and len(live_confirmed) < 3:
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
                            log_prefix=f"track_id={track.id} mode={mode} stage=search_relaxed",
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
                                wb_item_id=item.wb_item_id,
                                title=item.title,
                                price=str(item.price),
                                url=item.url,
                            )
                            for item in llm_ranked[:10]
                        ]
                    elif live_confirmed:
                        reranked = [
                            WbSimilarItemRD(
                                wb_item_id=item.wb_item_id,
                                title=item.title,
                                price=str(item.price),
                                url=item.url,
                            )
                            for item in live_confirmed[:10]
                        ]

            if not reranked:
                reranked = await _search_wb_loose_alternatives(
                    base_title=current.title or track.title,
                    exclude_wb_item_id=track.wb_item_id,
                    max_price=current.price if mode == "cheap" else None,
                    limit=8,
                )

            if reranked:
                live_input = [
                    WbSimilarProduct(
                        wb_item_id=item.wb_item_id,
                        title=item.title,
                        price=Decimal(str(item.price)),
                        url=item.url,
                    )
                    for item in reranked
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
                    require_cheaper=(mode == "cheap"),
                    limit=10,
                    log_prefix=f"track_id={track.id} mode={mode} stage=final",
                )
                if mode == "cheap" and not live_confirmed:
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
                        log_prefix=f"track_id={track.id} mode={mode} stage=final_color_relaxed",
                    )
                if mode == "similar" and len(live_confirmed) < 3:
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
                        log_prefix=f"track_id={track.id} mode={mode} stage=final_relaxed",
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
                        wb_item_id=item.wb_item_id,
                        title=item.title,
                        price=str(item.price),
                        url=item.url,
                    )
                    for item in live_confirmed[:10]
                ]
        finally:
            await _stop_spinner(spinner_task)

        alternatives = reranked
        current_price_text = str(current.price)
        if use_cache:
            await WbSimilarSearchCacheRD(
                track_id=track.id,
                mode=mode,
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
            if mode == "cheap"
            else tx.FIND_SIMILAR_EMPTY.format(title=escape(track.title))
        )
        await cb.message.edit_text(
            empty_text,
            reply_markup=back_kb,
        )
        return

    lines = [
        (
            tx.FIND_CHEAPER_HEADER.format(
                price=current_price_text, title=escape(track.title)
            )
            if mode == "cheap"
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

    if mode == "cheap" and current_price_decimal is not None:
        has_cheaper = False
        for item in alternatives:
            try:
                if Decimal(str(item.price)) < current_price_decimal:
                    has_cheaper = True
                    break
            except (InvalidOperation, TypeError):
                continue
        if not has_cheaper:
            lines.append("ℹ️ Дешевле не нашлось — показываю ближайшие похожие по цене.")
            lines.append("")

    def _price_sort_key(item: WbSimilarItemRD) -> Decimal:
        try:
            return Decimal(str(item.price))
        except (InvalidOperation, TypeError):
            return Decimal("999999999")

    base_brand = _normalize_brand(
        (current.brand if "current" in locals() and current else None)
    )

    alternatives = sorted(
        alternatives,
        key=lambda item: (
            0 if _is_same_brand(base_brand, item.brand) else 1,
            _price_sort_key(item),
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
            period = _feature_period(user.plan)
            period_title = _feature_period_title(period)
            feature_limit = _feature_limit(user.plan, "reviews")
            allowed, _used = await FeatureUsageDailyRD.try_consume(
                redis,
                tg_user_id=cb.from_user.id,
                feature="reviews",
                limit=feature_limit,
                period=period,
            )
            if not allowed:
                await cb.answer(
                    tx.FEATURE_LIMIT_REVIEWS_REACHED.format(
                        limit=feature_limit,
                        period=period_title,
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
            progress_text = tx.REVIEWS_ANALYSIS_PROGRESS.format(
                title=escape(track.title)
            )
            spinner_task = asyncio.create_task(
                _progress_spinner(
                    cb.message, base_text=progress_text, reply_markup=back_kb
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
            has_sizes=bool(track.last_sizes),
            pro_plan=_is_paid_plan(user.plan),
            qty_on=track.watch_qty,
            stock_on=track.watch_stock,
            price_fluctuation_on=track.watch_price_fluctuation,
        ),
    )


@router.callback_query(F.data == "wbm:plan:0")
async def wb_plan_cb(
    cb: CallbackQuery,
    session: AsyncSession,
    redis: "Redis",
) -> None:
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    has_active_subscription = _has_active_subscription(user, now=now)
    cfg = runtime_config_view(await get_runtime_config(session))
    tracks_used = await count_user_tracks(session, user.id, active_only=True)
    tracks_limit = _track_limit(user.plan)
    interval = (
        cfg.pro_interval_min if _is_paid_plan(user.plan) else cfg.free_interval_min
    )
    cheap_period = _feature_period(user.plan)
    reviews_period = _feature_period(user.plan)
    cheap_limit = _feature_limit(user.plan, "cheap")
    reviews_limit = _feature_limit(user.plan, "reviews")
    cheap_used = await FeatureUsageDailyRD.get_used(
        redis,
        tg_user_id=cb.from_user.id,
        feature="cheap",
        period=cheap_period,
        session=session,
    )
    reviews_used = await FeatureUsageDailyRD.get_used(
        redis,
        tg_user_id=cb.from_user.id,
        feature="reviews",
        period=reviews_period,
        session=session,
    )
    cheap_left = max(0, cheap_limit - cheap_used)
    reviews_left = max(0, reviews_limit - reviews_used)
    if user.plan == _PLAN_DB_PRO_PLUS:
        plan_label = tx.PLAN_BADGE_PRO_PLUS
    elif user.plan == _PLAN_DB_PRO:
        plan_label = tx.PLAN_BADGE_PRO
    else:
        plan_label = tx.PLAN_BADGE_FREE

    text = tx.PLAN_TEXT.format(
        plan=plan_label,
        tracks_limit=tracks_limit,
        tracks_used=tracks_used,
        interval=interval,
        cheap_period=_feature_period_phrase(cheap_period),
        cheap_limit=cheap_limit,
        cheap_left=cheap_left,
        reviews_period=_feature_period_phrase(reviews_period),
        reviews_limit=reviews_limit,
        reviews_left=reviews_left,
    )
    if _is_paid_plan(user.plan) and user.pro_expires_at:
        text += tx.PLAN_EXPIRES_LINE.format(
            expires=user.pro_expires_at.strftime("%d.%m.%Y")
        )
    if not has_active_subscription:
        text += tx.PLAN_SELECT_PROMPT

    await cb.answer()
    await cb.message.edit_text(
        text,
        reply_markup=plan_overview_kb(
            show_purchase_buttons=not has_active_subscription
        ),
    )


@router.callback_query(F.data.regexp(r"wbm:plan:offer:(pro|proplus)"))
async def wb_plan_offer_cb(cb: CallbackQuery, session: AsyncSession) -> None:
    offer_code = _normalize_offer_code(cb.data.split(":")[3])
    user = await get_or_create_monitor_user(
        session,
        cb.from_user.id,
        cb.from_user.username,
    )

    now = datetime.now(UTC).replace(tzinfo=None)
    if _has_active_subscription(user, now=now):
        await cb.answer(tx.PLAN_ALREADY_ACTIVE, show_alert=True)
        return

    discount = await get_user_active_discount(session, user_id=user.id, now=now)
    amount = _discounted_amount(_plan_base_amount(offer_code), discount)
    cfg = runtime_config_view(await get_runtime_config(session))

    await cb.answer()
    await cb.message.edit_text(
        _plan_offer_text(offer_code=offer_code, cfg=cfg, amount=amount),
        reply_markup=plan_offer_kb(
            offer_code=offer_code,
            card_amount=amount,
            stars_amount=amount,
            discount=discount,
        ),
    )


@router.callback_query(F.data == "wbm:pay:choice")
async def wb_pay_choice_cb(cb: CallbackQuery, session: AsyncSession) -> None:
    """Совместимость со старыми кнопками оплаты: открываем PRO карточку."""
    offer_code = _PLAN_PRO_CODE
    user = await get_or_create_monitor_user(
        session,
        cb.from_user.id,
        cb.from_user.username,
    )

    now = datetime.now(UTC).replace(tzinfo=None)
    if _has_active_subscription(user, now=now):
        await cb.answer(tx.PLAN_ALREADY_ACTIVE, show_alert=True)
        return

    discount = await get_user_active_discount(session, user_id=user.id, now=now)
    amount = _discounted_amount(_plan_base_amount(offer_code), discount)
    cfg = runtime_config_view(await get_runtime_config(session))

    await cb.answer()
    await cb.message.edit_text(
        _plan_offer_text(offer_code=offer_code, cfg=cfg, amount=amount),
        reply_markup=plan_offer_kb(
            offer_code=offer_code,
            card_amount=amount,
            stars_amount=amount,
            discount=discount,
        ),
    )


@router.callback_query(F.data.regexp(r"wbm:pay:card(?::(pro|proplus))?$"))
async def wb_pay_card_cb(cb: CallbackQuery, session: AsyncSession) -> None:
    """Оплата картой через Telegram Payments."""
    from aiogram.types import LabeledPrice

    parts = cb.data.split(":")
    offer_code = _normalize_offer_code(parts[3] if len(parts) > 3 else None)

    if not se.provider_token:
        await cb.answer("❌ Оплата картой временно недоступна", show_alert=True)
        return

    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    if _has_active_subscription(user, now=now):
        await cb.answer(tx.PLAN_ALREADY_ACTIVE, show_alert=True)
        return

    discount = await get_user_active_discount(session, user_id=user.id, now=now)
    days = _plan_days(offer_code)
    amount_rub = _discounted_amount(_plan_base_amount(offer_code), discount)

    payload = _build_payment_payload(
        offer_code=offer_code,
        days=days,
        amount=amount_rub,
        discount_activation_id=(discount.activation_id if discount else None),
    )

    description = tx.PAYMENT_CARD_DESCRIPTION_BY_PLAN.format(
        plan=_plan_title(offer_code),
        days=days,
        amount=amount_rub,
    )
    label = f"{_plan_title(offer_code)} ({days} дн.)"

    await cb.message.answer_invoice(
        title=f"WB Monitor {_plan_title(offer_code)}",
        description=description,
        payload=payload,
        currency="RUB",
        prices=[LabeledPrice(label=label, amount=amount_rub * 100)],  # в копейках
        provider_token=se.provider_token,
    )


@router.callback_query(F.data.regexp(r"wbm:pay:stars(?::(pro|proplus))?$"))
async def wb_pay_stars_cb(cb: CallbackQuery, session: AsyncSession) -> None:
    from aiogram.types import LabeledPrice

    parts = cb.data.split(":")
    offer_code = _normalize_offer_code(parts[3] if len(parts) > 3 else None)

    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    if _has_active_subscription(user, now=now):
        await cb.answer(tx.PLAN_ALREADY_ACTIVE, show_alert=True)
        return

    discount = await get_user_active_discount(session, user_id=user.id, now=now)
    days = _plan_days(offer_code)
    amount = _discounted_amount(_plan_base_amount(offer_code), discount)

    payload = _build_payment_payload(
        offer_code=offer_code,
        days=days,
        amount=amount,
        discount_activation_id=(discount.activation_id if discount else None),
    )
    label = f"{_plan_title(offer_code)} ({days} дн.)"
    if discount:
        label = tx.BTN_PAY_PRO_DISCOUNT.format(amount=amount, percent=discount.percent)

    await cb.message.answer_invoice(
        title=f"WB Monitor {_plan_title(offer_code)}",
        description=tx.PAYMENT_STARS_DESCRIPTION_BY_PLAN.format(
            plan=_plan_title(offer_code),
            days=days,
        ),
        payload=payload,
        currency="XTR",
        prices=[LabeledPrice(label=label, amount=amount)],
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
    parsed_payload = _parse_payment_payload(payment.invoice_payload)
    paid_days = parsed_payload[3] if parsed_payload is not None else 30
    paid_offer_code = (
        parsed_payload[2] if parsed_payload is not None else _PLAN_PRO_CODE
    )
    paid_plan = _plan_db_name_from_offer(paid_offer_code)

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
    user.plan = paid_plan
    user.pro_expires_at = base_expiry + timedelta(days=paid_days)
    await set_user_tracks_interval(session, user.id, cfg.pro_interval_min)

    if parsed_payload is not None:
        discount_activation_id, _amount, _offer_code, _days = parsed_payload
        if discount_activation_id > 0:
            await mark_discount_activation_consumed(
                session,
                activation_id=discount_activation_id,
                now=now,
            )

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

    text = tx.PRO_ACTIVATED_DAYS.format(days=paid_days)
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


@router.callback_query(F.data == "wbm:admin:promo")
async def wb_admin_promo_cb(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return

    await state.clear()
    await cb.message.edit_text(tx.ADMIN_PROMO_MENU_TEXT, reply_markup=admin_promo_kb())


@router.callback_query(F.data == "wbm:admin:promo:pro")
async def wb_admin_promo_pro_cb(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return

    await state.set_state(SettingsState.waiting_for_promo_pro)
    await cb.message.edit_text(
        tx.ADMIN_PROMO_PRO_PROMPT,
        reply_markup=admin_promo_input_kb(),
    )


@router.callback_query(F.data == "wbm:admin:promo:discount")
async def wb_admin_promo_discount_cb(cb: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return

    await state.set_state(SettingsState.waiting_for_promo_discount)
    await cb.message.edit_text(
        tx.ADMIN_PROMO_DISCOUNT_PROMPT,
        reply_markup=admin_promo_input_kb(),
    )


@router.callback_query(F.data == "wbm:admin:promo:deactivate")
async def wb_admin_promo_deactivate_cb(
    cb: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return

    await state.clear()
    await _show_admin_promo_list(cb.message, session=session, page=0)


@router.callback_query(F.data.regexp(r"wbm:admin:promo:list:(\d+)"))
async def wb_admin_promo_list_cb(
    cb: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return

    await state.clear()
    page = int(cb.data.split(":")[4])
    await _show_admin_promo_list(cb.message, session=session, page=page)


@router.callback_query(F.data.regexp(r"wbm:admin:promo:item:(\d+):(\d+)"))
async def wb_admin_promo_item_cb(
    cb: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return

    await state.clear()
    parts = cb.data.split(":")
    promo_id = int(parts[4])
    page = int(parts[5])

    promo = await get_promo_by_id(session, promo_id=promo_id)
    if promo is None or not promo.is_active:
        await cb.answer(tx.ADMIN_PROMO_DEACTIVATE_NOT_FOUND, show_alert=True)
        await _show_admin_promo_list(cb.message, session=session, page=page)
        return

    activations = await count_promo_activations(session, promo_id=promo.id)
    bot_me = await cb.bot.me()
    await cb.message.edit_text(
        _promo_card_text(
            promo=promo,
            activations=activations,
            bot_username=bot_me.username,
        ),
        reply_markup=admin_promo_card_kb(promo_id=promo.id, page=page),
    )


@router.callback_query(F.data.regexp(r"wbm:admin:promo:off:(\d+):(\d+)"))
async def wb_admin_promo_off_cb(
    cb: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    if not is_admin(cb.from_user.id, se):
        await cb.answer(tx.NO_ACCESS, show_alert=True)
        return

    await state.clear()
    parts = cb.data.split(":")
    promo_id = int(parts[4])
    page = int(parts[5])

    promo = await get_promo_by_id(session, promo_id=promo_id)
    if promo is None or not promo.is_active:
        await cb.answer(tx.ADMIN_PROMO_DEACTIVATE_NOT_FOUND, show_alert=True)
        await _show_admin_promo_list(cb.message, session=session, page=page)
        return

    changed = await deactivate_promo_link(session, promo_id=promo.id)
    await session.commit()

    if changed:
        await cb.answer("✅ Промо ссылка деактивирована.")
    else:
        await cb.answer(tx.ADMIN_PROMO_DEACTIVATE_ALREADY, show_alert=True)

    await _show_admin_promo_list(cb.message, session=session, page=page)


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


@router.message(SettingsState.waiting_for_promo_pro, F.text)
async def wb_admin_promo_pro_msg(
    msg: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if not msg.from_user or not is_admin(msg.from_user.id, se):
        await state.clear()
        return

    parsed = _parse_promo_create_payload(msg.text.strip())
    if parsed is None:
        await msg.answer(
            tx.ADMIN_PROMO_PRO_FORMAT_ERROR,
            reply_markup=admin_promo_input_kb(),
        )
        return

    days, life_hours = parsed
    if days < 1 or days > 365 or life_hours < 1 or life_hours > 720:
        await msg.answer(
            tx.ADMIN_PROMO_PRO_RANGE_ERROR,
            reply_markup=admin_promo_input_kb(),
        )
        return

    expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=life_hours)
    promo = await create_promo_link(
        session,
        kind="pro_days",
        value=days,
        expires_at=expires_at,
        created_by_tg_user_id=msg.from_user.id,
    )
    await session.commit()
    await state.clear()

    bot_me = await msg.bot.me()
    link = f"https://t.me/{bot_me.username}?start=promo_{promo.code}"
    await msg.answer(
        tx.ADMIN_PROMO_CREATED_PRO.format(
            link=link,
            days=days,
            expires=expires_at.strftime("%d.%m.%Y %H:%M"),
        ),
        reply_markup=admin_promo_kb(),
    )


@router.message(SettingsState.waiting_for_promo_discount, F.text)
async def wb_admin_promo_discount_msg(
    msg: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if not msg.from_user or not is_admin(msg.from_user.id, se):
        await state.clear()
        return

    parsed = _parse_promo_create_payload(msg.text.strip())
    if parsed is None:
        await msg.answer(
            tx.ADMIN_PROMO_DISCOUNT_FORMAT_ERROR,
            reply_markup=admin_promo_input_kb(),
        )
        return

    discount_percent, life_hours = parsed
    if (
        discount_percent < 1
        or discount_percent > 90
        or life_hours < 1
        or life_hours > 720
    ):
        await msg.answer(
            tx.ADMIN_PROMO_DISCOUNT_RANGE_ERROR,
            reply_markup=admin_promo_input_kb(),
        )
        return

    expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=life_hours)
    promo = await create_promo_link(
        session,
        kind="pro_discount",
        value=discount_percent,
        expires_at=expires_at,
        created_by_tg_user_id=msg.from_user.id,
    )
    await session.commit()
    await state.clear()

    bot_me = await msg.bot.me()
    link = f"https://t.me/{bot_me.username}?start=promo_{promo.code}"
    await msg.answer(
        tx.ADMIN_PROMO_CREATED_DISCOUNT.format(
            link=link,
            percent=discount_percent,
            expires=expires_at.strftime("%d.%m.%Y %H:%M"),
        ),
        reply_markup=admin_promo_kb(),
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
    if days not in {1, 7, 14, 30}:
        await cb.answer(tx.ADMIN_INVALID_PERIOD, show_alert=True)
        return

    stats = await get_admin_stats(session, days=days)
    try:
        await cb.message.edit_text(
            _admin_stats_text(stats),
            reply_markup=admin_panel_kb(selected_days=days),
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise
    await cb.answer()


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
    stats = await get_admin_stats(session, days=7)
    await msg.answer(
        tx.ADMIN_GRANT_PRO_DONE.format(
            tg_user_id=user.tg_user_id,
            days=days,
            expires=user.pro_expires_at.strftime("%d.%m.%Y %H:%M"),
        )
        + "\n\n"
        + _admin_stats_text(stats),
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
    admin = is_admin(cb.from_user.id, se)
    await cb.message.edit_text(
        dashboard_text(
            user.plan,
            used,
            free_interval_min=cfg.free_interval_min,
            pro_interval_min=cfg.pro_interval_min,
        ),
        reply_markup=dashboard_kb(
            admin,
            show_compare=_can_use_compare(plan=user.plan, admin=admin),
        ),
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


@router.callback_query(F.data.regexp(r"wbm:qty:(\d+)"))
async def wb_settings_qty_cb(cb: CallbackQuery, session: AsyncSession) -> None:
    track_id = int(cb.data.split(":")[2])
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )

    if not _is_paid_plan(user.plan):
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
                has_sizes=bool(track.last_sizes),
                pro_plan=True,
                qty_on=track.watch_qty,
                stock_on=track.watch_stock,
                price_fluctuation_on=track.watch_price_fluctuation,
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


@router.callback_query(F.data.regexp(r"wbm:stock:(\d+)"))
async def wb_settings_stock_cb(cb: CallbackQuery, session: AsyncSession) -> None:
    track_id = int(cb.data.split(":")[2])
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )

    track = await get_user_track_by_id(session, track_id)
    if not track:
        await cb.answer(tx.TRACK_NOT_FOUND, show_alert=True)
        return

    track.watch_stock = not track.watch_stock
    await session.commit()

    try:
        await cb.message.edit_text(
            format_track_text(track) + tx.SETTINGS_SUFFIX,
            reply_markup=settings_kb(
                track_id,
                has_sizes=bool(track.last_sizes),
                pro_plan=_is_paid_plan(user.plan),
                qty_on=track.watch_qty,
                stock_on=track.watch_stock,
                price_fluctuation_on=track.watch_price_fluctuation,
            ),
        )
    except TelegramBadRequest:
        pass

    await cb.answer(
        tx.SETTINGS_STOCK_ANSWER.format(
            state=(
                tx.SETTINGS_STOCK_STATE_ON
                if track.watch_stock
                else tx.SETTINGS_STOCK_STATE_OFF
            )
        )
    )


@router.callback_query(F.data.regexp(r"wbm:price_fluctuation:(\d+)"))
async def wb_settings_price_fluctuation_cb(
    cb: CallbackQuery, session: AsyncSession
) -> None:
    track_id = int(cb.data.split(":")[2])
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )

    track = await get_user_track_by_id(session, track_id)
    if not track:
        await cb.answer(tx.TRACK_NOT_FOUND, show_alert=True)
        return

    track.watch_price_fluctuation = not track.watch_price_fluctuation
    await session.commit()

    try:
        await cb.message.edit_text(
            format_track_text(track) + tx.SETTINGS_SUFFIX,
            reply_markup=settings_kb(
                track_id,
                has_sizes=bool(track.last_sizes),
                pro_plan=_is_paid_plan(user.plan),
                qty_on=track.watch_qty,
                stock_on=track.watch_stock,
                price_fluctuation_on=track.watch_price_fluctuation,
            ),
        )
    except TelegramBadRequest:
        pass

    await cb.answer(
        tx.SETTINGS_PRICE_FLUCTUATION_ANSWER.format(
            state=(
                tx.SETTINGS_PRICE_FLUCTUATION_STATE_ON
                if track.watch_price_fluctuation
                else tx.SETTINGS_PRICE_FLUCTUATION_STATE_OFF
            )
        )
    )


def _sizes_picker_kb(track_id: int, all_sizes: list[str], selected: set[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []

    for idx, size in enumerate(all_sizes):
        mark = "✅" if size in selected else "☑️"
        row.append(
            InlineKeyboardButton(
                text=f"{mark} {size}",
                callback_data=f"wbm:sizesel:{track_id}:{idx}",
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    rows.append([InlineKeyboardButton(text=tx.BTN_SIZES_RESET, callback_data=f"wbm:sizes_clear:{track_id}")])
    rows.append([InlineKeyboardButton(text=tx.BTN_SIZES_APPLY, callback_data=f"wbm:sizes_apply:{track_id}", style="success")])
    rows.append([
        InlineKeyboardButton(text=tx.SETTINGS_CANCEL_BTN, callback_data=f"wbm:settings:{track_id}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _sizes_picker_text(selected: set[str]) -> str:
    selected_text = ", ".join(sorted(selected)) if selected else tx.SETTINGS_SIZES_NONE
    return (
        f"{tx.SETTINGS_SIZES_PROMPT}\n\n"
        f"{tx.SETTINGS_SIZES_SELECTED.format(sizes=selected_text)}"
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

    selected = set(track.watch_sizes or track.last_sizes or [])
    await state.update_data(track_id=track_id, selected_sizes=list(selected))
    await state.set_state(SettingsState.waiting_for_sizes)

    await cb.message.edit_text(
        _sizes_picker_text(selected),
        reply_markup=_sizes_picker_kb(track_id, track.last_sizes, selected),
    )


@router.callback_query(F.data.regexp(r"wbm:sizesel:(\d+):(\d+)"))
async def wb_settings_sizes_toggle_cb(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    parts = cb.data.split(":")
    track_id = int(parts[2])
    size_idx = int(parts[3])

    data = await state.get_data()
    track = await get_user_track_by_id(session, track_id)
    if not track or not track.last_sizes:
        await cb.answer(tx.SETTINGS_NO_SIZES, show_alert=True)
        return

    if size_idx < 0 or size_idx >= len(track.last_sizes):
        await cb.answer(tx.INVALID_PAGE, show_alert=True)
        return

    selected_raw = data.get("selected_sizes", None)
    if selected_raw is None:
        selected = set(track.watch_sizes or track.last_sizes or [])
    else:
        selected = set(selected_raw)

    size = track.last_sizes[size_idx]
    if size in selected:
        selected.remove(size)
    else:
        selected.add(size)

    await state.update_data(track_id=track_id, selected_sizes=list(selected))
    await cb.message.edit_text(
        _sizes_picker_text(selected),
        reply_markup=_sizes_picker_kb(track_id, track.last_sizes, selected),
    )
    await cb.answer()


@router.message(SettingsState.waiting_for_sizes, F.text)
async def wb_settings_sizes_text_fallback(
    msg: Message, state: FSMContext, session: AsyncSession
) -> None:
    data = await state.get_data()
    track_id = data.get("track_id")
    if not track_id:
        await state.clear()
        return

    track = await get_user_track_by_id(session, int(track_id))
    if not track or not track.last_sizes:
        await state.clear()
        await msg.answer(tx.SETTINGS_NO_SIZES)
        return

    selected_raw = data.get("selected_sizes", None)
    if selected_raw is None:
        selected = set(track.watch_sizes or track.last_sizes or [])
    else:
        selected = set(selected_raw)
    await msg.answer(
        "ℹ️ Выбор размеров теперь только кнопками. Нажмите нужные размеры ниже и затем «✅ Подтвердить».",
        reply_markup=_sizes_picker_kb(track.id, track.last_sizes, selected),
    )


@router.callback_query(F.data.regexp(r"wbm:sizes_apply:(\d+)"))
async def wb_settings_sizes_apply_cb(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    track_id = int(cb.data.split(":")[2])
    data = await state.get_data()

    track = await get_user_track_by_id(session, track_id)
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    if not track:
        await state.clear()
        await cb.answer(tx.TRACK_NOT_FOUND, show_alert=True)
        return

    selected = set(data.get("selected_sizes") or [])
    track.watch_sizes = [s for s in (track.last_sizes or []) if s in selected]
    await session.commit()
    await state.clear()

    await cb.message.edit_text(
        format_track_text(track) + tx.SETTINGS_SUFFIX,
        reply_markup=settings_kb(
            track_id,
            has_sizes=bool(track.last_sizes),
            pro_plan=_is_paid_plan(user.plan),
            qty_on=track.watch_qty,
            stock_on=track.watch_stock,
            price_fluctuation_on=track.watch_price_fluctuation,
        ),
    )
    await cb.answer(
        tx.SETTINGS_SIZES_DONE.format(
            sizes=(", ".join(track.watch_sizes) if track.watch_sizes else tx.SETTINGS_SIZES_NONE)
        )
    )


@router.callback_query(F.data.regexp(r"wbm:sizes_clear:(\d+)"))
async def wb_settings_sizes_clear_cb(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    track_id = int(cb.data.split(":")[2])
    track = await get_user_track_by_id(session, track_id)
    if not track or not track.last_sizes:
        await cb.answer(tx.SETTINGS_NO_SIZES, show_alert=True)
        return

    # Сброс применяется сразу, без дополнительного подтверждения
    track.watch_sizes = []
    await session.commit()

    await state.update_data(track_id=track_id, selected_sizes=[])
    await cb.message.edit_text(
        _sizes_picker_text(set()),
        reply_markup=_sizes_picker_kb(track_id, track.last_sizes, set()),
    )
    await cb.answer(tx.SETTINGS_SIZES_RESET_DONE)


# ─── Support ─────────────────────────────────────────────────────────────────


from bot.services.repository import (
    create_support_ticket,
    get_open_tickets,
    get_ticket_by_id,
    reply_to_ticket,
    close_ticket,
    count_open_tickets,
    add_ticket_photo,
)


@router.callback_query(F.data == "wbm:help:0")
async def wb_help_cb(cb: CallbackQuery, session: AsyncSession) -> None:
    """Показать раздел помощи и поддержки."""
    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )
    is_admin_flag = is_admin(cb.from_user.id, se)

    # Для админа показываем количество открытых тикетов
    if is_admin_flag:
        open_count = await count_open_tickets(session)
        text = (
            tx.HELP_TEXT_ADMIN.format(open_tickets=open_count)
            if hasattr(tx, "HELP_TEXT_ADMIN")
            else tx.HELP_TEXT
        )
    else:
        text = (
            tx.HELP_TEXT
            if hasattr(tx, "HELP_TEXT")
            else "📨 Нажмите кнопку ниже, чтобы написать в поддержку."
        )

    await cb.message.edit_text(
        text,
        reply_markup=support_kb(),
    )


@router.callback_query(F.data == "wbm:support:start")
async def wb_support_start_cb(cb: CallbackQuery, state: FSMContext) -> None:
    """Начать создание тикета — сбросить состояние и показать подсказку."""
    await state.set_state(SupportState.waiting_for_message_or_media)
    await state.update_data(photos=[], message_text=None)
    await cb.message.edit_text(
        tx.SUPPORT_PROMPT_WITH_MEDIA,
        reply_markup=support_cancel_kb(),
    )


@router.callback_query(F.data == "wbm:support:cancel")
async def wb_support_cancel_cb(cb: CallbackQuery, state: FSMContext) -> None:
    """Отменить создание тикета."""
    await state.clear()
    await cb.message.edit_text(tx.SUPPORT_CANCELLED)
    await asyncio.sleep(1)
    # Возвращаем в меню
    await wb_home_cb(cb)


@router.message(SupportState.waiting_for_message_or_media, F.text)
async def wb_support_text_msg(
    msg: Message, state: FSMContext, session: AsyncSession
) -> None:
    """Получить текст сообщения для тикета."""
    data = await state.get_data()
    photos = data.get("photos", [])

    # Сохраняем текст
    await state.update_data(message_text=msg.text)

    # Переходим в состояние подтверждения
    await state.set_state(SupportState.waiting_for_media_confirmation)

    if photos:
        await msg.answer(
            tx.SUPPORT_MEDIA_ADDED.format(count=len(photos)),
            reply_markup=support_media_confirmation_kb(),
        )
    else:
        # Только текст, без фото
        await msg.answer(
            f"📝 <b>Сообщение:</b>\n{msg.text[:500]}{'...' if len(msg.text) > 500 else ''}\n\n"
            f"{tx.SUPPORT_CONFIRM_SEND}",
            reply_markup=support_media_confirmation_kb(),
        )


@router.message(SupportState.waiting_for_message_or_media, F.photo)
async def wb_support_photo_msg(
    msg: Message, state: FSMContext, session: AsyncSession
) -> None:
    """Получить фото для тикета."""
    data = await state.get_data()
    photos = data.get("photos", [])
    message_text = data.get("message_text")

    # Берем фото максимального качества (последнее в списке)
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

    # Переходим в состояние подтверждения
    await state.set_state(SupportState.waiting_for_media_confirmation)

    text = tx.SUPPORT_MEDIA_ADDED.format(count=len(photos))
    if message_text:
        text = f"📝 <b>Сообщение:</b>\n{message_text[:300]}{'...' if len(message_text) > 300 else ''}\n\n{text}"

    await msg.answer(
        text,
        reply_markup=support_media_confirmation_kb(),
    )


@router.message(SupportState.waiting_for_message_or_media)
async def wb_support_invalid_msg(msg: Message) -> None:
    """Обработать некорректное сообщение (не текст и не фото)."""
    await msg.answer(tx.SUPPORT_NO_TEXT_NO_MEDIA)


@router.callback_query(F.data == "wbm:support:add_more")
async def wb_support_add_more_cb(cb: CallbackQuery, state: FSMContext) -> None:
    """Пользователь хочет добавить ещё фото."""
    await state.set_state(SupportState.waiting_for_message_or_media)
    await cb.message.edit_text(
        tx.SUPPORT_PROMPT_WITH_MEDIA + "\n\n<i>Отправьте следующее фото:</i>",
        reply_markup=support_cancel_kb(),
    )


@router.callback_query(F.data == "wbm:support:send")
async def wb_support_send_cb(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    """Отправить тикет с фото и/или текстом."""
    data = await state.get_data()
    photos = data.get("photos", [])
    message_text = data.get("message_text")

    # Проверяем что есть что отправлять
    if not message_text and not photos:
        await cb.answer(tx.SUPPORT_NO_TEXT_NO_MEDIA, show_alert=True)
        return

    user = await get_or_create_monitor_user(
        session, cb.from_user.id, cb.from_user.username
    )

    # Создаём тикет
    ticket = await create_support_ticket(
        session,
        user_id=user.id,
        tg_user_id=cb.from_user.id,
        username=cb.from_user.username,
        message=message_text or "(без текста, только фото)",
    )

    # Сохраняем фото
    for photo_data in photos:
        await add_ticket_photo(
            session,
            ticket_id=ticket.id,
            file_id=photo_data["file_id"],
            file_unique_id=photo_data["file_unique_id"],
            width=photo_data["width"],
            height=photo_data["height"],
            file_size=photo_data["file_size"],
        )

    await state.clear()
    await cb.message.edit_text(tx.SUPPORT_SENT)
    admin = is_admin(cb.from_user.id, se)
    await cb.message.answer(
        tx.SUPPORT_SENT,
        reply_markup=dashboard_kb(
            admin,
            show_compare=_can_use_compare(plan=user.plan, admin=admin),
        ),
    )

    # Уведомляем админов о новом тикете
    admin_ids = se.admin_ids_list or {se.developer_id}
    for admin_id in admin_ids:
        try:
            username_display = (
                f"@{ticket.username}" if ticket.username else f"ID:{ticket.tg_user_id}"
            )

            # Отправляем уведомление с фото (media group или отдельно)
            if photos:
                # Отправляем фото группой, если их несколько
                if len(photos) > 1:
                    media_group = [
                        InputMediaPhoto(media=p["file_id"])
                        for p in photos[:10]  # max 10 для альбома
                    ]
                    await bot.send_media_group(admin_id, media=media_group)
                elif photos:
                    await bot.send_photo(admin_id, photo=photos[0]["file_id"])

                # Затем отправляем текст с информацией о тикете
                await bot.send_message(
                    admin_id,
                    tx.SUPPORT_ADMIN_NOTIFY.format(
                        username=username_display,
                        user_id=ticket.tg_user_id,
                        created_at=ticket.created_at.strftime("%d.%m.%Y %H:%M"),
                        message=ticket.message,
                    ),
                    reply_markup=admin_support_ticket_kb(ticket.id),
                )
            else:
                # Только текст
                await bot.send_message(
                    admin_id,
                    tx.SUPPORT_ADMIN_NOTIFY.format(
                        username=username_display,
                        user_id=ticket.tg_user_id,
                        created_at=ticket.created_at.strftime("%d.%m.%Y %H:%M"),
                        message=ticket.message,
                    ),
                    reply_markup=admin_support_ticket_kb(ticket.id),
                )
        except Exception as e:
            logger.warning("Failed to notify admin %s about ticket: %s", admin_id, e)

    # Отмечаем, что админы уведомлены
    ticket.admin_notified = True
    await session.commit()


@router.callback_query(F.data.regexp(r"wbm:support:admin:reply:(\d+)"))
async def wb_support_admin_reply_cb(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """Админ нажал кнопку ответа на тикет."""
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
        f"📝 Сообщение: {ticket.message[:200]}...\n\n"
        f"Напишите ваш ответ:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ Отмена", callback_data=f"wbm:support:admin:cancel"
                    )
                ]
            ]
        ),
    )
    await cb.answer()


@router.message(SupportState.waiting_for_admin_reply, F.text)
async def wb_support_admin_reply_msg(
    msg: Message, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    """Админ отправил ответ на тикет."""
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    reply_to_user_id = data.get("reply_to_user_id")

    if not ticket_id:
        await state.clear()
        return

    # Сохраняем ответ
    ticket = await reply_to_ticket(
        session,
        ticket_id=ticket_id,
        response=msg.text,
        responded_by_tg_id=msg.from_user.id,
    )

    await state.clear()

    if ticket:
        # Отправляем ответ пользователю
        try:
            await bot.send_message(
                reply_to_user_id,
                tx.SUPPORT_USER_REPLY.format(response=ticket.response),
            )
        except Exception as e:
            logger.warning("Failed to send reply to user %s: %s", reply_to_user_id, e)

        await msg.answer(tx.SUPPORT_ADMIN_REPLY_SENT)
    else:
        await msg.answer("❌ Ошибка: тикет не найден")


@router.callback_query(F.data.regexp(r"wbm:support:admin:close:(\d+)"))
async def wb_support_admin_close_cb(cb: CallbackQuery, session: AsyncSession) -> None:
    """Админ закрыл тикет без ответа."""
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
    """Админ отменил ответ на тикет."""
    await state.clear()
    await cb.message.delete()
    await cb.answer("Отменено")
