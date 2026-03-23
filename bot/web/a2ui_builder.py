from __future__ import annotations
from typing import TYPE_CHECKING
from decimal import Decimal

if TYPE_CHECKING:
    from bot.db.models import TrackModel, MonitorUserModel

def build_dashboard_a2ui(user: MonitorUserModel, tracks: list[TrackModel]) -> dict:
    """
    Build A2UI JSON for the dashboard.
    A2UI follows a declarative format with components and their properties.
    """
    
    # Dashboard Header
    components = [
        {
            "id": "header",
            "type": "Header",
            "properties": {
                "title": f"Привет, {user.first_name or user.username or 'Пользователь'}!",
                "subtitle": f"Ваш тариф: {user.plan.upper()}",
                "icon": "👋"
            }
        },
        {
            "id": "stats",
            "type": "StatsRow",
            "properties": {
                "items": [
                    {"label": "Всего товаров", "value": str(len(tracks)), "icon": "📦"},
                    {"label": "Активных", "value": str(len([t for t in tracks if t.is_active])), "icon": "🔔"}
                ]
            }
        }
    ]

    # Track List
    track_cards = []
    for track in tracks:
        price_text = f"{track.last_price} ₽" if track.last_price else "Нет цены"
        stock_text = "В наличии" if track.last_in_stock else "Нет в наличии"
        
        # WB image URL structure: https://basket-XX.wbbasket.ru/volYY/partZZ/AA/images/big/1.webp
        # But we don't have basket/vol info in TrackModel easily available here without more logic.
        # We'll use a placeholder or just title for now.
        
        card = {
            "id": f"track_{track.id}",
            "type": "Card",
            "properties": {
                "title": track.title,
                "description": f"{price_text} • {stock_text}",
                "image_url": f"https://www.wildberries.ru/catalog/{track.wb_item_id}/detail.aspx", # Placeholder link
                "metadata": {
                    "id": track.id,
                    "wb_item_id": track.wb_item_id,
                    "is_active": track.is_active
                },
                "actions": [
                    {
                        "id": f"toggle_{track.id}",
                        "label": "Выключить" if track.is_active else "Включить",
                        "type": "button",
                        "style": "secondary" if track.is_active else "primary",
                        "event": "toggle_track"
                    },
                    {
                        "id": f"open_{track.id}",
                        "label": "Открыть на WB",
                        "type": "link",
                        "url": track.url
                    }
                ]
            }
        }
        track_cards.append(card)

    if not track_cards:
        components.append({
            "id": "empty_state",
            "type": "EmptyState",
            "properties": {
                "title": "У вас пока нет товаров",
                "message": "Добавьте товары в боте, чтобы они появились здесь.",
                "icon": "🔍"
            }
        })
    else:
        components.append({
            "id": "track_list",
            "type": "Grid",
            "properties": {
                "columns": 1,
                "gap": "16px"
            },
            "children": track_cards
        })

    return {
        "version": "0.8",
        "ui": {
            "root": "dashboard_container",
            "components": [
                {
                    "id": "dashboard_container",
                    "type": "Container",
                    "properties": {
                        "padding": "16px",
                        "background": "transparent"
                    },
                    "children": components
                }
            ]
        }
    }
