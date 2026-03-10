"""
keyboards/inline.py
Inline-клавиатуры с поддержкой Bot API 9.4.

Допустимые значения style (aiogram.enums.ButtonStyle):
  "primary"  — синий  (главное действие)
  "success"  — зелёный (позитивное/платёж)
  "danger"   — красный (удаление/отмена)
  None       — стандартный серый
"""

from __future__ import annotations

from urllib.parse import quote, urlencode

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.callbacks import (
    AdminAction,
    AdminActionCb,
    AdminPromoItemCb,
    AdminPromoOffCb,
    AdminPromoPageCb,
    AdminStatsCb,
    CompareAction,
    CompareActionCb,
    CompareModeCb,
    NavAction,
    NavCb,
    PaymentActionCb,
    PaymentMethod,
    PlanOfferCb,
    QuickAction,
    QuickActionCb,
    QuickModeCb,
    SupportAction,
    SupportActionCb,
    SupportTicketAction,
    SupportTicketActionCb,
    TrackAction,
    TrackActionCb,
    TrackModeCb,
    TrackPageCb,
    TrackPagePickerCb,
    TrackSizeSelectCb,
)
from bot.enums import CompareMode, PlanOfferCode, SearchMode, UserPlan
from bot.db.models import TrackModel
from bot.services.config import (
    FREE_INTERVAL,
    FREE_LIMIT,
    PRO_INTERVAL,
    PRO_LIMIT,
    PRO_PLUS_LIMIT,
)
from bot import text as tx


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _btn(
    text: str,
    callback_data: str | CallbackData,
    style: str | None = None,
) -> InlineKeyboardButton:
    """Shorthand для callback-кнопки с опциональным style (Bot API 9.4)."""
    packed = (
        callback_data.pack()
        if isinstance(callback_data, CallbackData)
        else callback_data
    )
    return InlineKeyboardButton(text=text, callback_data=packed, style=style)


# ─── Dashboard ────────────────────────────────────────────────────────────────


def dashboard_text(
    plan: str,
    used: int,
    *,
    free_interval_min: int = FREE_INTERVAL,
    pro_interval_min: int = PRO_INTERVAL,
) -> str:
    if plan == UserPlan.PRO_PLUS.value:
        limit = PRO_PLUS_LIMIT
        interval = pro_interval_min
        plan_badge = tx.PLAN_BADGE_PRO_PLUS
    elif plan == UserPlan.PRO.value:
        limit = PRO_LIMIT
        interval = pro_interval_min
        plan_badge = tx.PLAN_BADGE_PRO
    else:
        limit = FREE_LIMIT
        interval = free_interval_min
        plan_badge = tx.PLAN_BADGE_FREE
    return tx.dashboard_text(
        plan_badge=plan_badge,
        used=used,
        limit=limit,
        interval=interval,
    )


def dashboard_kb(is_admin: bool, *, show_compare: bool = True) -> InlineKeyboardMarkup:
    rows = [
        [
            # primary — синий (главное действие)
            _btn(tx.BTN_ADD_ITEM, NavCb(action=NavAction.ADD), style="primary"),
            _btn(tx.BTN_MY_TRACKS, NavCb(action=NavAction.LIST)),
        ],
        [
            _btn(tx.BTN_PLAN, NavCb(action=NavAction.PLAN)),
            _btn(tx.BTN_REFERRAL, NavCb(action=NavAction.REF)),
        ],
        [
            _btn(tx.BTN_SUPPORT, NavCb(action=NavAction.HELP)),
        ],
    ]
    if show_compare:
        rows.insert(
            2, [_btn(tx.BTN_COMPARE, CompareActionCb(action=CompareAction.OPEN))]
        )

    if is_admin:
        rows.append([_btn(tx.BTN_ADMIN, AdminActionCb(action=AdminAction.OPEN))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_dashboard_kb(is_admin: bool) -> InlineKeyboardMarkup:
    rows = [[_btn(tx.BTN_BACK_MENU, NavCb(action=NavAction.HOME))]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def add_item_prompt_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn(
                    tx.SETTINGS_CANCEL_BTN,
                    NavCb(action=NavAction.CANCEL),
                    style="danger",
                )
            ]
        ]
    )


# ─── Track view ───────────────────────────────────────────────────────────────


def format_track_text(track: TrackModel) -> str:
    return tx.format_track_text(track)


def paged_track_kb(
    track: TrackModel,
    page: int,
    total: int,
    confirm_remove: bool = False,
    cheap_btn_text: str | None = None,
    reviews_btn_text: str | None = None,
) -> InlineKeyboardMarkup:
    if track.is_active:
        action_btn = _btn(
            tx.BTN_PAUSE, TrackActionCb(action=TrackAction.PAUSE, track_id=track.id)
        )
    else:
        # success — зелёный «Возобновить»
        action_btn = _btn(
            tx.BTN_RESUME,
            TrackActionCb(action=TrackAction.RESUME, track_id=track.id),
            style="success",
        )

    safe_total = max(1, total)
    prev_page = (page - 1) % safe_total
    next_page = (page + 1) % safe_total

    nav: list[InlineKeyboardButton] = [
        _btn("⬅️", TrackPageCb(page=prev_page)),
        _btn(
            f"{page + 1} / {total}",
            TrackPagePickerCb(track_id=track.id, current_page=page, offset=0),
        ),
        _btn("➡️", TrackPageCb(page=next_page)),
    ]

    if confirm_remove:
        top_rows = [
            [
                _btn(
                    tx.BTN_REMOVE_CONFIRM,
                    TrackActionCb(action=TrackAction.REMOVE_YES, track_id=track.id),
                    style="danger",
                ),
                _btn(
                    tx.BTN_REMOVE_CANCEL,
                    TrackActionCb(action=TrackAction.REMOVE_NO, track_id=track.id),
                ),
            ]
        ]
    else:
        top_rows = [
            [
                _btn(
                    tx.BTN_REMOVE,
                    TrackActionCb(action=TrackAction.REMOVE, track_id=track.id),
                    style="danger",
                )
            ]
        ]

    return InlineKeyboardMarkup(
        inline_keyboard=[
            *top_rows,
            [
                action_btn,
                _btn(
                    tx.BTN_SETTINGS,
                    TrackActionCb(action=TrackAction.SETTINGS, track_id=track.id),
                ),
            ],
            [
                _btn(
                    cheap_btn_text or tx.BTN_FIND_CHEAPER,
                    TrackActionCb(action=TrackAction.CHEAP, track_id=track.id),
                )
            ],
            [
                _btn(
                    reviews_btn_text or tx.BTN_REVIEW_ANALYSIS,
                    TrackActionCb(action=TrackAction.REVIEWS, track_id=track.id),
                )
            ],
            nav,
            [_btn(tx.BTN_BACK_MENU, NavCb(action=NavAction.HOME))],
        ]
    )


def track_page_picker_kb(
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
        page_buttons.append(_btn(label, TrackPageCb(page=i)))
        if len(page_buttons) >= per_row:
            rows.append(page_buttons)
            page_buttons = []
    if page_buttons:
        rows.append(page_buttons)

    if safe_total > max_buttons:
        nav_row: list[InlineKeyboardButton] = []
        if safe_offset > 0:
            nav_row.append(
                _btn(
                    "⬅️",
                    TrackPagePickerCb(
                        track_id=track_id,
                        current_page=current_page,
                        offset=max(0, safe_offset - max_buttons),
                    ),
                )
            )
        nav_row.append(
            _btn(
                f"{safe_offset + 1}-{end} / {safe_total}", NavCb(action=NavAction.NOOP)
            )
        )
        if end < safe_total:
            nav_row.append(
                _btn(
                    "➡️",
                    TrackPagePickerCb(
                        track_id=track_id,
                        current_page=current_page,
                        offset=end,
                    ),
                )
            )
        rows.append(nav_row)

    rows.append(
        [
            _btn(
                tx.BTN_BACK,
                TrackPagePickerCb(
                    track_id=track_id, current_page=current_page, offset=-1
                ),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_kb(
    track_id: int,
    has_sizes: bool = True,
    pro_plan: bool = False,
    qty_on: bool = False,
    stock_on: bool = True,
    price_fluctuation_on: bool = True,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    # Кнопка уведомлений о наличии (доступна всем)
    stock_style = "success" if stock_on else None
    stock_label = tx.STOCK_ON_LABEL if stock_on else tx.STOCK_OFF_LABEL
    rows.append(
        [
            _btn(
                stock_label,
                TrackActionCb(action=TrackAction.STOCK, track_id=track_id),
                style=stock_style,
            )
        ]
    )
    # Кнопка отслеживания колебаний цены (доступна всем)
    fluctuation_style = "success" if price_fluctuation_on else None
    fluctuation_label = (
        tx.PRICE_FLUCTUATION_ON_LABEL
        if price_fluctuation_on
        else tx.PRICE_FLUCTUATION_OFF_LABEL
    )
    rows.append(
        [
            _btn(
                fluctuation_label,
                TrackActionCb(action=TrackAction.PRICE_FLUCTUATION, track_id=track_id),
                style=fluctuation_style,
            )
        ]
    )
    if pro_plan:
        qty_style = "success" if qty_on else None
        qty_label = tx.QTY_ON_LABEL if qty_on else tx.QTY_OFF_LABEL
        rows.append(
            [
                _btn(
                    qty_label,
                    TrackActionCb(action=TrackAction.QTY, track_id=track_id),
                    style=qty_style,
                )
            ]
        )
    if has_sizes:
        rows.append(
            [
                _btn(
                    tx.BTN_SIZES,
                    TrackActionCb(action=TrackAction.SIZES, track_id=track_id),
                )
            ]
        )
    rows.append(
        [_btn(tx.BTN_BACK, TrackActionCb(action=TrackAction.BACK, track_id=track_id))]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sizes_picker_kb(
    *,
    track_id: int,
    all_sizes: list[str],
    selected: set[str],
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for idx, size in enumerate(all_sizes):
        mark = "✅" if size in selected else "☑️"
        row.append(
            _btn(
                f"{mark} {size}",
                TrackSizeSelectCb(track_id=track_id, size_idx=idx),
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [
            _btn(
                tx.BTN_SIZES_RESET,
                TrackActionCb(action=TrackAction.SIZES_CLEAR, track_id=track_id),
            )
        ]
    )
    rows.append(
        [
            _btn(
                tx.BTN_SIZES_APPLY,
                TrackActionCb(action=TrackAction.SIZES_APPLY, track_id=track_id),
            )
        ]
    )
    rows.append(
        [
            _btn(
                tx.SETTINGS_CANCEL_BTN,
                TrackActionCb(action=TrackAction.SETTINGS, track_id=track_id),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def track_search_mode_kb(track_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn(
                    tx.SEARCH_MODE_CHEAPER_BTN,
                    TrackModeCb(mode=SearchMode.CHEAP, track_id=track_id),
                )
            ],
            [
                _btn(
                    tx.SEARCH_MODE_SIMILAR_BTN,
                    TrackModeCb(mode=SearchMode.SIMILAR, track_id=track_id),
                )
            ],
            [
                _btn(
                    tx.FIND_CHEAPER_TO_LIST_BTN,
                    TrackActionCb(action=TrackAction.BACK, track_id=track_id),
                )
            ],
        ]
    )


def track_search_back_kb(track_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn(
                    tx.BTN_BACK,
                    TrackActionCb(action=TrackAction.CHEAP, track_id=track_id),
                )
            ]
        ]
    )


def reviews_back_to_track_kb(track_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn(
                    tx.REVIEWS_BACK_TO_TRACK_BTN,
                    TrackActionCb(action=TrackAction.BACK, track_id=track_id),
                )
            ]
        ]
    )


def quick_item_kb(
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
                _btn(
                    tx.QUICK_ADD_BTN,
                    QuickActionCb(action=QuickAction.ADD, wb_item_id=wb_item_id),
                )
            ]
        )
    rows.append(
        [
            _btn(
                reviews_btn_text or tx.QUICK_REVIEWS_BTN,
                QuickActionCb(action=QuickAction.REVIEWS, wb_item_id=wb_item_id),
            )
        ]
    )
    rows.append(
        [
            _btn(
                search_btn_text or tx.QUICK_SEARCH_BTN,
                QuickActionCb(action=QuickAction.SEARCH, wb_item_id=wb_item_id),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def quick_search_mode_kb(wb_item_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn(
                    tx.SEARCH_MODE_CHEAPER_BTN,
                    QuickModeCb(mode=SearchMode.CHEAP, wb_item_id=wb_item_id),
                )
            ],
            [
                _btn(
                    tx.SEARCH_MODE_SIMILAR_BTN,
                    QuickModeCb(mode=SearchMode.SIMILAR, wb_item_id=wb_item_id),
                )
            ],
            [
                _btn(
                    tx.BTN_BACK,
                    QuickActionCb(action=QuickAction.PREVIEW, wb_item_id=wb_item_id),
                )
            ],
        ]
    )


def quick_back_preview_kb(wb_item_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn(
                    tx.BTN_BACK,
                    QuickActionCb(action=QuickAction.PREVIEW, wb_item_id=wb_item_id),
                )
            ]
        ]
    )


def quick_back_search_kb(wb_item_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn(
                    tx.BTN_BACK,
                    QuickActionCb(action=QuickAction.SEARCH, wb_item_id=wb_item_id),
                )
            ]
        ]
    )


def compare_mode_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn(tx.BTN_COMPARE_MODE_CHEAP, CompareModeCb(mode=CompareMode.CHEAP))],
            [
                _btn(
                    tx.BTN_COMPARE_MODE_QUALITY, CompareModeCb(mode=CompareMode.QUALITY)
                )
            ],
            [_btn(tx.BTN_COMPARE_MODE_GIFT, CompareModeCb(mode=CompareMode.GIFT))],
            [_btn(tx.BTN_COMPARE_MODE_SAFE, CompareModeCb(mode=CompareMode.SAFE))],
            [_btn(tx.SETTINGS_CANCEL_BTN, NavCb(action=NavAction.CANCEL))],
        ]
    )


# ─── Plan / Payment ───────────────────────────────────────────────────────────


def plan_kb(
    is_pro: bool,
    expires_str: str | None = None,
    pay_btn_text: str | None = None,
    discount: object | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    if not is_pro:
        # Две кнопки оплаты сразу: карта и звёзды
        if discount:
            card_amount = max(1, int(round(150 * (100 - discount.percent) / 100)))
            card_text = tx.BTN_PAY_CARD_DISCOUNT.format(
                amount=card_amount, percent=discount.percent
            )
            stars_amount = max(1, int(round(150 * (100 - discount.percent) / 100)))
            stars_text = tx.BTN_PAY_PRO_DISCOUNT.format(
                amount=stars_amount, percent=discount.percent
            )
        else:
            card_text = tx.BTN_PAY_CARD
            stars_text = tx.BTN_PAY_PRO

        rows.append(
            [
                _btn(
                    card_text,
                    PaymentActionCb(
                        method=PaymentMethod.CARD, offer_code=PlanOfferCode.PRO
                    ),
                    style="primary",
                )
            ]
        )
        rows.append(
            [
                _btn(
                    stars_text,
                    PaymentActionCb(
                        method=PaymentMethod.STARS, offer_code=PlanOfferCode.PRO
                    ),
                )
            ]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{tx.BTN_PRO_ACTIVE}"
                        f"{tx.BTN_PRO_ACTIVE_UNTIL_DELIM + expires_str if expires_str else ''}"
                    ),
                    callback_data=NavCb(action=NavAction.NOOP).pack(),
                    style="success",
                )
            ]
        )

    rows.append([_btn(tx.BTN_BACK_MENU, NavCb(action=NavAction.HOME))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def plan_overview_kb(*, show_purchase_buttons: bool = True) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if show_purchase_buttons:
        rows.append(
            [
                _btn(
                    tx.BTN_PLAN_SELECT_PRO,
                    PlanOfferCb(offer_code=PlanOfferCode.PRO),
                    style="primary",
                ),
                _btn(
                    tx.BTN_PLAN_SELECT_PRO_PLUS,
                    PlanOfferCb(offer_code=PlanOfferCode.PRO_PLUS),
                    style="success",
                ),
            ]
        )
    rows.append([_btn(tx.BTN_BACK_MENU, NavCb(action=NavAction.HOME))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def plan_offer_kb(
    *,
    offer_code: str,
    card_amount: int,
    stars_amount: int,
    discount: object | None = None,
) -> InlineKeyboardMarkup:
    if discount:
        card_text = tx.BTN_PAY_CARD_DISCOUNT.format(
            amount=card_amount,
            percent=discount.percent,
        )
        stars_text = tx.BTN_PAY_PRO_DISCOUNT.format(
            amount=stars_amount,
            percent=discount.percent,
        )
    else:
        card_text = tx.BTN_PAY_CARD_AMOUNT.format(amount=card_amount)
        stars_text = tx.BTN_PAY_STARS_AMOUNT.format(amount=stars_amount)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn(
                    card_text,
                    PaymentActionCb(
                        method=PaymentMethod.CARD, offer_code=PlanOfferCode(offer_code)
                    ),
                    style="primary",
                )
            ],
            [
                _btn(
                    stars_text,
                    PaymentActionCb(
                        method=PaymentMethod.STARS, offer_code=PlanOfferCode(offer_code)
                    ),
                )
            ],
            [_btn(tx.BTN_BACK, NavCb(action=NavAction.PLAN))],
        ]
    )


def payment_choice_kb(discount: object | None = None) -> InlineKeyboardMarkup:
    """Клавиатура выбора способа оплаты."""
    rows: list[list[InlineKeyboardButton]] = []

    # Оплата картой
    if discount:
        card_amount = max(1, int(round(150 * (100 - discount.percent) / 100)))
        card_text = tx.BTN_PAY_CARD_DISCOUNT.format(
            amount=card_amount, percent=discount.percent
        )
    else:
        card_text = tx.BTN_PAY_CARD
    rows.append(
        [
            _btn(
                card_text,
                PaymentActionCb(
                    method=PaymentMethod.CARD, offer_code=PlanOfferCode.PRO
                ),
                style="primary",
            )
        ]
    )

    # Оплата звёздами
    if discount:
        stars_amount = max(1, int(round(150 * (100 - discount.percent) / 100)))
        stars_text = tx.BTN_PAY_PRO_DISCOUNT.format(
            amount=stars_amount, percent=discount.percent
        )
    else:
        stars_text = tx.BTN_PAY_STARS
    rows.append(
        [
            _btn(
                stars_text,
                PaymentActionCb(
                    method=PaymentMethod.STARS, offer_code=PlanOfferCode.PRO
                ),
            )
        ]
    )

    rows.append([_btn(tx.BTN_BACK, NavCb(action=NavAction.PLAN))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def invoice_kb() -> InlineKeyboardMarkup:
    """Клавиатура внутри инвойса — pay=True автоматически делает кнопку зелёной."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=tx.BTN_PAY_STARS, pay=True)]]
    )


def ref_kb(ref_link: str) -> InlineKeyboardMarkup:
    share_query = urlencode(
        {
            "url": ref_link,
            "text": tx.REFERRAL_SHARE_TEXT,
        },
        quote_via=quote,
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=tx.BTN_SHARE_LINK,
                    url=f"https://t.me/share/url?{share_query}",
                )
            ],
            [_btn(tx.BTN_BACK_MENU, NavCb(action=NavAction.HOME))],
        ]
    )


# ─── Admin ───────────────────────────────────────────────────────────────────


def admin_panel_kb(selected_days: int | None = None) -> InlineKeyboardMarkup:
    def _label(days: int) -> str:
        if selected_days == days:
            return tx.BTN_ADMIN_DAYS_SELECTED.format(days=days)
        return tx.BTN_ADMIN_DAYS.format(days=days)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn(_label(1), AdminStatsCb(days=1)),
                _btn(_label(7), AdminStatsCb(days=7)),
                _btn(_label(14), AdminStatsCb(days=14)),
                _btn(_label(30), AdminStatsCb(days=30)),
            ],
            [_btn(tx.BTN_ADMIN_SETTINGS, AdminActionCb(action=AdminAction.CFG))],
            [_btn(tx.BTN_ADMIN_PROMO, AdminActionCb(action=AdminAction.PROMO))],
            [
                _btn(
                    tx.BTN_ADMIN_GRANT_PRO,
                    AdminActionCb(action=AdminAction.GRANTPRO),
                    style="success",
                )
            ],
            [_btn(tx.BTN_BACK_MENU, NavCb(action=NavAction.HOME))],
        ]
    )


def admin_grant_pro_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn(tx.BTN_BACK, AdminActionCb(action=AdminAction.OPEN))],
        ]
    )


def admin_config_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn(
                    tx.BTN_ADMIN_FREE_INTERVAL,
                    AdminActionCb(action=AdminAction.CFG_FREE),
                ),
                _btn(
                    tx.BTN_ADMIN_PRO_INTERVAL, AdminActionCb(action=AdminAction.CFG_PRO)
                ),
            ],
            [
                _btn(
                    tx.BTN_ADMIN_CHEAP_THRESHOLD,
                    AdminActionCb(action=AdminAction.CFG_CHEAP),
                )
            ],
            [
                _btn(
                    tx.BTN_ADMIN_FREE_AI_LIMIT,
                    AdminActionCb(action=AdminAction.CFG_AI_FREE),
                ),
                _btn(
                    tx.BTN_ADMIN_PRO_AI_LIMIT,
                    AdminActionCb(action=AdminAction.CFG_AI_PRO),
                ),
            ],
            [
                _btn(
                    tx.BTN_ADMIN_REVIEW_SAMPLE_LIMIT,
                    AdminActionCb(action=AdminAction.CFG_REVIEWS_LIMIT),
                )
            ],
            [
                _btn(
                    tx.BTN_ADMIN_ANALYSIS_MODEL,
                    AdminActionCb(action=AdminAction.CFG_ANALYSIS_MODEL),
                )
            ],
            [_btn(tx.BTN_BACK, AdminActionCb(action=AdminAction.OPEN))],
        ]
    )


def admin_config_input_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_btn(tx.BTN_BACK, AdminActionCb(action=AdminAction.CFG))]]
    )


def support_kb() -> InlineKeyboardMarkup:
    """Клавиатура для раздела поддержки."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn(tx.BTN_SUPPORT, SupportActionCb(action=SupportAction.START))],
            [_btn(tx.BTN_BACK_MENU, NavCb(action=NavAction.HOME))],
        ]
    )


def support_cancel_kb() -> InlineKeyboardMarkup:
    """Клавиатура отмены создания тикета."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn(tx.BTN_SUPPORT_CANCEL, SupportActionCb(action=SupportAction.CANCEL))]
        ]
    )


def support_media_confirmation_kb(photo_count: int = 0) -> InlineKeyboardMarkup:
    """Клавиатура подтверждения отправки тикета с медиа."""
    kb = [
        [
            _btn(
                tx.BTN_SUPPORT_SEND,
                SupportActionCb(action=SupportAction.SEND),
                style="success",
            )
        ],
        [_btn(tx.BTN_SUPPORT_ADD_MORE, SupportActionCb(action=SupportAction.ADD_MORE))],
        [_btn(tx.BTN_SUPPORT_CANCEL, SupportActionCb(action=SupportAction.CANCEL))],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def admin_support_ticket_kb(ticket_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для админа при просмотре тикета."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn(
                    tx.BTN_REPLY_TICKET,
                    SupportTicketActionCb(
                        action=SupportTicketAction.REPLY, ticket_id=ticket_id
                    ),
                    style="primary",
                ),
                _btn(
                    tx.BTN_CLOSE_TICKET,
                    SupportTicketActionCb(
                        action=SupportTicketAction.CLOSE, ticket_id=ticket_id
                    ),
                    style="danger",
                ),
            ],
        ]
    )


def support_admin_reply_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("❌ Отмена", SupportActionCb(action=SupportAction.ADMIN_CANCEL))]
        ]
    )


def admin_promo_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn(tx.BTN_ADMIN_PROMO_PRO, AdminActionCb(action=AdminAction.PROMO_PRO))],
            [
                _btn(
                    tx.BTN_ADMIN_PROMO_DISCOUNT,
                    AdminActionCb(action=AdminAction.PROMO_DISCOUNT),
                )
            ],
            [
                _btn(
                    tx.BTN_ADMIN_PROMO_DEACTIVATE,
                    AdminActionCb(action=AdminAction.PROMO_DEACTIVATE),
                )
            ],
            [_btn(tx.BTN_BACK, AdminActionCb(action=AdminAction.OPEN))],
        ]
    )


def admin_promo_list_kb(
    items: list[tuple[int, str]],
    *,
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [_btn(label, AdminPromoItemCb(promo_id=promo_id, page=page))]
        for promo_id, label in items
    ]

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(_btn("⬅️", AdminPromoPageCb(page=page - 1)))
    nav.append(_btn(f"{page + 1} / {total_pages}", NavCb(action=NavAction.NOOP)))
    if page < total_pages - 1:
        nav.append(_btn("➡️", AdminPromoPageCb(page=page + 1)))
    rows.append(nav)

    rows.append([_btn(tx.BTN_BACK, AdminActionCb(action=AdminAction.PROMO))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_promo_card_kb(*, promo_id: int, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn(
                    tx.BTN_ADMIN_PROMO_DEACTIVATE,
                    AdminPromoOffCb(promo_id=promo_id, page=page),
                    style="danger",
                )
            ],
            [_btn(tx.BTN_BACK, AdminPromoPageCb(page=page))],
        ]
    )


def admin_promo_input_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_btn(tx.BTN_BACK, AdminActionCb(action=AdminAction.PROMO))]]
    )
