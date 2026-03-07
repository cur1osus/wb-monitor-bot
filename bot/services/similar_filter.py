"""similar_filter.py — helpers for finding similar/cheaper products.

Moved from bot.handlers.wb_monitor to keep that module thin.
"""
from __future__ import annotations

import asyncio
import logging
import re
from decimal import Decimal
from urllib.parse import quote_plus
from typing import TYPE_CHECKING

from aiohttp import ClientSession

from bot.db.redis import WbSimilarItemRD
from bot.services.wb_client import (
    WbSimilarProduct,
    WB_HTTP_HEADERS,
    WB_HTTP_PROXY,
    fetch_product,
)

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

# ─── Color matching ───────────────────────────────────────────────────────────

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


def color_groups_from_card(colors: list[str] | None) -> set[str]:
    if not colors:
        return set()
    return _extract_color_groups(" ".join(colors))


# Keep the private alias for internal use
_color_groups_from_card = color_groups_from_card


# ─── Numeric token matching ───────────────────────────────────────────────────

def _extract_numeric_tokens(text: str) -> set[str]:
    return set(re.findall(r"\b\d{1,4}\b", text or ""))


def filter_candidates_by_numeric_tokens(
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


# Keep private alias used in handler imports that may still use old name
_filter_candidates_by_numeric_tokens = filter_candidates_by_numeric_tokens


# ─── Brand helpers ────────────────────────────────────────────────────────────

def _normalize_brand(brand: str | None) -> str:
    return (brand or "").strip().lower()


def _is_same_brand(base_brand: str | None, candidate_brand: str | None) -> bool:
    b = _normalize_brand(base_brand)
    c = _normalize_brand(candidate_brand)
    return bool(b and c and b == c)


def sort_by_brand_then_price(
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


_sort_by_brand_then_price = sort_by_brand_then_price


# ─── Live filter ─────────────────────────────────────────────────────────────

async def live_filter_cheaper_in_stock(
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
    base_color_groups = color_groups_from_card(base_colors)

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

    async def _fetch_one(item: WbSimilarProduct) -> object:
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

        if (
            base_subject_id is not None
            and snap.subject_id is not None
            and snap.subject_id != base_subject_id
        ):
            reason_counts["subject_mismatch"] += 1
            continue

        if (
            base_kind_id is not None
            and snap.kind_id is not None
            and snap.kind_id != base_kind_id
        ):
            reason_counts["kind_mismatch"] += 1
            continue

        item_color_groups = color_groups_from_card(snap.colors)
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

    out = sort_by_brand_then_price(out, base_brand=base_brand)[:limit]

    if log_prefix:
        logger.info(
            "%s live-filter stats: total=%s accepted=%s fetch_error=%s "
            "no_snapshot_or_price=%s out_of_stock=%s not_cheaper=%s "
            "subject_mismatch=%s kind_mismatch=%s color_mismatch=%s",
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


# Private alias for backward compatibility during transition
_live_filter_cheaper_in_stock = live_filter_cheaper_in_stock


# ─── Loose alternatives search ────────────────────────────────────────────────

async def search_wb_loose_alternatives(
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
        item_url = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"

        out.append(
            WbSimilarItemRD(
                wb_item_id=nm_id,
                title=title,
                price=str(price),
                url=item_url,
            )
        )

    out.sort(key=lambda item: Decimal(item.price))
    if max_price is not None:
        cheaper = [item for item in out if Decimal(item.price) < max_price]
        if cheaper:
            return cheaper[:limit]
    return out[:limit]


_search_wb_loose_alternatives = search_wb_loose_alternatives
