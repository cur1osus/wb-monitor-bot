from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from dataclasses import dataclass
from html import escape as escape_html

from aiohttp import ClientSession

from bot.services.wb_client import WB_HTTP_HEADERS, WB_HTTP_PROXY, WbProductSnapshot

logger = logging.getLogger(__name__)

_COMPARE_MODES = {"cheap", "quality", "gift", "safe", "balanced"}


def _mode_prompt(mode: str) -> str:
    prompts = {
        "cheap": (
            "Приоритет — минимальная цена при приемлемом качестве. "
            "Если разница в качестве несущественная, выбирай более дешевый вариант."
        ),
        "quality": (
            "Приоритет — качество и надежность. "
            "Ориентируйся на рейтинг, стабильность отзывов, низкий риск и репутацию."
        ),
        "gift": (
            "Приоритет — товар, который не стыдно подарить: "
            "стабильное качество, низкий риск негатива, хорошая презентабельность и наличие. "
            "Не выбирай только из-за самой низкой цены."
        ),
        "safe": (
            "Приоритет — минимальный риск неудачной покупки: "
            "надежные отзывы, меньше критичных жалоб, предсказуемое качество и наличие."
        ),
        "balanced": (
            "Сбалансируй цену, качество, риск и наличие без перекоса в один фактор."
        ),
    }
    return prompts.get(mode, prompts["balanced"])

_CRIT_RE = re.compile(
    r"брак|слом|трещ|плох|ужас|возврат|не рекоменд|отвал|разочар|small|маломер|большемер",
    re.IGNORECASE,
)


@dataclass(slots=True)
class ProductScore:
    wb_item_id: int
    value: int
    trust: int
    risk: int
    availability: int
    overall: int
    target_price: int | None


@dataclass(slots=True)
class CompareResult:
    winner_id: int
    ranking: list[int]
    reason: str
    risks: list[str]
    wait_tip: str | None
    scores: list[ProductScore]


async def compare_products_with_llm(
    *,
    products: list[WbProductSnapshot],
    mode: str,
    api_key: str,
    model: str,
    api_base_url: str,
    price_history: dict[int, dict[str, float | int | None]] | None = None,
) -> CompareResult:
    mode = (mode or "balanced").strip().lower()
    if mode not in _COMPARE_MODES:
        mode = "balanced"

    if len(products) < 2:
        raise ValueError("Need at least 2 products")

    review_signals = await _fetch_review_signals_many([p.wb_item_id for p in products])
    det = _deterministic_compare(products, mode=mode, history=price_history or {}, review_signals=review_signals)

    api_key = (api_key or "").strip()
    model = (model or "").strip()
    if not api_key or not model:
        return det

    endpoint = _chat_completions_url(api_base_url)
    mode_prompt = _mode_prompt(mode)

    payload = {
        "mode": mode,
        "mode_instruction": mode_prompt,
        "scoring_facts": [
            {
                "id": s.wb_item_id,
                "value": s.value,
                "trust": s.trust,
                "risk": s.risk,
                "availability": s.availability,
                "overall": s.overall,
                "target_price": s.target_price,
            }
            for s in det.scores
        ],
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
                "entity": p.entity,
                "history": (price_history or {}).get(p.wb_item_id),
                "review_signals": review_signals.get(p.wb_item_id),
            }
            for p in products
        ],
        "task": (
            "Выбери лучший товар в зависимости от режима. Используй только факты. "
            "Пиши простым русским языком для покупателя: без id, без названий полей, без JSON-вставок в тексте. "
            "В risks верни только список коротких строк. "
            "Верни JSON: {winner_id, ranking, reason, risks:[..], wait_tip}."
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
                    "Ты аналитик WB. Отвечай строго JSON без markdown. "
                    f"Режим сравнения: {mode}. {mode_prompt}"
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    }

    try:
        async with ClientSession(headers=WB_HTTP_HEADERS) as session:
            async with session.post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}"},
                json=body,
                timeout=25,
                proxy=WB_HTTP_PROXY,
            ) as resp:
                if resp.status != 200:
                    logger.warning("product-compare llm status=%s", resp.status)
                    return det
                data = await resp.json(content_type=None)
    except Exception:
        logger.exception("product-compare llm request failed")
        return det

    _log_token_usage(data)
    parsed = _parse_compare_result(data)
    if not parsed:
        return det

    valid_ids = {p.wb_item_id for p in products}
    ranking = [i for i in parsed.ranking if i in valid_ids]
    if not ranking:
        ranking = det.ranking

    winner_id = parsed.winner_id if parsed.winner_id in valid_ids else ranking[0]
    if winner_id not in ranking:
        ranking.insert(0, winner_id)

    return CompareResult(
        winner_id=winner_id,
        ranking=ranking,
        reason=parsed.reason or det.reason,
        risks=parsed.risks or det.risks,
        wait_tip=parsed.wait_tip or det.wait_tip,
        scores=det.scores,
    )


def _deterministic_compare(
    products: list[WbProductSnapshot],
    *,
    mode: str,
    history: dict[int, dict[str, float | int | None]],
    review_signals: dict[int, dict[str, float | int]],
) -> CompareResult:
    prices = [float(p.price) for p in products if p.price is not None]
    min_price = min(prices) if prices else 0.0
    max_price = max(prices) if prices else 0.0

    weights = {
        "cheap": (0.5, 0.15, 0.2, 0.15),
        "quality": (0.2, 0.35, 0.3, 0.15),
        "gift": (0.2, 0.3, 0.2, 0.3),
        "safe": (0.15, 0.35, 0.35, 0.15),
        "balanced": (0.3, 0.25, 0.25, 0.2),
    }[mode]

    scores: list[ProductScore] = []
    for p in products:
        price = float(p.price) if p.price is not None else None
        if price is None or max_price <= min_price:
            price_part = 50.0
        else:
            price_part = 100.0 * (max_price - price) / (max_price - min_price)

        rating = float(p.rating or 0)
        reviews = int(p.reviews or 0)
        rev_norm = min(100.0, math.log10(reviews + 1) * 35.0)
        value = int(max(0, min(100, round(price_part * 0.6 + rating * 8 + rev_norm * 0.2))))

        rs = review_signals.get(p.wb_item_id, {})
        stability = float(rs.get("stability", 50.0))
        critical_share = float(rs.get("critical_share", 0.0))
        trust = int(max(0, min(100, round(rev_norm * 0.6 + stability * 0.4))))
        risk = int(max(0, min(100, round(100 - (critical_share * 100 * 0.7 + rating * 10 * 0.3)))))

        qty = int(p.total_qty or 0)
        sizes_count = len(p.sizes or [])
        availability = 100 if p.in_stock else 0
        availability = int(max(0, min(100, availability * 0.65 + min(30, qty / 3) + min(20, sizes_count * 3))))

        overall_f = value * weights[0] + trust * weights[1] + (100 - risk) * weights[2] + availability * weights[3]

        # Для режима "подарок" не должны побеждать только за счет минимальной цены.
        # Добавляем бонус за "надежное качество" и легкий штраф за крайние ценовые позиции.
        if mode == "gift":
            quality = max(0.0, min(100.0, rating * 18.0 + rev_norm * 0.45))
            if price is None or max_price <= min_price:
                price_moderation = 60.0
            else:
                # Лучший балл — ближе к середине диапазона цен, а не к минимуму.
                mid = (min_price + max_price) / 2.0
                half = max(1.0, (max_price - min_price) / 2.0)
                dist = abs(price - mid)
                price_moderation = max(0.0, 100.0 - (dist / half) * 100.0)

            overall_f = (
                quality * 0.35
                + trust * 0.25
                + availability * 0.25
                + (100 - risk) * 0.1
                + price_moderation * 0.05
            )

        overall = int(max(0, min(100, round(overall_f))))

        hist = history.get(p.wb_item_id, {})
        h_min = hist.get("min")
        target_price = None
        if h_min is not None:
            try:
                target_price = int(round(float(h_min)))
            except Exception:
                target_price = None

        scores.append(
            ProductScore(
                wb_item_id=p.wb_item_id,
                value=value,
                trust=trust,
                risk=risk,
                availability=availability,
                overall=overall,
                target_price=target_price,
            )
        )

    ranked = sorted(scores, key=lambda s: s.overall, reverse=True)
    winner = ranked[0]

    # Build wait_tip covering ALL products that have a historical minimum.
    wait_tips: list[str] = []
    for s in ranked:
        if s.target_price is not None:
            prod = next((p for p in products if p.wb_item_id == s.wb_item_id), None)
            if prod:
                current = float(prod.price) if prod.price is not None else None
                label = escape_html(prod.title[:40]) if prod.title else str(s.wb_item_id)
                if current is not None and s.target_price < current:
                    wait_tips.append(f"{label}: мин. {s.target_price}₽ (сейчас {int(current)}₽)")
                else:
                    wait_tips.append(f"{label}: ист. мин. {s.target_price}₽")

    wait_tip = ("Исторические минимумы: " + "; ".join(wait_tips)) if wait_tips else None

    risks = [
        "Проверь размерную сетку и отзывы по размеру.",
        "Сверь продавца и условия возврата перед покупкой.",
    ]

    return CompareResult(
        winner_id=winner.wb_item_id,
        ranking=[s.wb_item_id for s in ranked],
        reason="Лучший итоговый баланс по цене, надежности отзывов, риску и наличию.",
        risks=risks,
        wait_tip=wait_tip,
        scores=ranked,
    )


async def _fetch_review_signals_many(wb_item_ids: list[int]) -> dict[int, dict[str, float | int]]:
    results = await asyncio.gather(
        *(_fetch_review_signals(nm) for nm in wb_item_ids),
        return_exceptions=True,
    )
    out: dict[int, dict[str, float | int]] = {}
    for nm, res in zip(wb_item_ids, results):
        if isinstance(res, dict):
            out[nm] = res
        else:
            out[nm] = {"critical_share": 0.0, "stability": 50.0}
    return out


async def _fetch_review_signals(wb_item_id: int) -> dict[str, float | int]:
    root_id = await _fetch_root_id(wb_item_id)
    if root_id is None:
        return {"critical_share": 0.0, "stability": 50.0}

    urls = [
        f"https://feedbacks1.wb.ru/feedbacks/v1/{root_id}",
        f"https://feedbacks2.wb.ru/feedbacks/v1/{root_id}",
    ]
    feedbacks: list[dict] = []
    async with ClientSession(headers=WB_HTTP_HEADERS) as session:
        for url in urls:
            try:
                async with session.get(url, timeout=15, proxy=WB_HTTP_PROXY) as resp:
                    if resp.status != 200:
                        continue
                    payload = await resp.json(content_type=None)
            except Exception:
                continue
            if isinstance(payload, dict) and isinstance(payload.get("feedbacks"), list):
                feedbacks = [f for f in payload["feedbacks"] if isinstance(f, dict)]
                if feedbacks:
                    break

    if not feedbacks:
        return {"critical_share": 0.0, "stability": 50.0}

    ratings: list[int] = []
    critical = 0
    for fb in feedbacks:
        val = fb.get("productValuation", fb.get("valuation"))
        try:
            r = int(val)
            if 1 <= r <= 5:
                ratings.append(r)
        except Exception:
            pass
        text = " ".join(
            [
                str(fb.get("text") or ""),
                str(fb.get("pros") or ""),
                str(fb.get("cons") or ""),
            ]
        )
        if _CRIT_RE.search(text):
            critical += 1

    total = len(feedbacks)
    critical_share = (critical / total) if total else 0.0

    if ratings:
        mean = sum(ratings) / len(ratings)
        var = sum((r - mean) ** 2 for r in ratings) / len(ratings)
        std = math.sqrt(var)
        stability = max(0.0, min(100.0, 100.0 - std * 18.0))
    else:
        stability = 50.0

    return {
        "critical_share": round(critical_share, 3),
        "stability": round(stability, 1),
        "reviews_total": total,
    }


async def _fetch_root_id(wb_item_id: int) -> int | None:
    url = (
        "https://card.wb.ru/cards/v4/detail"
        f"?appType=1&curr=rub&dest=-1257786&nm={wb_item_id}"
    )

    async with ClientSession(headers=WB_HTTP_HEADERS) as session:
        try:
            async with session.get(url, timeout=15, proxy=WB_HTTP_PROXY) as resp:
                if resp.status != 200:
                    return None
                payload = await resp.json(content_type=None)
        except Exception:
            return None

    if not isinstance(payload, dict):
        return None
    products = payload.get("products")
    if not isinstance(products, list):
        nested = payload.get("data")
        if isinstance(nested, dict):
            products = nested.get("products")
    if not isinstance(products, list) or not products:
        return None
    first = products[0]
    if not isinstance(first, dict):
        return None
    raw_root = first.get("root")
    try:
        return int(raw_root)
    except Exception:
        return None


def _chat_completions_url(base_url: str) -> str:
    normalized = (base_url or "").strip().rstrip("/")
    if not normalized:
        normalized = "https://litellm.tokengate.ru/v1"
    if normalized.endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"


@dataclass(slots=True)
class _ParsedCompare:
    winner_id: int
    ranking: list[int]
    reason: str
    risks: list[str]
    wait_tip: str | None


def _parse_compare_result(payload: object) -> _ParsedCompare | None:
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

    ranking: list[int] = []
    if isinstance(obj.get("ranking"), list):
        for row in obj["ranking"]:
            try:
                ranking.append(int(row))
            except Exception:
                continue

    reason = str(obj.get("reason") or "").strip()[:700]
    risks: list[str] = []
    raw_risks = obj.get("risks") or []
    if isinstance(raw_risks, list):
        for item in raw_risks:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    risks.append(text[:180])
            elif isinstance(item, dict):
                text = str(
                    item.get("risk_description")
                    or item.get("description")
                    or item.get("text")
                    or ""
                ).strip()
                if text:
                    risks.append(text[:180])
            if len(risks) >= 5:
                break

    wait_tip = str(obj.get("wait_tip") or "").strip()[:220] or None

    return _ParsedCompare(
        winner_id=winner_id,
        ranking=ranking,
        reason=reason,
        risks=risks,
        wait_tip=wait_tip,
    )


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
