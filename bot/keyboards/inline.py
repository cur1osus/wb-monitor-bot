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

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.db.models import TrackModel
from bot.services.config import FREE_INTERVAL, FREE_LIMIT, PRO_INTERVAL, PRO_LIMIT
from bot import text as tx


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _btn(
    text: str,
    callback_data: str,
    style: str | None = None,
) -> InlineKeyboardButton:
    """Shorthand для callback-кнопки с опциональным style (Bot API 9.4)."""
    return InlineKeyboardButton(text=text, callback_data=callback_data, style=style)


# ─── Dashboard ────────────────────────────────────────────────────────────────


def dashboard_text(
    plan: str,
    used: int,
    *,
    free_interval_min: int = FREE_INTERVAL,
    pro_interval_min: int = PRO_INTERVAL,
) -> str:
    limit = PRO_LIMIT if plan == "pro" else FREE_LIMIT
    interval = pro_interval_min if plan == "pro" else free_interval_min
    plan_badge = tx.PLAN_BADGE_PRO if plan == "pro" else tx.PLAN_BADGE_FREE
    return tx.dashboard_text(
        plan_badge=plan_badge,
        used=used,
        limit=limit,
        interval=interval,
    )


def dashboard_kb(is_admin: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            # primary — синий (главное действие)
            _btn(tx.BTN_ADD_ITEM, "wbm:add:0", style="primary"),
            _btn(tx.BTN_MY_TRACKS, "wbm:list:0"),
        ],
        [
            _btn(tx.BTN_PLAN, "wbm:plan:0"),
            _btn(tx.BTN_REFERRAL, "wbm:ref:0"),
        ],
        [
            _btn(tx.BTN_HELP, "wbm:help:0"),
        ],
    ]
    if is_admin:
        rows.append(
            [
                _btn(tx.BTN_ADMIN, "wbm:admin:0"),
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_dashboard_kb(is_admin: bool) -> InlineKeyboardMarkup:
    rows = [[_btn(tx.BTN_BACK_MENU, "wbm:home:0")]]
    if is_admin:
        rows.append([_btn(tx.BTN_ADMIN, "wbm:admin:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def add_item_prompt_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_btn(tx.SETTINGS_CANCEL_BTN, "wbm:cancel:0", style="danger")]]
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
        action_btn = _btn(tx.BTN_PAUSE, f"wbm:pause:{track.id}")
    else:
        # success — зелёный «Возобновить»
        action_btn = _btn(tx.BTN_RESUME, f"wbm:resume:{track.id}", style="success")

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(_btn("⬅️", f"wbm:page:{page - 1}"))
    nav.append(_btn(f"{page + 1} / {total}", "wbm:noop:0"))
    if page < total - 1:
        nav.append(_btn("➡️", f"wbm:page:{page + 1}"))

    if confirm_remove:
        top_rows = [
            [
                _btn(
                    tx.BTN_REMOVE_CONFIRM,
                    f"wbm:remove_yes:{track.id}",
                    style="danger",
                ),
                _btn(tx.BTN_REMOVE_CANCEL, f"wbm:remove_no:{track.id}"),
            ]
        ]
    else:
        top_rows = [[_btn(tx.BTN_REMOVE, f"wbm:remove:{track.id}", style="danger")]]

    return InlineKeyboardMarkup(
        inline_keyboard=[
            *top_rows,
            [
                action_btn,
                _btn(tx.BTN_SETTINGS, f"wbm:settings:{track.id}"),
            ],
            [_btn(cheap_btn_text or tx.BTN_FIND_CHEAPER, f"wbm:cheap:{track.id}")],
            [
                _btn(
                    reviews_btn_text or tx.BTN_REVIEW_ANALYSIS,
                    f"wbm:reviews:{track.id}",
                )
            ],
            nav,
            [_btn(tx.BTN_BACK_MENU, "wbm:home:0")],
        ]
    )


def settings_kb(
    track_id: int,
    has_sizes: bool = True,
    pro_plan: bool = False,
    qty_on: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            _btn(tx.BTN_TARGETS, f"wbm:targets:{track_id}", style="primary"),
        ],
        [
            _btn(tx.BTN_RESET_TARGET, f"wbm:price_reset:{track_id}"),
            _btn(tx.BTN_RESET_DROP, f"wbm:drop_reset:{track_id}"),
        ],
    ]
    if pro_plan:
        qty_style = "success" if qty_on else None
        qty_label = tx.QTY_ON_LABEL if qty_on else tx.QTY_OFF_LABEL
        rows.append([_btn(qty_label, f"wbm:qty:{track_id}", style=qty_style)])
    if has_sizes:
        rows.append([_btn(tx.BTN_SIZES, f"wbm:sizes:{track_id}")])
    rows.extend(
        [
            [_btn(tx.BTN_BACK, f"wbm:back:{track_id}")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ─── Plan / Payment ───────────────────────────────────────────────────────────


def plan_kb(is_pro: bool, expires_str: str | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    if not is_pro:
        rows.append(
            [
                # success — зелёный для кнопки оплаты (Bot API 9.4)
                InlineKeyboardButton(
                    text=tx.BTN_PAY_PRO,
                    callback_data="wbm:pay:stars",
                    style="success",
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
                    callback_data="wbm:noop:0",
                    style="success",
                )
            ]
        )

    rows.append([_btn(tx.BTN_BACK_MENU, "wbm:home:0")])
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
            [_btn(tx.BTN_BACK_MENU, "wbm:home:0")],
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
                _btn(_label(1), "wbm:admin:stats:1"),
                _btn(_label(7), "wbm:admin:stats:7"),
                _btn(_label(14), "wbm:admin:stats:14"),
                _btn(_label(30), "wbm:admin:stats:30"),
            ],
            [_btn(tx.BTN_ADMIN_SETTINGS, "wbm:admin:cfg")],
            [_btn(tx.BTN_ADMIN_GRANT_PRO, "wbm:admin:grantpro", style="success")],
            [_btn(tx.BTN_BACK_MENU, "wbm:home:0")],
        ]
    )


def admin_grant_pro_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn(tx.BTN_BACK, "wbm:admin:0")],
        ]
    )


def admin_config_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn(tx.BTN_ADMIN_FREE_INTERVAL, "wbm:admin:cfg:free"),
                _btn(tx.BTN_ADMIN_PRO_INTERVAL, "wbm:admin:cfg:pro"),
            ],
            [_btn(tx.BTN_ADMIN_CHEAP_THRESHOLD, "wbm:admin:cfg:cheap")],
            [
                _btn(tx.BTN_ADMIN_FREE_AI_LIMIT, "wbm:admin:cfg:ai_free"),
                _btn(tx.BTN_ADMIN_PRO_AI_LIMIT, "wbm:admin:cfg:ai_pro"),
            ],
            [_btn(tx.BTN_ADMIN_REVIEW_SAMPLE_LIMIT, "wbm:admin:cfg:reviews_limit")],
            [_btn(tx.BTN_ADMIN_ANALYSIS_MODEL, "wbm:admin:cfg:analysis_model")],
            [_btn(tx.BTN_BACK, "wbm:admin:0")],
        ]
    )


def admin_config_input_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn(tx.BTN_BACK, "wbm:admin:cfg")]])
