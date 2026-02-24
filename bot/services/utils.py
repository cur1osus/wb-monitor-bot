from __future__ import annotations

from bot.settings import Settings


def is_admin(user_id: int, settings: Settings) -> bool:
    """Check if user is admin."""
    return user_id == settings.developer_id or user_id in settings.admin_ids_list
