from __future__ import annotations

from enum import Enum

from aiogram.filters.callback_data import CallbackData

from bot.enums import CompareMode, PlanOfferCode, SearchMode


class CallbackEnum(str, Enum):
    pass


class NavAction(CallbackEnum):
    ADD = "add"
    CANCEL = "cancel"
    HELP = "help"
    HOME = "home"
    LIST = "list"
    NOOP = "noop"
    PLAN = "plan"
    REF = "ref"


class TrackAction(CallbackEnum):
    BACK = "back"
    CHEAP = "cheap"
    PRICE_FLUCTUATION = "price_fluctuation"
    PAUSE = "pause"
    QTY = "qty"
    REMOVE = "remove"
    REMOVE_NO = "remove_no"
    REMOVE_YES = "remove_yes"
    RESUME = "resume"
    REVIEWS = "reviews"
    SETTINGS = "settings"
    SIZES = "sizes"
    SIZES_APPLY = "sizes_apply"
    SIZES_CLEAR = "sizes_clear"
    STOCK = "stock"


class QuickAction(CallbackEnum):
    ADD = "add"
    PREVIEW = "preview"
    REVIEWS = "reviews"
    SEARCH = "search"


class CompareAction(CallbackEnum):
    OPEN = "open"


class PaymentMethod(CallbackEnum):
    CARD = "card"
    CHOICE = "choice"
    STARS = "stars"


class SupportAction(CallbackEnum):
    ADD_MORE = "add_more"
    ADMIN_CANCEL = "admin_cancel"
    CANCEL = "cancel"
    SEND = "send"
    START = "start"


class SupportTicketAction(CallbackEnum):
    CLOSE = "close"
    REPLY = "reply"


class AdminAction(CallbackEnum):
    CFG = "cfg"
    CFG_AI_FREE = "cfg_ai_free"
    CFG_AI_PRO = "cfg_ai_pro"
    CFG_ANALYSIS_MODEL = "cfg_analysis_model"
    CFG_CHEAP = "cfg_cheap"
    CFG_FREE = "cfg_free"
    CFG_PRO = "cfg_pro"
    CFG_REVIEWS_LIMIT = "cfg_reviews_limit"
    GRANTPRO = "grantpro"
    OPEN = "open"
    PROMO = "promo"
    PROMO_DEACTIVATE = "promo_deactivate"
    PROMO_DISCOUNT = "promo_discount"
    PROMO_PRO = "promo_pro"


class NavCb(CallbackData, prefix="nav"):
    action: NavAction


class TrackActionCb(CallbackData, prefix="trk"):
    action: TrackAction
    track_id: int


class TrackPageCb(CallbackData, prefix="tpg"):
    page: int


class TrackPagePickerCb(CallbackData, prefix="tpk"):
    track_id: int
    current_page: int
    offset: int


class TrackModeCb(CallbackData, prefix="tmd"):
    mode: SearchMode
    track_id: int


class TrackSizeSelectCb(CallbackData, prefix="tsz"):
    track_id: int
    size_idx: int


class QuickActionCb(CallbackData, prefix="qck"):
    action: QuickAction
    wb_item_id: int


class QuickModeCb(CallbackData, prefix="qmd"):
    mode: SearchMode
    wb_item_id: int


class CompareActionCb(CallbackData, prefix="cmp"):
    action: CompareAction


class CompareModeCb(CallbackData, prefix="cpm"):
    mode: CompareMode


class PlanOfferCb(CallbackData, prefix="plo"):
    offer_code: PlanOfferCode


class PaymentActionCb(CallbackData, prefix="pay"):
    method: PaymentMethod
    offer_code: PlanOfferCode


class SupportActionCb(CallbackData, prefix="sup"):
    action: SupportAction


class SupportTicketActionCb(CallbackData, prefix="sut"):
    action: SupportTicketAction
    ticket_id: int


class AdminActionCb(CallbackData, prefix="adm"):
    action: AdminAction


class AdminStatsCb(CallbackData, prefix="ads"):
    days: int


class AdminPromoPageCb(CallbackData, prefix="app"):
    page: int


class AdminPromoItemCb(CallbackData, prefix="api"):
    promo_id: int
    page: int


class AdminPromoOffCb(CallbackData, prefix="apo"):
    promo_id: int
    page: int
