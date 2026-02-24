from aiogram.filters.callback_data import CallbackData


class BackFactory(CallbackData, prefix="bk"):
    to: str


class CancelFactory(CallbackData, prefix="cn"):
    to: str
