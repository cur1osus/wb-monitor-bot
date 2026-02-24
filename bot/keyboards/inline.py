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

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.db.models import TrackModel
from bot.services.config import FREE_INTERVAL, FREE_LIMIT, PRO_INTERVAL, PRO_LIMIT


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _btn(
    text: str,
    callback_data: str,
    style: str | None = None,
) -> InlineKeyboardButton:
    """Shorthand для callback-кнопки с опциональным style (Bot API 9.4)."""
    return InlineKeyboardButton(text=text, callback_data=callback_data, style=style)


# ─── Dashboard ────────────────────────────────────────────────────────────────


def dashboard_text(plan: str, used: int) -> str:
    limit = PRO_LIMIT if plan == "pro" else FREE_LIMIT
    interval = PRO_INTERVAL if plan == "pro" else FREE_INTERVAL
    plan_badge = "⭐ PRO" if plan == "pro" else "🆓 FREE"
    return (
        "🔎 <b>WB Monitor</b>\n"
        "<blockquote>Цены берутся из API — без персональных скидок и кошелька WB</blockquote>\n\n"
        f"Тариф: <b>{plan_badge}</b>\n"
        f"Треков: <b>{used}</b> / {limit}\n"
        f"Интервал проверок: каждые <b>{interval} мин</b>"
    )


def dashboard_kb(is_admin: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            # primary — синий (главное действие)
            _btn("➕ Добавить товар", "wbm:add:0", style="primary"),
            _btn("📋 Мои треки", "wbm:list:0"),
        ],
        [
            _btn("💳 Тариф", "wbm:plan:0"),
            _btn("🤝 Реферал", "wbm:ref:0"),
        ],
        [
            _btn("❓ Справка", "wbm:help:0"),
        ],
    ]
    if is_admin:
        rows.append(
            [
                _btn("🛠 Админ панель", "wbm:admin:0"),
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_dashboard_kb(is_admin: bool) -> InlineKeyboardMarkup:
    rows = [[_btn("◀️ В меню", "wbm:home:0")]]
    if is_admin:
        rows.append([_btn("🛠 Админ панель", "wbm:admin:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def add_item_prompt_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_btn("❌ Отмена", "wbm:cancel:0", style="danger")]]
    )


# ─── Track view ───────────────────────────────────────────────────────────────


def format_track_text(track: TrackModel) -> str:
    status = "🟢 Активен" if track.is_active else "⏸ Пауза"
    current_price = (
        f"<b>{track.last_price} ₽</b>" if track.last_price is not None else "—"
    )
    target_price = f"{track.target_price} ₽" if track.target_price is not None else "—"
    drop = (
        f"{track.target_drop_percent}%"
        if track.target_drop_percent is not None
        else "—"
    )
    qty = str(track.last_qty) if track.last_qty is not None else "—"
    in_stock = "✅ Есть" if track.last_in_stock else "❌ Нет"
    sizes_line = ""
    if track.watch_sizes:
        sizes_line = f"📏 Размеры: {', '.join(track.watch_sizes)}\n"

    return (
        f"📦 <b>{track.title}</b>\n"
        f"<blockquote>Цены из API — без персональных скидок и кошелька WB</blockquote>\n\n"
        f"🔹 Артикул: <code>{track.wb_item_id}</code>\n"
        f"💰 Текущая цена: {current_price}\n"
        f"🏪 В наличии: {in_stock}\n"
        f"📊 Остаток: {qty} шт\n"
        f"🎯 Цель цены: {target_price}\n"
        f"📉 Порог падения: {drop}\n"
        f"{sizes_line}"
        f"⏱ Интервал: {track.check_interval_min} мин\n"
        f"📡 Статус: {status}"
    )


def paged_track_kb(track: TrackModel, page: int, total: int) -> InlineKeyboardMarkup:
    if track.is_active:
        action_btn = _btn("⏸ Пауза", f"wbm:pause:{track.id}")
    else:
        # success — зелёный «Возобновить»
        action_btn = _btn("▶️ Возобновить", f"wbm:resume:{track.id}", style="success")

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(_btn("⬅️", f"wbm:page:{page - 1}"))
    nav.append(_btn(f"{page + 1} / {total}", "wbm:noop:0"))
    if page < total - 1:
        nav.append(_btn("➡️", f"wbm:page:{page + 1}"))

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                action_btn,
                _btn("⚙️ Настройки", f"wbm:settings:{track.id}"),
            ],
            [
                _btn("🔎 Найти дешевле", f"wbm:cheap:{track.id}"),
            ],
            [
                # danger — красный для удаления (Bot API 9.4)
                _btn("🗑 Удалить", f"wbm:remove:{track.id}", style="danger"),
            ],
            nav,
            [_btn("◀️ В меню", "wbm:home:0")],
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
            _btn("🎯 Цель цены", f"wbm:price:{track_id}", style="primary"),
            _btn("📉 Падение %", f"wbm:drop:{track_id}", style="primary"),
        ],
    ]
    if pro_plan:
        qty_style = "success" if qty_on else None
        qty_label = "📦 Остаток: вкл" if qty_on else "📦 Остаток: выкл"
        rows.append([_btn(qty_label, f"wbm:qty:{track_id}", style=qty_style)])
    if has_sizes:
        rows.append([_btn("📏 Размеры", f"wbm:sizes:{track_id}")])
    rows.extend(
        [
            [_btn("◀️ Назад", f"wbm:back:{track_id}")],
            [_btn("❌ Отмена", "wbm:cancel:0", style="danger")],
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
                    text="⭐ Оплатить 150 звёзд — 30 дней Pro",
                    callback_data="wbm:pay:stars",
                    style="success",
                )
            ]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"✅ Pro активен{' до ' + expires_str if expires_str else ''}",
                    callback_data="wbm:noop:0",
                    style="success",
                )
            ]
        )

    rows.append([_btn("◀️ В меню", "wbm:home:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def invoice_kb() -> InlineKeyboardMarkup:
    """Клавиатура внутри инвойса — pay=True автоматически делает кнопку зелёной."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⭐ Оплатить звёздами", pay=True)]]
    )


def ref_kb(ref_link: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📤 Поделиться ссылкой",
                    url=f"https://t.me/share/url?url={ref_link}&text=WB Monitor — отслеживай цены на Wildberries!",
                )
            ],
            [_btn("◀️ В меню", "wbm:home:0")],
        ]
    )


# ─── Admin ───────────────────────────────────────────────────────────────────


def admin_panel_kb(selected_days: int | None = None) -> InlineKeyboardMarkup:
    def _label(days: int) -> str:
        return f"✅ {days} дн" if selected_days == days else f"📊 {days} дн"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn(_label(7), "wbm:admin:stats:7"),
                _btn(_label(14), "wbm:admin:stats:14"),
                _btn(_label(30), "wbm:admin:stats:30"),
            ],
            [_btn("🎁 Выдать PRO", "wbm:admin:grantpro", style="success")],
            [_btn("◀️ В меню", "wbm:home:0")],
        ]
    )


def admin_grant_pro_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("◀️ Назад", "wbm:admin:0")],
        ]
    )
