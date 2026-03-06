from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass

from aiohttp import ClientSession

from bot.services.wb_client import WB_HTTP_HEADERS, WB_HTTP_PROXY, WbProductSnapshot

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CompareResult:
    winner_id: int
    ranking: list[int]
    reason: str


async def compare_products_with_llm(
    *,
    products: list[WbProductSnapshot],
    api_key: str,
    model: str,
    api_base_url: str,
) -> CompareResult:
    api_key = (api_key or "").strip()
    model = (model or "").strip()

    if len(products) < 2:
        raise ValueError("Need at least 2 products")

    # Fallback (if no model/key or any LLM error)
    fallback = _fallback_compare(products)
    if not api_key or not model:
        return fallback

    endpoint = _chat_completions_url(api_base_url)
    payload = {
        "products": [
            {
                "id": p.wb_item_id,
                "title": p.title,
                "price": str(p.price) if p.price is not None else None,
                "rating": float(p.rating) if p.rating is not None else None,
                "reviews": p.reviews,
                "in_stock": p.in_stock,
                "sizes_count": len(p.sizes or []),
                "brand": p.brand,
            }
            for p in products
        ],
        "task": (
            "Сравни товары и выбери лучший универсальный вариант для покупки. "
            "Учитывай цену, рейтинг, число отзывов и наличие. "
            "Верни JSON строго формата: "
            '{"winner_id":123,"ranking":[123,456],"reason":"..."}'
        ),
    }

    body = {
        "model": model,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты аналитик e-commerce. Отвечай строго JSON, без markdown."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False),
            },
        ],
    }

    try:
        async with ClientSession(headers=WB_HTTP_HEADERS) as session:
            async with session.post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}"},
                json=body,
                timeout=20,
                proxy=WB_HTTP_PROXY,
            ) as resp:
                if resp.status != 200:
                    logger.warning("product-compare llm status=%s", resp.status)
                    return fallback
                data = await resp.json(content_type=None)
    except Exception:
        logger.exception("product-compare llm request failed")
        return fallback

    _log_token_usage(data)

    parsed = _parse_compare_result(data)
    if not parsed:
        return fallback

    valid_ids = {p.wb_item_id for p in products}
    ranking = [i for i in parsed.ranking if i in valid_ids]
    if not ranking:
        return fallback

    winner_id = parsed.winner_id if parsed.winner_id in valid_ids else ranking[0]
    if winner_id not in ranking:
        ranking.insert(0, winner_id)

    return CompareResult(winner_id=winner_id, ranking=ranking, reason=parsed.reason)


def _fallback_compare(products: list[WbProductSnapshot]) -> CompareResult:
    def score(p: WbProductSnapshot) -> float:
        price_score = 0.0
        if p.price is not None:
            # below 10k rub gets better baseline, still relative ranking works
            price_score = max(0.0, 40.0 - float(p.price) / 250.0)
        rating_score = float(p.rating or 0) * 10.0
        reviews_score = min(20.0, math.log10((p.reviews or 0) + 1) * 8.0)
        stock_score = 10.0 if p.in_stock else 0.0
        return price_score + rating_score + reviews_score + stock_score

    ranked = sorted(products, key=score, reverse=True)
    winner = ranked[0]
    reason = "Лучший баланс цены, рейтинга, количества отзывов и наличия."
    return CompareResult(
        winner_id=winner.wb_item_id,
        ranking=[p.wb_item_id for p in ranked],
        reason=reason,
    )


def _chat_completions_url(base_url: str) -> str:
    normalized = (base_url or "").strip().rstrip("/")
    if not normalized:
        normalized = "https://litellm.tokengate.ru/v1"
    if normalized.endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"


def _log_token_usage(payload: object) -> None:
    if not isinstance(payload, dict):
        return
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return

    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    total_tokens = usage.get("total_tokens")

    try:
        p = int(prompt_tokens) if prompt_tokens is not None else None
    except Exception:
        p = None
    try:
        c = int(completion_tokens) if completion_tokens is not None else None
    except Exception:
        c = None
    try:
        t = int(total_tokens) if total_tokens is not None else None
    except Exception:
        t = None

    if p is None and c is None and t is None:
        return

    logger.info(
        "product-compare token usage: prompt=%s completion=%s total=%s",
        p,
        c,
        t,
    )


def _parse_compare_result(payload: object) -> CompareResult | None:
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        obj = json.loads(content)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None

    try:
        winner_id = int(obj.get("winner_id"))
    except Exception:
        return None

    raw_ranking = obj.get("ranking")
    ranking: list[int] = []
    if isinstance(raw_ranking, list):
        for row in raw_ranking:
            try:
                ranking.append(int(row))
            except Exception:
                continue

    reason = str(obj.get("reason") or "").strip()[:500]
    return CompareResult(winner_id=winner_id, ranking=ranking, reason=reason)
