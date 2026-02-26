from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from aiohttp import ClientSession

from bot.services.wb_client import WB_HTTP_HEADERS, WB_HTTP_PROXY, WbSimilarProduct

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CheapAiPick:
    wb_item_id: int
    score: int
    reason: str


async def rerank_similar_with_llm(
    *,
    api_key: str,
    model: str,
    api_base_url: str,
    base_title: str,
    base_price: str,
    candidates: list[WbSimilarProduct],
    limit: int = 5,
) -> list[WbSimilarProduct]:
    """Rerank/filter similar products via LLM.

    Returns original candidates on any error to keep feature resilient.
    """
    api_key = (api_key or "").strip()
    model = (model or "").strip()
    if not api_key or not model or not candidates:
        return candidates[:limit]

    endpoint = _chat_completions_url(api_base_url)
    payload = {
        "base": {"title": base_title, "price": base_price},
        "candidates": [
            {
                "id": c.wb_item_id,
                "title": c.title,
                "price": str(c.price),
                "url": c.url,
            }
            for c in candidates
        ],
        "task": (
            "Выбери только функциональные аналоги исходного товара. "
            "Не включай товары из других типов/категорий. "
            "Верни JSON формата: {\"picked\":[{\"id\":123,\"score\":0..100,\"reason\":\"...\"}]}. "
            "Сортируй picked по убыванию релевантности. Максимум 5."
        ),
    }

    body = {
        "model": model,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты ассистент для e-commerce matching. "
                    "Определи, какие кандидаты являются реальными аналогами товара. "
                    "Строго исключай нерелевантные типы товаров."
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
                    logger.warning("cheap-ai rerank failed: status=%s", resp.status)
                    return candidates[:limit]
                data = await resp.json(content_type=None)
    except Exception:
        logger.exception("cheap-ai rerank request failed")
        return candidates[:limit]

    picks = _parse_picks(data)
    if not picks:
        return candidates[:limit]

    by_id = {item.wb_item_id: item for item in candidates}
    ordered: list[WbSimilarProduct] = []
    for pick in sorted(picks, key=lambda x: x.score, reverse=True):
        item = by_id.get(pick.wb_item_id)
        if item and item not in ordered:
            ordered.append(item)
        if len(ordered) >= limit:
            break

    return ordered if ordered else candidates[:limit]


def _chat_completions_url(base_url: str) -> str:
    normalized = (base_url or "").strip().rstrip("/")
    if not normalized:
        normalized = "https://litellm.tokengate.ru/v1"
    if normalized.endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"


def _parse_picks(payload: object) -> list[CheapAiPick]:
    if not isinstance(payload, dict):
        return []

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return []

    first = choices[0]
    if not isinstance(first, dict):
        return []

    message = first.get("message")
    if not isinstance(message, dict):
        return []

    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        return []

    try:
        obj = json.loads(content)
    except Exception:
        return []

    raw_picked = obj.get("picked") if isinstance(obj, dict) else None
    if not isinstance(raw_picked, list):
        return []

    out: list[CheapAiPick] = []
    for row in raw_picked:
        if not isinstance(row, dict):
            continue
        raw_id = row.get("id")
        raw_score = row.get("score", 0)
        raw_reason = row.get("reason", "")
        try:
            nm_id = int(raw_id)
        except Exception:
            continue
        try:
            score = int(raw_score)
        except Exception:
            score = 0
        out.append(
            CheapAiPick(
                wb_item_id=nm_id,
                score=max(0, min(100, score)),
                reason=str(raw_reason)[:160],
            )
        )
    return out
