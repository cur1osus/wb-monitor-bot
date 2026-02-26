from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.db.models import TrackModel
    from bot.services.review_analysis import ReviewInsights


TRACK_NOT_FOUND = "Трек не найден"
NO_ACCESS = "❌ Нет доступа"
SETTINGS_SUFFIX = "\n\n⚙️ Настройки:"

FEATURE_LIMIT_CHEAP_REACHED = (
    "Лимит поиска похожих товаров на сегодня исчерпан ({limit}). "
    "Попробуйте завтра или обновитесь до PRO."
)
FEATURE_LIMIT_REVIEWS_REACHED = (
    "Лимит анализа отзывов на сегодня исчерпан ({limit}). "
    "Попробуйте завтра или обновитесь до PRO."
)

DASHBOARD_TEMPLATE = (
    "🔎 <b>WB Monitor</b>\n"
    "<blockquote>Отслеживание цен и наличия на Wildberries</blockquote>\n\n"
    "Тариф: <b>{plan_badge}</b>\n"
    "Треков: <b>{used}</b> / {limit}\n"
    "Интервал проверок: каждые <b>{interval} мин</b>"
)
PLAN_BADGE_PRO = "⭐ PRO"
PLAN_BADGE_FREE = "🆓 FREE"

BTN_ADD_ITEM = "➕ Добавить товар"
BTN_MY_TRACKS = "📋 Мои треки"
BTN_PLAN = "💳 Тариф"
BTN_REFERRAL = "🤝 Реферал"
BTN_HELP = "❓ Справка"
BTN_ADMIN = "🛠 Админ панель"
BTN_BACK_MENU = "◀️ В меню"
BTN_PAUSE = "⏸ Пауза"
BTN_RESUME = "▶️ Возобновить"
BTN_REMOVE_CONFIRM = "⚠️ Да, удалить"
BTN_REMOVE_CANCEL = "↩️ Нет"
BTN_REMOVE = "🗑 Удалить"
BTN_SETTINGS = "⚙️ Настройки"
BTN_FIND_CHEAPER = "🔎 Найти дешевле"
BTN_REVIEW_ANALYSIS = "🧠 Анализ отзывов"
BTN_WITH_USAGE_TEMPLATE = "{title} ({used}/{limit})"
BTN_TARGETS = "🎯 Цель ₽ / %"
BTN_RESET_TARGET = "♻️ Сброс цели"
BTN_RESET_DROP = "♻️ Сброс падения"
BTN_SIZES = "📏 Размеры"
BTN_BACK = "◀️ Назад"
BTN_PAY_PRO = "⭐ Оплатить 150 звёзд — 30 дней Pro"
BTN_PRO_ACTIVE = "✅ Pro активен"
BTN_PRO_ACTIVE_UNTIL_DELIM = " до "
BTN_PAY_STARS = "⭐ Оплатить звёздами"
BTN_PAY_PRO_DISCOUNT = "⭐ Оплатить {amount} звёзд — скидка {percent}%"
BTN_SHARE_LINK = "📤 Поделиться ссылкой"
BTN_ADMIN_DAYS_SELECTED = "✅ {days} дн"
BTN_ADMIN_DAYS = "📊 {days} дн"
BTN_ADMIN_SETTINGS = "⚙️ Настройки бота"
BTN_ADMIN_GRANT_PRO = "🎁 Выдать PRO"
BTN_ADMIN_PROMO = "🎟 Промо ссылки"
BTN_ADMIN_FREE_INTERVAL = "⏱ FREE интервал"
BTN_ADMIN_PRO_INTERVAL = "⚡ PRO интервал"
BTN_ADMIN_CHEAP_THRESHOLD = "🔎 Порог похожести"
BTN_ADMIN_FREE_AI_LIMIT = "🆓 Лимит AI FREE"
BTN_ADMIN_PRO_AI_LIMIT = "⭐ Лимит AI PRO"
BTN_ADMIN_REVIEW_SAMPLE_LIMIT = "🧪 Лимит отзывов LLM"
BTN_ADMIN_ANALYSIS_MODEL = "🤖 Модель анализа"
BTN_ADMIN_PROMO_PRO = "🎁 Ссылка на PRO"
BTN_ADMIN_PROMO_DISCOUNT = "💸 Ссылка со скидкой"
BTN_ADMIN_PROMO_DEACTIVATE = "⛔ Деактивировать ссылку"
QTY_ON_LABEL = "📦 Остаток: вкл"
QTY_OFF_LABEL = "📦 Остаток: выкл"
REFERRAL_SHARE_TEXT = "WB Monitor — отслеживай цены на Wildberries!"

ADD_ITEM_PROMPT = (
    "📎 Отправьте ссылку на товар Wildberries или его артикул (6-12 цифр)."
)
WB_LINK_PARSE_ERROR = "❌ Не удалось распознать ссылку WB. Отправьте корректную ссылку."
TRACK_ALREADY_EXISTS = "⚠️ Вы уже отслеживаете этот товар."
TRACK_LIMIT_REACHED = "❌ Достигнут лимит треков ({limit}). Обновитесь до Pro!"
PRODUCT_FETCH_ERROR = "❌ Не удалось получить данные о товаре. Проверьте ссылку."
TRACK_ADDED_TEMPLATE = (
    "✅ Товар добавлен в отслеживание!\n\n"
    "📦 {title}\n"
    "💰 Цена: {price}\n"
    "⭐ Рейтинг: {rating}\n"
    "📦 В наличии: {in_stock}"
)
TRACK_ADDED_RATING_WITH_REVIEWS = "{rating} ({reviews} отзывов)"
TRACK_ADDED_PRICE_UNKNOWN = "не указана"
TRACK_ADDED_RATING_UNKNOWN = "не указан"
TRACK_ADDED_IN_STOCK_YES = "да"
TRACK_ADDED_IN_STOCK_NO = "нет"
TRACK_ADDED_FIND_CHEAPER_BTN = "🔎 Найти дешевле"
TRACK_ADDED_MY_TRACKS_BTN = "📦 Мои треки"
TRACK_ADDED_BACK_MENU_BTN = "◀ В меню"
NO_ACTIVE_TRACKS = "У вас нет активных треков"
INVALID_PAGE = "Недействительная страница"
REMOVE_CONFIRM = "Подтвердите удаление"
REMOVE_CANCELLED = "Удаление отменено"
TRACK_DELETED = "Трек удален"

FIND_CHEAPER_TO_LIST_BTN = "◀️ К товару"
FIND_CHEAPER_PROGRESS = "🔎 Ищу похожие товары дешевле для <b>{title}</b>..."
FIND_CHEAPER_ANSWER = "Ищу варианты..."
FIND_CHEAPER_PRICE_ERROR = "❌ Не удалось получить текущую цену товара."
FIND_CHEAPER_EMPTY = (
    "🔎 Для <b>{title}</b> не нашлось похожих товаров дешевле <b>{price} ₽</b>."
)
FIND_CHEAPER_HEADER = "🔎 Похожие товары дешевле <b>{price} ₽</b> для <b>{title}</b>"
FIND_CHEAPER_TIP = "⚠️ Сверяйте характеристики перед покупкой."

REVIEWS_ANALYSIS_ANSWER = "Анализирую отзывы..."
REVIEWS_BACK_TO_TRACK_BTN = "◀️ К товару"
REVIEWS_ANALYSIS_PROGRESS = "🧠 Анализирую развернутые отзывы для <b>{title}</b>..."
REVIEWS_ANALYSIS_FAILED = "❌ Не удалось выполнить анализ отзывов. Попробуйте позже."
REVIEWS_ANALYSIS_NO_REVIEWS = (
    "ℹ️ У этого товара пока нет отзывов. Анализировать пока нечего."
)

PLAN_TEXT = (
    "💳 <b>Ваш тариф: {plan}</b>\n\n"
    "📦 Треков: {used}/{limit}\n"
    "⏱ Интервал проверок: {interval} мин\n\n"
)
PLAN_PRO_ACTIVE = "✅ Pro активен\n"
PLAN_PRO_UPSELL = (
    "🚀 Обновитесь до <b>PRO</b> — 50 треков, проверка каждые {interval} мин!"
)

PAYMENT_TITLE = "WB Monitor Pro"
PAYMENT_DESCRIPTION = "Доступ к Pro на 30 дней"
PAYMENT_LABEL = "Pro (30 дней)"
REFERRAL_REWARD_NOTIFY = "🎉 По рефералке начислено +7 дней Pro!"
PRO_ACTIVATED = "✅ Pro активирован. Доступ продлен на 30 дней."
PRO_ACTIVATED_WITH_REFERRAL = "\n🎁 Реферальный бонус пригласившему (+7 дней) начислен."

REFERRAL_TEXT = (
    "👥 <b>Реферальная программа</b>\n\n"
    "Приглашайте друзей и получайте <b>+7 дней Pro</b> за каждую оплату!\n\n"
    "Ваша ссылка:\n<code>{ref_link}</code>"
)

HELP_TEXT = (
    "❓ <b>Помощь WB Monitor</b>\n\n"
    "/start - Главное меню\n\n"
    "Бот отслеживает цены и наличие товаров на Wildberries.\n"
    "Просто отправьте ссылку на товар или его артикул."
)

ADMIN_STATS_TEXT = (
    "🛠 <b>Админ панель</b>\n"
    "Период: <b>{days} {days_word}</b>\n\n"
    "👥 Пользователи: <b>{total_users}</b> (новых: +{new_users})\n"
    "⭐ PRO активных: <b>{pro_users}</b>\n"
    "📦 Треки: <b>{total_tracks}</b> (активных: {active_tracks}, новых: +{new_tracks})\n"
    "🔁 Проверок: <b>{checks_count}</b>\n"
    "🔔 Уведомлений: <b>{alerts_count}</b>\n"
    "🔎 Поисков дешевле: <b>{cheap_scans_count}</b>\n"
    "🧠 Анализов отзывов: <b>{reviews_scans_count}</b>"
)

ADMIN_RUNTIME_CONFIG_TEXT = (
    "⚙️ <b>Настройки бота</b>\n\n"
    "🆓 FREE интервал: <b>{free} мин</b>\n"
    "⭐ PRO интервал: <b>{pro} мин</b>\n"
    "🔎 Порог похожести: <b>{cheap}%</b>\n\n"
    "🆓 Лимит AI FREE в день: <b>{free_ai}</b>\n"
    "⭐ Лимит AI PRO в день: <b>{pro_ai}</b>\n\n"
    "🧪 Лимит отзывов в анализе (на сторону): <b>{review_limit}</b>\n"
    "🤖 Модель анализа: <code>{analysis_model}</code>\n\n"
    "Изменения применяются сразу."
)

ADMIN_FREE_PROMPT = "🆓 Введите новый интервал FREE в минутах (от 5 до 1440):"
ADMIN_PRO_PROMPT = "⭐ Введите новый интервал PRO в минутах (от 1 до 1440):"
ADMIN_CHEAP_PROMPT = "🔎 Введите порог похожести для поиска дешевле (от 10 до 95):"
ADMIN_FREE_AI_LIMIT_PROMPT = (
    "🆓 Введите лимит AI-запросов в день для FREE (от 1 до 50):"
)
ADMIN_PRO_AI_LIMIT_PROMPT = "⭐ Введите лимит AI-запросов в день для PRO (от 1 до 200):"
ADMIN_REVIEW_SAMPLE_LIMIT_PROMPT = "🧪 Введите лимит развернутых отзывов на каждую сторону (плюсы/минусы) от 10 до 200:"
ADMIN_ANALYSIS_MODEL_PROMPT = (
    "🤖 Введите ID модели для анализа (например, qwen/qwen3-32b):"
)
ADMIN_PROMO_MENU_TEXT = (
    "🎟 <b>Промо ссылки</b>\n\n"
    "Создавайте ссылки для:\n"
    "• бесплатной PRO подписки\n"
    "• скидки на оплату PRO\n\n"
    "Ссылки генерируются случайно и работают только до срока действия."
)
ADMIN_PROMO_PRO_PROMPT = (
    "🎁 Введите параметры в формате:\n"
    "<code>дни_PRO часы_жизни</code>\n\n"
    "Пример: <code>30 72</code>"
)
ADMIN_PROMO_DISCOUNT_PROMPT = (
    "💸 Введите параметры в формате:\n"
    "<code>скидка_% часы_жизни</code>\n\n"
    "Пример: <code>25 48</code>"
)
ADMIN_PROMO_DEACTIVATE_PROMPT = (
    "⛔ Отправьте промо-код или полную ссылку, которую нужно деактивировать."
)
ADMIN_FREE_INT_ERROR = "❌ Введите целое число от 5 до 1440."
ADMIN_FREE_RANGE_ERROR = "❌ Значение вне диапазона: 5..1440"
ADMIN_PRO_INT_ERROR = "❌ Введите целое число от 1 до 1440."
ADMIN_PRO_RANGE_ERROR = "❌ Значение вне диапазона: 1..1440"
ADMIN_CHEAP_INT_ERROR = "❌ Введите целое число от 10 до 95."
ADMIN_CHEAP_RANGE_ERROR = "❌ Значение вне диапазона: 10..95"
ADMIN_FREE_AI_INT_ERROR = "❌ Введите целое число от 1 до 50."
ADMIN_FREE_AI_RANGE_ERROR = "❌ Значение вне диапазона: 1..50"
ADMIN_PRO_AI_INT_ERROR = "❌ Введите целое число от 1 до 200."
ADMIN_PRO_AI_RANGE_ERROR = "❌ Значение вне диапазона: 1..200"
ADMIN_REVIEW_SAMPLE_LIMIT_INT_ERROR = "❌ Введите целое число от 10 до 200."
ADMIN_REVIEW_SAMPLE_LIMIT_RANGE_ERROR = "❌ Значение вне диапазона: 10..200"
ADMIN_MODEL_EMPTY_ERROR = "❌ Модель не должна быть пустой."
ADMIN_PROMO_PRO_FORMAT_ERROR = (
    "❌ Неверный формат. Используйте: <code>дни_PRO часы_жизни</code>."
)
ADMIN_PROMO_DISCOUNT_FORMAT_ERROR = (
    "❌ Неверный формат. Используйте: <code>скидка_% часы_жизни</code>."
)
ADMIN_PROMO_PRO_RANGE_ERROR = "❌ Дни PRO: 1..365, часы жизни ссылки: 1..720."
ADMIN_PROMO_DISCOUNT_RANGE_ERROR = "❌ Скидка: 1..90, часы жизни ссылки: 1..720."
ADMIN_PROMO_DEACTIVATE_FORMAT_ERROR = "❌ Не удалось распознать промо-код. Отправьте код или ссылку вида ?start=promo_<code>."
ADMIN_PROMO_DEACTIVATE_NOT_FOUND = "⚠️ Промо ссылка не найдена."
ADMIN_PROMO_DEACTIVATE_ALREADY = "ℹ️ Эта промо ссылка уже деактивирована."
ADMIN_INVALID_PERIOD = "Недоступный период"

ADMIN_GRANT_PRO_PROMPT = (
    "🎁 <b>Выдать PRO</b>\n\n"
    "Отправьте данные в формате:\n"
    "<code>tg_id дни</code>\n\n"
    "Пример:\n"
    "<code>123456789 30</code>"
)
ADMIN_GRANT_PRO_FORMAT_ERROR = (
    "❌ Неверный формат. Используйте: <code>tg_id дни</code> (дни от 1 до 365)."
)
ADMIN_GRANT_PRO_USER_NOT_FOUND = (
    "❌ Пользователь не найден. Он должен хотя бы один раз запустить бота (/start)."
)
ADMIN_GRANT_PRO_DONE = (
    "✅ Пользователю <code>{tg_user_id}</code> выдан PRO на <b>{days}</b> дн.\n"
    "Действует до: <b>{expires}</b>"
)
ADMIN_GRANT_PRO_USER_NOTIFY = (
    "🎉 Вам активирован PRO на <b>{days}</b> дн.\nДействует до: <b>{expires}</b>"
)

ADMIN_PROMO_CREATED_PRO = (
    "✅ Создана PRO-ссылка:\n<code>{link}</code>\n\n"
    "Дает: <b>{days}</b> дней PRO\n"
    "Действует до: <b>{expires}</b>"
)
ADMIN_PROMO_CREATED_DISCOUNT = (
    "✅ Создана скидочная ссылка:\n<code>{link}</code>\n\n"
    "Скидка: <b>{percent}%</b>\n"
    "Действует до: <b>{expires}</b>"
)
ADMIN_PROMO_DEACTIVATED = "✅ Промо ссылка деактивирована:\n<code>{code}</code>"

PROMO_INVALID_OR_EXPIRED = "⚠️ Промо ссылка недействительна или срок ее действия истек."
PROMO_ALREADY_USED = "ℹ️ Вы уже активировали эту промо ссылку ранее."
PROMO_PRO_APPLIED = "🎉 Промо активировано: вам начислено <b>{days}</b> дней PRO."
PROMO_DISCOUNT_APPLIED = (
    "🎉 Промо активировано: скидка <b>{percent}%</b> на ближайшую оплату PRO."
)
PLAN_DISCOUNT_HINT = "\n💸 Доступна скидка <b>{percent}%</b> на следующую оплату PRO."

SETTINGS_CANCEL_BTN = "❌ Отмена"
SETTINGS_TARGETS_PROMPT = (
    "🎯 Введите цель в одном сообщении:\n"
    "• Цена в ₽: <code>1500</code> или <code>1500.50</code>\n"
    "• Падение в %: <code>10%</code> или <code>0.5%</code>"
)
SETTINGS_TARGETS_ERROR = (
    "❌ Некорректный формат. Отправьте число для цены (₽) "
    "или число с символом % для падения."
)
SETTINGS_TARGETS_PRICE_GT_CURRENT = (
    "❌ Цель цены не может быть выше текущей: <b>{current} ₽</b>."
)
SETTINGS_TARGETS_DROP_RANGE_ERROR = "❌ Процент должен быть от 0.1% до 99%."
SETTINGS_TARGETS_PRICE_DONE = (
    "✅ Целевая цена для <b>{title}</b> установлена: {price} ₽"
)
SETTINGS_TARGETS_DROP_DONE = (
    "✅ Уведомление о падении цены на {drop}% для <b>{title}</b> включено."
)
SETTINGS_PRICE_RESET_DONE = "Цель цены сброшена"
SETTINGS_DROP_RESET_DONE = "Порог падения сброшен"
SETTINGS_QTY_PRO_ONLY = "⭐️ Доступно только на тарифе PRO"
SETTINGS_QTY_ANSWER = "Остаток: {state}"
SETTINGS_QTY_STATE_ON = "ВКЛ"
SETTINGS_QTY_STATE_OFF = "ВЫКЛ"
SETTINGS_NO_SIZES = "У этого товара нет размеров"
SETTINGS_SIZES_ALL_KEYWORD = "все"
SETTINGS_SIZES_NONE = "Нет"
SETTINGS_SIZES_PROMPT = (
    "📏 Доступные размеры: {sizes}\n\n"
    "Введите размеры через запятую, которые хотите отслеживать "
    "(или отправьте '0' чтобы очистить фильтр):"
)
SETTINGS_SIZES_DONE = "✅ Размеры для отслеживания обновлены: {sizes}"

START_REF_LINKED = "✅ Вы подключены по реферальной ссылке."

WORKER_EVENTS: dict[str, str] = {
    "price_target": "💸 Цена достигла цели: {price} ₽ (цель: {target} ₽)",
    "price_drop": "📉 Падение цены на {percent}%: {old} ₽ → {new} ₽",
    "in_stock": "✅ Товар снова в наличии",
    "stock_changed": "📦 Остаток изменился {direction}: {old} → {new}",
    "sizes_appeared": "📏 Появились размеры: {sizes}",
    "sizes_gone": "📏 Исчезли размеры: {sizes}",
    "paused_error": "⚠️ Трек #{id} поставлен на паузу из-за ошибок.\n{title}",
}
WORKER_NOTIFY_TEMPLATE = "🔔 <b>{title}</b>\n{event}\n{url}"

REVIEW_ANALYSIS_NO_API_KEY = "Сервис анализа временно недоступен. Попробуйте позже."
REVIEW_ANALYSIS_NO_MODEL = "Сервис анализа временно недоступен. Попробуйте позже."
REVIEW_ANALYSIS_NO_DETAILED = "Не удалось найти развернутые отзывы для анализа."
REVIEW_ANALYSIS_NO_CARD = "Не удалось получить данные карточки товара."
REVIEW_ANALYSIS_NO_FEEDBACKS = "Не удалось получить отзывы от Wildberries."
REVIEW_ANALYSIS_LLM_EMPTY = "Не удалось получить результат анализа. Попробуйте позже."
REVIEW_ANALYSIS_LLM_FORBIDDEN = "Сервис анализа временно недоступен. Попробуйте позже."
REVIEW_ANALYSIS_RATE_LIMIT_WAIT = (
    "Сейчас высокая нагрузка на сервис анализа. Подождите {wait} и попробуйте снова."
)
REVIEW_ANALYSIS_RATE_LIMIT_SOON = (
    "Сейчас высокая нагрузка на сервис анализа. Подождите немного и попробуйте снова."
)
REVIEW_ANALYSIS_TASK_PROMPT = (
    "Выдели 3 сильных качества и 3 слабых качества товара на основе отзывов. "
    "Если данных для слабых качеств недостаточно, верни меньше пунктов или пустой список."
)
REVIEW_ANALYSIS_PROS_PREFIX = "Плюсы"
REVIEW_ANALYSIS_CONS_PREFIX = "Минусы"
REVIEW_ANALYSIS_COMMENT_PREFIX = "Комментарий"
REVIEW_ANALYSIS_EMPTY_MARKERS = {"нет", "-", "—"}
REVIEW_ANALYSIS_SYSTEM_PROMPT = (
    "Ты продуктовый аналитик. "
    "На основе отзывов выдели ключевые сильные и слабые качества товара. "
    "Верни только JSON без пояснений в формате: "
    '{"strengths": ["..."], "weaknesses": ["..."]}. '
    "Ограничение: максимум 3 пункта в каждом списке."
)
REVIEW_ANALYSIS_USER_PROMPT_PREFIX = (
    "Проанализируй отзывы и верни итог. Данные для анализа:\n"
)
TIME_SECONDS_SUFFIX = "сек"
TIME_MINUTES_SUFFIX = "мин"
REVIEW_ANALYSIS_SAMPLES_LINE = (
    "<blockquote>Развернутых отзывов (взято/всего): "
    "+{pos_used}/{pos_total} / -{neg_used}/{neg_total}</blockquote>"
)
REVIEW_ANALYSIS_LIMIT_NOTE_BOTH = (
    "ℹ️ Для анализа взято не более {limit} положительных "
    "и {limit} отрицательных отзывов."
)
REVIEW_ANALYSIS_LIMIT_NOTE_POS = (
    "ℹ️ Для анализа взято не более {limit} положительных отзывов."
)
REVIEW_ANALYSIS_LIMIT_NOTE_NEG = (
    "ℹ️ Для анализа взято не более {limit} отрицательных отзывов."
)


def review_insights_text(track_title: str, insights: "ReviewInsights") -> str:
    pos_used = max(0, int(getattr(insights, "positive_samples", 0)))
    neg_used = max(0, int(getattr(insights, "negative_samples", 0)))
    pos_total_raw = int(getattr(insights, "positive_total", 0) or 0)
    neg_total_raw = int(getattr(insights, "negative_total", 0) or 0)
    pos_total = max(pos_used, pos_total_raw)
    neg_total = max(neg_used, neg_total_raw)
    limit = max(1, int(getattr(insights, "sample_limit_per_side", 50) or 50))

    pos_capped = pos_total > pos_used and pos_used >= limit
    neg_capped = neg_total > neg_used and neg_used >= limit

    lines = [
        f"🧠 <b>Анализ отзывов</b> для <b>{escape(track_title)}</b>",
        REVIEW_ANALYSIS_SAMPLES_LINE.format(
            pos_used=pos_used,
            pos_total=pos_total,
            neg_used=neg_used,
            neg_total=neg_total,
        ),
        "",
        "✅ <b>Сильные качества:</b>",
    ]

    if insights.strengths:
        for idx, item in enumerate(insights.strengths, start=1):
            lines.append(f"{idx}. {escape(item)}")
    else:
        lines.append("1. Не удалось выделить по доступным отзывам.")

    lines.append("")
    lines.append("⚠️ <b>Слабые качества:</b>")

    if insights.weaknesses:
        for idx, item in enumerate(insights.weaknesses, start=1):
            lines.append(f"{idx}. {escape(item)}")
    else:
        lines.append("Нет явных повторяющихся минусов в развернутых отзывах.")

    if pos_capped and neg_capped:
        lines.append("")
        lines.append(REVIEW_ANALYSIS_LIMIT_NOTE_BOTH.format(limit=limit))
    elif pos_capped:
        lines.append("")
        lines.append(REVIEW_ANALYSIS_LIMIT_NOTE_POS.format(limit=limit))
    elif neg_capped:
        lines.append("")
        lines.append(REVIEW_ANALYSIS_LIMIT_NOTE_NEG.format(limit=limit))

    return "\n".join(lines)


def admin_stats_text(stats: object) -> str:
    days = int(getattr(stats, "days"))
    days_word = "день" if days == 1 else ("дня" if days in {2, 3, 4} else "дней")
    return ADMIN_STATS_TEXT.format(
        days=days,
        days_word=days_word,
        total_users=getattr(stats, "total_users"),
        new_users=getattr(stats, "new_users"),
        pro_users=getattr(stats, "pro_users"),
        total_tracks=getattr(stats, "total_tracks"),
        active_tracks=getattr(stats, "active_tracks"),
        new_tracks=getattr(stats, "new_tracks"),
        checks_count=getattr(stats, "checks_count"),
        alerts_count=getattr(stats, "alerts_count"),
        cheap_scans_count=getattr(stats, "cheap_scans_count", 0),
        reviews_scans_count=getattr(stats, "reviews_scans_count", 0),
    )


def admin_runtime_config_text(cfg: object) -> str:
    return ADMIN_RUNTIME_CONFIG_TEXT.format(
        free=getattr(cfg, "free_interval_min"),
        pro=getattr(cfg, "pro_interval_min"),
        cheap=getattr(cfg, "cheap_match_percent"),
        free_ai=getattr(cfg, "free_daily_ai_limit"),
        pro_ai=getattr(cfg, "pro_daily_ai_limit"),
        review_limit=getattr(cfg, "review_sample_limit_per_side"),
        analysis_model=getattr(cfg, "analysis_model", "—"),
    )


def dashboard_text(
    *,
    plan_badge: str,
    used: int,
    limit: int,
    interval: int,
) -> str:
    return DASHBOARD_TEMPLATE.format(
        plan_badge=plan_badge,
        used=used,
        limit=limit,
        interval=interval,
    )


def button_with_usage(title: str, *, used: int, limit: int) -> str:
    safe_used = max(0, used)
    safe_limit = max(1, limit)
    return BTN_WITH_USAGE_TEMPLATE.format(title=title, used=safe_used, limit=safe_limit)


def format_track_text(track: "TrackModel") -> str:
    status = "🟢 Активен" if track.is_active else "⏸ Пауза"
    current_price = (
        f"<b>{track.last_price} ₽</b>" if track.last_price is not None else "—"
    )
    rating = (
        f"{track.last_rating:.1f} ({track.last_reviews or 0} отзывов)"
        if track.last_rating is not None
        else "—"
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
        f"<blockquote>Отслеживание цены и наличия товара</blockquote>\n\n"
        f"🔹 Артикул: <code>{track.wb_item_id}</code>\n"
        f"💰 Текущая цена: {current_price}\n"
        f"⭐ Рейтинг: {rating}\n"
        f"🏪 В наличии: {in_stock}\n"
        f"📊 Остаток: {qty} шт\n"
        f"🎯 Цель цены: {target_price}\n"
        f"📉 Порог падения: {drop}\n"
        f"{sizes_line}"
        f"📡 Статус: {status}"
    )
