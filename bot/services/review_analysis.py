from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass

from aiohttp import ClientSession

from bot.services.wb_client import WB_HTTP_HEADERS, WB_HTTP_PROXY

_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
_MIN_DETAILED_REVIEW_LEN = 80
_MAX_PROMPT_REVIEWS_PER_SIDE = 25
_MAX_REVIEW_TEXT_LEN = 700
_MAX_QUALITY_LEN = 180


class ReviewAnalysisError(RuntimeError):
    pass


class ReviewAnalysisConfigError(ReviewAnalysisError):
    pass


class ReviewAnalysisRateLimitError(ReviewAnalysisError):
    def __init__(self, *, wait_seconds: int | None = None) -> None:
        self.wait_seconds = wait_seconds
        if wait_seconds and wait_seconds > 0:
            wait_text = _humanize_wait(wait_seconds)
            message = (
                "Сейчас высокая нагрузка на LLM API Groq. "
                f"Подождите {wait_text} и попробуйте снова."
            )
        else:
            message = (
                "Сейчас высокая нагрузка на LLM API Groq. "
                "Подождите немного и попробуйте снова."
            )
        super().__init__(message)


@dataclass(slots=True)
class ReviewInsights:
    strengths: list[str]
    weaknesses: list[str]
    positive_samples: int
    negative_samples: int


@dataclass(slots=True)
class _ReviewSample:
    rating: int
    text: str


@dataclass(slots=True)
class _GroqApiResponse:
    status: int
    payload: dict[str, object] | None
    headers: dict[str, str]


async def analyze_reviews_with_groq(
    *,
    wb_item_id: int,
    product_title: str,
    groq_api_key: str,
    groq_model: str,
    groq_fallback_models: list[str] | tuple[str, ...] | None = None,
) -> ReviewInsights:
    api_key = groq_api_key.strip()
    model = groq_model.strip()
    if not api_key:
        raise ReviewAnalysisConfigError(
            "Не задан GROQ_API_KEY. Добавьте ключ в переменные окружения."
        )
    if not model:
        raise ReviewAnalysisConfigError(
            "Не задан GROQ_MODEL. Укажите модель в переменных окружения."
        )

    feedbacks = await _fetch_feedbacks_for_item(wb_item_id)
    positive, negative = _collect_detailed_reviews(feedbacks)

    if not positive and not negative:
        raise ReviewAnalysisError("Не удалось найти развернутые отзывы для анализа.")

    prompt_payload = {
        "product_title": product_title,
        "positive_reviews": [
            _serialize_review(sample)
            for sample in positive[:_MAX_PROMPT_REVIEWS_PER_SIDE]
        ],
        "negative_reviews": [
            _serialize_review(sample)
            for sample in negative[:_MAX_PROMPT_REVIEWS_PER_SIDE]
        ],
        "task": (
            "Выдели 3 сильных качества и 3 слабых качества товара на основе отзывов. "
            "Если данных для слабых качеств недостаточно, верни меньше пунктов или пустой список."
        ),
    }

    result = await _request_groq(
        api_key=api_key,
        model=model,
        fallback_models=groq_fallback_models,
        prompt_payload=prompt_payload,
    )

    return ReviewInsights(
        strengths=result["strengths"],
        weaknesses=result["weaknesses"],
        positive_samples=len(positive),
        negative_samples=len(negative),
    )


def _serialize_review(sample: _ReviewSample) -> dict[str, str | int]:
    return {
        "rating": sample.rating,
        "text": sample.text[:_MAX_REVIEW_TEXT_LEN],
    }


def _collect_detailed_reviews(
    feedbacks: list[dict[str, object]],
) -> tuple[list[_ReviewSample], list[_ReviewSample]]:
    positive: list[_ReviewSample] = []
    negative: list[_ReviewSample] = []

    for feedback in feedbacks:
        rating = _parse_rating(feedback)
        if rating is None:
            continue

        text = _compose_review_text(feedback)
        if len(text) < _MIN_DETAILED_REVIEW_LEN:
            continue

        sample = _ReviewSample(rating=rating, text=text)
        if rating >= 4:
            positive.append(sample)
        elif rating <= 2:
            negative.append(sample)

    return _deduplicate_samples(positive), _deduplicate_samples(negative)


def _deduplicate_samples(samples: list[_ReviewSample]) -> list[_ReviewSample]:
    seen: set[str] = set()
    out: list[_ReviewSample] = []
    for sample in sorted(samples, key=lambda item: len(item.text), reverse=True):
        normalized = sample.text.lower().strip()
        if normalized in seen:
            continue
        seen.add(normalized)
        out.append(sample)
    return out


def _parse_rating(feedback: dict[str, object]) -> int | None:
    for key in ("productValuation", "valuation"):
        value = feedback.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            if 1 <= value <= 5:
                return value
            continue
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                continue
            try:
                parsed = int(stripped)
            except ValueError:
                continue
            if 1 <= parsed <= 5:
                return parsed
    return None


def _compose_review_text(feedback: dict[str, object]) -> str:
    parts: list[str] = []

    text = _clean_text(feedback.get("text"))
    pros = _clean_text(feedback.get("pros"))
    cons = _clean_text(feedback.get("cons"))

    if pros:
        parts.append(f"Плюсы: {pros}")
    if cons:
        parts.append(f"Минусы: {cons}")
    if text:
        parts.append(f"Комментарий: {text}")

    return " ".join(parts).strip()


def _clean_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    compact = " ".join(value.replace("\n", " ").split())
    if compact in {"нет", "-", "—"}:
        return ""
    return compact


async def _fetch_feedbacks_for_item(wb_item_id: int) -> list[dict[str, object]]:
    root_id = await _fetch_root_id(wb_item_id)
    if root_id is None:
        raise ReviewAnalysisError("Не удалось получить данные карточки товара.")

    urls = (
        f"https://feedbacks1.wb.ru/feedbacks/v1/{root_id}",
        f"https://feedbacks2.wb.ru/feedbacks/v1/{root_id}",
    )

    async with ClientSession(headers=WB_HTTP_HEADERS) as session:
        for url in urls:
            try:
                async with session.get(url, timeout=20, proxy=WB_HTTP_PROXY) as resp:
                    if resp.status != 200:
                        continue
                    payload = await resp.json(content_type=None)
            except Exception:
                continue

            if not isinstance(payload, dict):
                continue

            raw_feedbacks = payload.get("feedbacks")
            if isinstance(raw_feedbacks, list):
                return [item for item in raw_feedbacks if isinstance(item, dict)]

    raise ReviewAnalysisError("Не удалось получить отзывы от Wildberries.")


async def _fetch_root_id(wb_item_id: int) -> int | None:
    url = (
        "https://card.wb.ru/cards/v4/detail"
        f"?appType=1&curr=rub&dest=-1257786&nm={wb_item_id}"
    )

    async with ClientSession(headers=WB_HTTP_HEADERS) as session:
        try:
            async with session.get(url, timeout=20, proxy=WB_HTTP_PROXY) as resp:
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

    product = products[0]
    if not isinstance(product, dict):
        return None

    raw_root = product.get("root")
    if isinstance(raw_root, int):
        return raw_root
    if isinstance(raw_root, str):
        stripped = raw_root.strip()
        if stripped.isdigit():
            return int(stripped)
    return None


async def _request_groq(
    *,
    api_key: str,
    model: str,
    fallback_models: list[str] | tuple[str, ...] | None,
    prompt_payload: dict[str, object],
) -> dict[str, list[str]]:
    system_prompt = (
        "Ты продуктовый аналитик. "
        "На основе отзывов выдели ключевые сильные и слабые качества товара. "
        "Верни только JSON без пояснений в формате: "
        '{"strengths": ["..."], "weaknesses": ["..."]}. '
        "Ограничение: максимум 3 пункта в каждом списке."
    )
    user_prompt = (
        "Проанализируй отзывы и верни итог. "
        "Данные для анализа:\n"
        f"{json.dumps(prompt_payload, ensure_ascii=False)}"
    )

    model_candidates = _model_candidates(model, fallback_models)
    rate_limited_wait: int | None = None
    rate_limited_detected = False
    for current_model in model_candidates:
        base_payload: dict[str, object] = {
            "model": current_model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        payload_with_format = {
            **base_payload,
            "response_format": {"type": "json_object"},
        }

        for payload in (payload_with_format, base_payload):
            response = await _post_groq(api_key=api_key, payload=payload)
            if response is None:
                continue

            if response.status == 429:
                rate_limited_detected = True
                wait_seconds = _extract_rate_limit_wait_seconds(response.headers)
                if wait_seconds is not None:
                    if rate_limited_wait is None:
                        rate_limited_wait = wait_seconds
                    else:
                        rate_limited_wait = max(rate_limited_wait, wait_seconds)
                continue

            if response.status != 200 or response.payload is None:
                continue

            content = _extract_message_content(response.payload)
            if not content:
                continue

            parsed = _parse_json_content(content)
            strengths = _normalize_qualities(
                parsed,
                keys=("strengths", "good", "positive"),
            )
            weaknesses = _normalize_qualities(
                parsed,
                keys=("weaknesses", "bad", "negative"),
            )

            if strengths or weaknesses:
                return {
                    "strengths": strengths,
                    "weaknesses": weaknesses,
                }

    if rate_limited_detected:
        raise ReviewAnalysisRateLimitError(wait_seconds=rate_limited_wait)

    raise ReviewAnalysisError("Groq не вернул корректный ответ ни в одной модели.")


def _model_candidates(
    primary_model: str,
    fallback_models: list[str] | tuple[str, ...] | None,
) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []

    for item in [primary_model, *(fallback_models or [])]:
        model = item.strip()
        if not model or model in seen:
            continue
        seen.add(model)
        out.append(model)

    return out


async def _post_groq(
    *,
    api_key: str,
    payload: dict[str, object],
) -> _GroqApiResponse | None:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with ClientSession() as session:
        try:
            async with session.post(
                _GROQ_URL,
                headers=headers,
                json=payload,
                timeout=40,
            ) as resp:
                header_map = {k.lower(): v for k, v in resp.headers.items()}
                data: dict[str, object] | None = None
                try:
                    raw = await resp.json(content_type=None)
                    if isinstance(raw, dict):
                        data = raw
                except Exception:
                    data = None

                return _GroqApiResponse(
                    status=resp.status,
                    payload=data,
                    headers=header_map,
                )
        except Exception:
            return None


def _extract_rate_limit_wait_seconds(headers: dict[str, str]) -> int | None:
    retry_after = headers.get("retry-after", "")
    retry_after_seconds = _parse_retry_after_seconds(retry_after)
    if retry_after_seconds is not None:
        return retry_after_seconds

    reset_tokens = _parse_duration_seconds(headers.get("x-ratelimit-reset-tokens", ""))
    reset_requests = _parse_duration_seconds(
        headers.get("x-ratelimit-reset-requests", "")
    )
    candidates = [v for v in (reset_tokens, reset_requests) if v is not None]
    if not candidates:
        return None
    return int(max(candidates))


def _parse_retry_after_seconds(value: str) -> int | None:
    raw = value.strip()
    if not raw:
        return None

    try:
        seconds = float(raw)
    except ValueError:
        return None
    if seconds <= 0:
        return None
    return max(1, int(math.ceil(seconds)))


_DURATION_PART_RE = re.compile(r"(\d+(?:\.\d+)?)([hms])")


def _parse_duration_seconds(value: str) -> int | None:
    raw = value.strip().lower()
    if not raw:
        return None

    total = 0.0
    matched = False
    for number, unit in _DURATION_PART_RE.findall(raw):
        matched = True
        amount = float(number)
        if unit == "h":
            total += amount * 3600
        elif unit == "m":
            total += amount * 60
        elif unit == "s":
            total += amount

    if not matched or total <= 0:
        return None
    return max(1, int(math.ceil(total)))


def _humanize_wait(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} сек"

    minutes = seconds // 60
    rest = seconds % 60
    if rest == 0:
        return f"{minutes} мин"
    return f"{minutes} мин {rest} сек"


def _extract_message_content(response: dict[str, object]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""

    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if not isinstance(message, dict):
        return ""

    content = message.get("content")
    return content if isinstance(content, str) else ""


def _parse_json_content(content: str) -> dict[str, object]:
    stripped = content.strip()
    try:
        payload = json.loads(stripped)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {}
        fragment = stripped[start : end + 1]
        try:
            payload = json.loads(fragment)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}


def _normalize_qualities(
    parsed: dict[str, object],
    *,
    keys: tuple[str, ...],
) -> list[str]:
    raw: object = []
    for key in keys:
        if key in parsed:
            raw = parsed[key]
            break

    if not isinstance(raw, list):
        return []

    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        text = " ".join(item.split()).strip("- ")
        if not text:
            continue
        text = text[:_MAX_QUALITY_LEN]
        normalized = text.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        out.append(text)
        if len(out) >= 3:
            break
    return out
