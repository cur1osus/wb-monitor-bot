from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass
from decimal import Decimal
from string import punctuation
from typing import TYPE_CHECKING
from urllib.parse import quote_plus

from aiohttp import ClientSession

try:
    from pymorphy3 import MorphAnalyzer
except Exception:  # pragma: no cover
    MorphAnalyzer = None  # type: ignore[assignment]

from bot.db.redis import WbItemCacheRD

if TYPE_CHECKING:
    from redis.asyncio import Redis

WB_RE = re.compile(r"(\d{6,15})")
WB_CATALOG_RE = re.compile(r"/catalog/(\d{6,15})", re.IGNORECASE)
_CYRILLIC_RE = re.compile(r"[а-яё]")
SEARCH_WB_URLS = (
    "https://search.wb.ru/exactmatch/ru/common/v13/search?ab_testing=false&appType=1&curr=rub&dest=-1257786&lang=ru&page={page}&query={query}&resultset=catalog&sort=popular&spp=30&suppressSpellcheck=false",
    "https://search.wb.ru/exactmatch/ru/common/v9/search?ab_testing=false&appType=1&curr=rub&dest=-1257786&lang=ru&page={page}&query={query}&resultset=catalog&sort=popular&spp=30&suppressSpellcheck=false",
)
MENU_URL = "https://static-basket-01.wbbasket.ru/vol0/data/main-menu-ru-ru-v3.json"
CATALOG_URL = "https://catalog.wb.ru/catalog/{shard}/v4/catalog?ab_testing=false&appType=1&curr=rub&dest=-1257786&lang=ru&page={page}&sort=popular&spp=30"
WB_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.wildberries.ru/",
    "Origin": "https://www.wildberries.ru",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}

_STOP_WORDS = {
    "для",
    "или",
    "без",
    "под",
    "это",
    "при",
    "как",
    "что",
    "она",
    "они",
    "the",
    "and",
    "with",
}
_GENDER_MALE_PREFIXES = ("муж",)
_GENDER_FEMALE_PREFIXES = ("жен",)
_GENDER_MALE_WORDS = {"men", "male", "man", "boy", "boys"}
_GENDER_FEMALE_WORDS = {"women", "woman", "female", "girl", "girls"}
_GENDER_UNISEX_WORDS = {"unisex", "унисекс"}
_GENDER_TOKENS = _GENDER_MALE_WORDS | _GENDER_FEMALE_WORDS | _GENDER_UNISEX_WORDS
_MIN_CHARACTERISTICS_MATCH_PERCENT = 50
_RELAXED_MATCH_PERCENT = 35
_MINIMAL_MATCH_PERCENT = 20
_GENERIC_PRODUCT_TOKENS = {
    "кабель",
    "провод",
    "зарядка",
    "зарядное",
    "зарядный",
    "устройство",
    "устройства",
    "устройств",
    "адаптер",
    "телефон",
    "телефона",
    "телефонов",
    "смартфон",
    "смартфона",
    "часы",
    "часов",
    "смарт",
    "умный",
    "умных",
    "ремешок",
    "аксессуар",
    "аксессуары",
    "чехол",
    "кейс",
    "комплект",
    "набор",
    "зарядки",
    "usb",
    "type",
    "micro",
    "lightning",
    "charger",
    "cable",
    "charge",
}
_ECOSYSTEM_TOKENS: dict[str, set[str]] = {
    "apple": {"apple", "iphone", "iwatch", "airpods", "watchos", "ios"},
    "xiaomi": {"xiaomi", "redmi", "miband"},
    "samsung": {"samsung", "galaxy"},
    "huawei": {"huawei", "honor"},
    "yandex": {"yandex", "яндекс", "yndx", "alice", "алиса", "станция"},
}
_TYPE_TOKENS = {
    "cable",
    "charge",
    "adapter",
    "power",
    "supply",
    "battery",
    "station",
    "remeshok",
    "strap",
    "case",
    "cover",
    "glass",
}
_MODEL_TOKEN_RE = re.compile(r"\b[a-z0-9]{2,}(?:-[a-z0-9]{2,})+\b", re.IGNORECASE)
_TOKEN_NORMALIZATION = {
    "часы": "watch",
    "часов": "watch",
    "часами": "watch",
    "часам": "watch",
    "час": "watch",
    "смартчасы": "watch",
    "iwatch": "watch",
    "эпл": "apple",
    "апл": "apple",
    "айфон": "iphone",
    "сяоми": "xiaomi",
    "ксиоми": "xiaomi",
    "redmi": "xiaomi",
    "галакси": "galaxy",
    "кабель": "cable",
    "провод": "cable",
    "шнур": "cable",
    "зарядка": "charge",
    "зарядки": "charge",
    "зарядное": "charge",
    "зарядные": "charge",
    "зарядный": "charge",
    "блок": "power",
    "питание": "power",
    "питания": "power",
    "адаптер": "adapter",
    "аккумулятор": "battery",
    "станция": "station",
    "яндекс": "yandex",
    "charger": "charge",
    "charging": "charge",
    "remeshok": "strap",
    "ремешок": "strap",
}
_MENU_CACHE_TTL_SEC = 6 * 60 * 60
_MENU_CACHE: list["WbCatalogCategory"] | None = None
_MENU_CACHE_TS = 0.0
WB_HTTP_PROXY = os.environ.get("WB_HTTP_PROXY", "").strip() or None
_MORPH = MorphAnalyzer() if MorphAnalyzer is not None else None

_WEB_SEARCH_URL = "https://duckduckgo.com/html/?q={query}"
_WEB_ID_RE = re.compile(r"/catalog/(\d{6,15})/detail\.aspx", re.IGNORECASE)
_WEB_MAX_CANDIDATES = 40
_WEB_FETCH_CONCURRENCY = 8


@dataclass
class WbProductSnapshot:
    wb_item_id: int
    title: str
    price: Decimal | None
    rating: Decimal | None
    reviews: int | None
    in_stock: bool
    total_qty: int | None
    sizes: list[str]
    brand: str | None = None
    entity: str | None = None
    subject_id: int | None = None


@dataclass
class WbSimilarProduct:
    wb_item_id: int
    title: str
    price: Decimal
    url: str


@dataclass(frozen=True)
class WbCatalogCategory:
    name: str
    shard: str
    query: str
    tokens: frozenset[str]


def extract_wb_item_id(url_or_text: str) -> int | None:
    catalog_match = WB_CATALOG_RE.search(url_or_text)
    if catalog_match:
        try:
            return int(catalog_match.group(1))
        except ValueError:
            return None

    found = WB_RE.search(url_or_text)
    if not found:
        return None
    try:
        return int(found.group(1))
    except ValueError:
        return None


def _extract_price(product: dict[str, object]) -> Decimal | None:
    sale_price = product.get("salePriceU")
    sizes_data = product.get("sizes")
    if (
        not isinstance(sale_price, (int, float))
        and isinstance(sizes_data, list)
        and sizes_data
    ):
        first_size = sizes_data[0]
        if isinstance(first_size, dict):
            price_data = first_size.get("price")
            if isinstance(price_data, dict):
                first_price = price_data.get("product")
                sale_price = (
                    first_price if isinstance(first_price, (int, float)) else None
                )
    if not isinstance(sale_price, (int, float)):
        return None
    return Decimal(str(sale_price)) / Decimal("100")


def _extract_rating(product: dict[str, object]) -> Decimal | None:
    raw = product.get("nmReviewRating")
    if not isinstance(raw, (int, float)):
        raw = product.get("reviewRating")
    if not isinstance(raw, (int, float)):
        raw = product.get("rating")
    if not isinstance(raw, (int, float)):
        return None
    return Decimal(str(raw))


def _extract_reviews(product: dict[str, object]) -> int | None:
    raw = product.get("nmFeedbacks")
    value = _parse_int(raw)
    if value is not None:
        return max(0, value)

    value = _parse_int(product.get("feedbacks"))
    if value is not None:
        return max(0, value)

    value = _parse_int(product.get("feedbacksCount"))
    if value is not None:
        return max(0, value)
    return None


def _normalize_match_percent(value: int | None) -> int:
    if value is None:
        return _MIN_CHARACTERISTICS_MATCH_PERCENT
    return max(10, min(95, int(value)))


def _tokenize(text: str) -> list[str]:
    normalized = text.translate(str.maketrans({c: " " for c in punctuation})).lower()
    out: list[str] = []
    for token in normalized.split():
        if _MORPH is not None and _CYRILLIC_RE.search(token):
            parsed = _MORPH.parse(token)
            if parsed:
                token = parsed[0].normal_form
        token = _TOKEN_NORMALIZATION.get(token, token)
        if len(token) >= 3 and not token.isdigit() and token not in _STOP_WORDS:
            out.append(token)
    return out


def _build_search_query(title: str) -> str:
    tokens = _tokenize(title)
    if not tokens:
        return title.strip()[:80]

    model_tokens = sorted(_extract_model_tokens(title))
    if model_tokens:
        return " ".join((tokens + model_tokens)[:8])
    return " ".join(tokens[:6])


def _extract_model_tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _MODEL_TOKEN_RE.finditer(text or "")}


def _model_tokens_compatible(
    base_model_tokens: set[str], candidate_text: str, candidate_tokens: set[str]
) -> bool:
    if not base_model_tokens:
        return True

    candidate_model_tokens = _extract_model_tokens(candidate_text)
    if any(token in candidate_model_tokens for token in base_model_tokens):
        return True

    return any(token in candidate_tokens for token in base_model_tokens)


def _is_male_token(token: str) -> bool:
    if token in _GENDER_MALE_WORDS:
        return True
    return any(token.startswith(prefix) for prefix in _GENDER_MALE_PREFIXES)


def _is_female_token(token: str) -> bool:
    if token in _GENDER_FEMALE_WORDS:
        return True
    return any(token.startswith(prefix) for prefix in _GENDER_FEMALE_PREFIXES)


def _detect_gender(text: str) -> str | None:
    tokens = _tokenize(text)
    has_unisex = any(token in _GENDER_UNISEX_WORDS for token in tokens)
    has_male = any(_is_male_token(token) for token in tokens)
    has_female = any(_is_female_token(token) for token in tokens)

    if has_unisex:
        return None
    if has_male and not has_female:
        return "male"
    if has_female and not has_male:
        return "female"
    return None


def _characteristic_tokens(text: str) -> set[str]:
    out: set[str] = set()
    for token in _tokenize(text):
        if token in _GENDER_TOKENS:
            continue
        if _is_male_token(token) or _is_female_token(token):
            continue
        out.add(token)
    return out


def _is_latin_or_digit_token(token: str) -> bool:
    has_latin = any("a" <= ch <= "z" for ch in token)
    has_digit = any(ch.isdigit() for ch in token)
    return has_latin or has_digit


def _anchor_tokens(tokens: set[str]) -> set[str]:
    return {token for token in tokens if token not in _GENERIC_PRODUCT_TOKENS}


def _required_anchor_matches(anchor_tokens: set[str]) -> int:
    if not anchor_tokens:
        return 0

    strong = [token for token in anchor_tokens if _is_latin_or_digit_token(token)]
    if len(strong) >= 2:
        return 2
    if len(anchor_tokens) >= 4:
        return 2
    return 1


def _detect_ecosystem(tokens: set[str]) -> str | None:
    for ecosystem, ecosystem_tokens in _ECOSYSTEM_TOKENS.items():
        if any(token in ecosystem_tokens for token in tokens):
            return ecosystem
    return None


def _is_ecosystem_compatible(
    base_ecosystem: str | None, candidate_tokens: set[str]
) -> bool:
    if base_ecosystem is None:
        return True
    candidate_ecosystem = _detect_ecosystem(candidate_tokens)
    if candidate_ecosystem is None:
        return True
    return candidate_ecosystem == base_ecosystem


def _exclude_reference_tokens(tokens: set[str], reference_text: str | None) -> set[str]:
    if not reference_text:
        return set(tokens)

    ref_tokens = set(_tokenize(reference_text))
    if not ref_tokens:
        return set(tokens)

    return {
        token
        for token in tokens
        if not any(_tokens_match(token, ref_token) for ref_token in ref_tokens)
    }


async def _get_json_with_retries(
    session: ClientSession,
    url: str,
    *,
    timeout: int = 20,
    retries: int = 2,
) -> object | None:
    for attempt in range(retries + 1):
        try:
            async with session.get(url, timeout=timeout, proxy=WB_HTTP_PROXY) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    if isinstance(data, (dict, list)):
                        return data
                    return None
                if resp.status == 429 and attempt < retries:
                    await asyncio.sleep(0.35 * (attempt + 1))
                    continue
                return None
        except Exception:
            if attempt < retries:
                await asyncio.sleep(0.35 * (attempt + 1))
                continue
            return None
    return None


def _is_cyrillic_token(token: str) -> bool:
    return bool(_CYRILLIC_RE.search(token))


def _tokens_match(left: str, right: str) -> bool:
    if left == right:
        return True

    # Prefix matching is useful for latin/model tokens (yndx-00051, usbc, etc.)
    # but too noisy for short cyrillic stems (e.g. "маркер" vs "маркиратор").
    left_cyr = _is_cyrillic_token(left)
    right_cyr = _is_cyrillic_token(right)

    if left_cyr or right_cyr:
        # For cyrillic tokens require longer shared prefix to reduce false positives.
        if len(left) >= 6 and len(right) >= 6 and left[:6] == right[:6]:
            return True
        return False

    if len(left) >= 4 and len(right) >= 4 and left[:4] == right[:4]:
        return True
    return False


def _match_count(base_tokens: set[str], candidate_tokens: set[str]) -> int:
    if not base_tokens or not candidate_tokens:
        return 0

    matched = 0
    for token in base_tokens:
        if any(_tokens_match(token, candidate) for candidate in candidate_tokens):
            matched += 1
    return matched


def _match_percent(base_tokens: set[str], candidate_tokens: set[str]) -> int:
    if not base_tokens:
        return 0
    matched = _match_count(base_tokens, candidate_tokens)
    return int((matched / len(base_tokens)) * 100)


def _overlap_score(left: set[str], right: set[str] | frozenset[str]) -> int:
    if not left or not right:
        return 0

    score = 0
    for left_token in left:
        for right_token in right:
            if _tokens_match(left_token, right_token):
                score += 1
                break
    return score


def _parse_int(raw: object) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        value = raw.strip()
        if not value:
            return None
        try:
            return int(value)
        except ValueError:
            return None
    return None


async def fetch_product(
    redis: "Redis",
    wb_item_id: int,
    session: ClientSession | None = None,
    *,
    use_cache: bool = True,
) -> WbProductSnapshot | None:
    if use_cache:
        cached = await WbItemCacheRD.get(redis, wb_item_id)
        if cached:
            return WbProductSnapshot(
                wb_item_id=wb_item_id,
                title=cached.title or f"WB #{wb_item_id}",
                price=Decimal(str(cached.price)) if cached.price is not None else None,
                rating=Decimal(str(cached.rating))
                if cached.rating is not None
                else None,
                reviews=cached.reviews,
                in_stock=bool(cached.in_stock),
                total_qty=cached.total_qty,
                sizes=list(cached.sizes),
            )

    url = f"https://card.wb.ru/cards/v4/detail?appType=1&curr=rub&dest=-1257786&nm={wb_item_id}"
    if session is None:
        async with ClientSession(headers=WB_HTTP_HEADERS) as new_session:
            return await _fetch_and_cache(new_session, redis, url, wb_item_id)
    return await _fetch_and_cache(session, redis, url, wb_item_id)


def _extract_web_candidate_ids(html_text: str) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for m in _WEB_ID_RE.finditer(html_text or ""):
        try:
            nm = int(m.group(1))
        except Exception:
            continue
        if nm in seen:
            continue
        seen.add(nm)
        out.append(nm)
    return out


async def _web_search_candidate_ids(
    session: ClientSession,
    *,
    query_text: str,
    limit: int = _WEB_MAX_CANDIDATES,
) -> list[int]:
    q = quote_plus(query_text.strip())
    if not q:
        return []
    url = _WEB_SEARCH_URL.format(query=q)
    try:
        async with session.get(url, timeout=12, proxy=WB_HTTP_PROXY) as resp:
            if resp.status != 200:
                return []
            html_text = await resp.text()
    except Exception:
        return []
    ids = _extract_web_candidate_ids(html_text)
    return ids[: max(1, limit)]


async def search_similar_cheaper_via_web(
    *,
    redis: "Redis",
    base_title: str,
    max_price: Decimal,
    exclude_wb_item_id: int,
    base_entity: str | None = None,
    base_brand: str | None = None,
    base_subject_id: int | None = None,
    match_percent_threshold: int | None = None,
    limit: int = 5,
    candidate_limit: int = _WEB_MAX_CANDIDATES,
) -> list[WbSimilarProduct]:
    base_text = f"{base_title} {base_entity or ''}"
    base_tokens = _characteristic_tokens(base_text)
    if not base_tokens:
        return []

    base_brand_tokens = set(_tokenize(base_brand or ""))
    base_anchor_tokens = _anchor_tokens(base_tokens)
    base_type_tokens = {token for token in base_tokens if token in _TYPE_TOKENS}
    base_model_tokens = _extract_model_tokens(base_text)
    base_ecosystem = _detect_ecosystem(base_tokens)
    required_anchor_matches = _required_anchor_matches(base_anchor_tokens)
    min_match_percent = _normalize_match_percent(match_percent_threshold)

    # two queries: broad + model-focused
    q1 = f'site:wildberries.ru {base_title}'
    model_part = " ".join(sorted(base_model_tokens))
    q2 = f'site:wildberries.ru {base_title} {model_part}'.strip()

    async with ClientSession(headers=WB_HTTP_HEADERS) as session:
        ids1, ids2 = await asyncio.gather(
            _web_search_candidate_ids(session, query_text=q1, limit=candidate_limit),
            _web_search_candidate_ids(session, query_text=q2, limit=candidate_limit),
        )

    ordered_ids: list[int] = []
    seen: set[int] = set()
    for nm in ids1 + ids2:
        if nm == exclude_wb_item_id or nm in seen:
            continue
        seen.add(nm)
        ordered_ids.append(nm)
    ordered_ids = ordered_ids[: max(limit * 6, candidate_limit)]

    sem = asyncio.Semaphore(_WEB_FETCH_CONCURRENCY)

    async def load(nm_id: int) -> tuple[int, WbProductSnapshot | None]:
        async with sem:
            try:
                p = await fetch_product(redis, nm_id, use_cache=True)
            except Exception:
                p = None
            return nm_id, p

    snapshots = await asyncio.gather(*(load(nm) for nm in ordered_ids))

    candidates: list[WbSimilarProduct] = []
    for nm_id, snap in snapshots:
        if snap is None or snap.price is None or snap.price >= max_price:
            continue
        if base_subject_id is not None and snap.subject_id is not None and snap.subject_id != base_subject_id:
            continue

        candidate_text = f"{snap.title} {snap.entity or ''}"
        candidate_tokens = _characteristic_tokens(candidate_text)

        if not _is_ecosystem_compatible(base_ecosystem, candidate_tokens):
            continue
        if base_model_tokens and not _model_tokens_compatible(base_model_tokens, candidate_text, candidate_tokens):
            continue
        if base_brand_tokens:
            cand_brand_tokens = set(_tokenize(snap.brand or ""))
            if cand_brand_tokens and _match_count(base_brand_tokens, cand_brand_tokens) == 0:
                continue
        if required_anchor_matches > 0 and _match_count(base_anchor_tokens, candidate_tokens) < required_anchor_matches:
            continue
        if base_type_tokens and _match_count(base_type_tokens, candidate_tokens) == 0:
            continue
        if min_match_percent > 0 and _match_percent(base_tokens, candidate_tokens) < min_match_percent:
            continue

        candidates.append(
            WbSimilarProduct(
                wb_item_id=nm_id,
                title=snap.title,
                price=snap.price,
                url=f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx",
            )
        )

    candidates.sort(key=lambda p: p.price)
    return candidates[:limit]


async def search_similar_cheaper(
    *,
    base_title: str,
    max_price: Decimal,
    exclude_wb_item_id: int,
    base_entity: str | None = None,
    base_brand: str | None = None,
    base_subject_id: int | None = None,
    match_percent_threshold: int | None = None,
    limit: int = 5,
    session: ClientSession | None = None,
) -> list[WbSimilarProduct]:
    if session is None:
        async with ClientSession(headers=WB_HTTP_HEADERS) as new_session:
            return await _search_similar_all_sources(
                new_session,
                base_title=base_title,
                max_price=max_price,
                exclude_wb_item_id=exclude_wb_item_id,
                base_entity=base_entity,
                base_brand=base_brand,
                base_subject_id=base_subject_id,
                match_percent_threshold=match_percent_threshold,
                limit=limit,
            )

    return await _search_similar_all_sources(
        session,
        base_title=base_title,
        max_price=max_price,
        exclude_wb_item_id=exclude_wb_item_id,
        base_entity=base_entity,
        base_brand=base_brand,
        base_subject_id=base_subject_id,
        match_percent_threshold=match_percent_threshold,
        limit=limit,
    )


async def _search_similar_all_sources(
    session: ClientSession,
    *,
    base_title: str,
    max_price: Decimal,
    exclude_wb_item_id: int,
    base_entity: str | None,
    base_brand: str | None,
    base_subject_id: int | None,
    match_percent_threshold: int | None,
    limit: int,
) -> list[WbSimilarProduct]:
    base_text = f"{base_title} {base_entity or ''}"
    base_gender = _detect_gender(base_text)
    base_tokens = _characteristic_tokens(base_text)
    base_model_tokens = _extract_model_tokens(base_text)
    if not base_tokens:
        return []
    raw_brand_tokens = set(_tokenize(base_brand or ""))
    base_brand_tokens = (
        raw_brand_tokens
        if raw_brand_tokens and _match_count(raw_brand_tokens, base_tokens) > 0
        else set()
    )
    base_anchor_tokens = _anchor_tokens(base_tokens)
    base_type_tokens = {token for token in base_tokens if token in _TYPE_TOKENS}
    required_anchor_matches = _required_anchor_matches(base_anchor_tokens)
    strong_anchor_count = len(
        [token for token in base_anchor_tokens if _is_latin_or_digit_token(token)]
    )
    base_ecosystem = _detect_ecosystem(base_tokens)
    strict_match_percent = _normalize_match_percent(match_percent_threshold)
    relaxed_match_percent = max(20, strict_match_percent - 15)
    minimal_match_percent = max(10, strict_match_percent - 30)

    combined: list[WbSimilarProduct] = []
    seen_ids: set[int] = set()

    async def collect_pass(
        *,
        min_match_percent: int,
        enforce_gender: bool,
        min_relevance: int,
        required_anchor_matches_for_pass: int,
        require_model_tokens: bool,
    ) -> None:
        if len(combined) >= limit:
            return

        by_search = await _search_similar_with_search(
            session,
            base_title=base_title,
            base_gender=base_gender,
            base_tokens=base_tokens,
            base_brand_tokens=base_brand_tokens,
            base_anchor_tokens=base_anchor_tokens,
            base_type_tokens=base_type_tokens,
            base_model_tokens=base_model_tokens,
            required_anchor_matches=required_anchor_matches_for_pass,
            require_model_tokens=require_model_tokens,
            base_ecosystem=base_ecosystem,
            base_subject_id=base_subject_id,
            min_match_percent=min_match_percent,
            enforce_gender=enforce_gender,
            max_price=max_price,
            exclude_wb_item_id=exclude_wb_item_id,
            limit=limit - len(combined),
            skip_ids=seen_ids,
        )
        for item in by_search:
            if item.wb_item_id in seen_ids:
                continue
            combined.append(item)
            seen_ids.add(item.wb_item_id)

        if len(combined) >= limit:
            return

        by_catalog = await _search_similar_with_catalog(
            session,
            base_title=base_title,
            base_entity=base_entity,
            base_gender=base_gender,
            base_tokens=base_tokens,
            base_brand_tokens=base_brand_tokens,
            base_anchor_tokens=base_anchor_tokens,
            base_type_tokens=base_type_tokens,
            base_model_tokens=base_model_tokens,
            required_anchor_matches=required_anchor_matches_for_pass,
            require_model_tokens=require_model_tokens,
            base_ecosystem=base_ecosystem,
            base_subject_id=base_subject_id,
            min_match_percent=min_match_percent,
            enforce_gender=enforce_gender,
            min_relevance=min_relevance,
            max_price=max_price,
            exclude_wb_item_id=exclude_wb_item_id,
            limit=limit - len(combined),
            skip_ids=seen_ids,
        )
        for item in by_catalog:
            if item.wb_item_id in seen_ids:
                continue
            combined.append(item)
            seen_ids.add(item.wb_item_id)

    await collect_pass(
        min_match_percent=strict_match_percent,
        enforce_gender=True,
        min_relevance=2 if len(base_tokens) >= 3 else 1,
        required_anchor_matches_for_pass=required_anchor_matches,
        require_model_tokens=True,
    )
    if not combined:
        relaxed_anchor_matches = (
            required_anchor_matches
            if strong_anchor_count >= 2
            else max(1, required_anchor_matches - 1)
        )
        await collect_pass(
            min_match_percent=relaxed_match_percent,
            enforce_gender=False,
            min_relevance=1,
            required_anchor_matches_for_pass=relaxed_anchor_matches,
            require_model_tokens=True,
        )
    if not combined:
        minimal_anchor_matches = (
            required_anchor_matches
            if strong_anchor_count >= 2
            else max(1, required_anchor_matches - 1)
        )
        await collect_pass(
            min_match_percent=minimal_match_percent,
            enforce_gender=False,
            min_relevance=0,
            required_anchor_matches_for_pass=minimal_anchor_matches,
            require_model_tokens=True,
        )

    if not combined and base_model_tokens:
        await collect_pass(
            min_match_percent=minimal_match_percent,
            enforce_gender=False,
            min_relevance=0,
            required_anchor_matches_for_pass=max(0, minimal_anchor_matches - 1),
            require_model_tokens=False,
        )

    combined.sort(key=lambda p: p.price)
    return combined[:limit]


async def _search_similar_with_search(
    session: ClientSession,
    *,
    base_title: str,
    base_gender: str | None,
    base_tokens: set[str],
    base_brand_tokens: set[str],
    base_anchor_tokens: set[str],
    base_type_tokens: set[str],
    base_model_tokens: set[str],
    required_anchor_matches: int,
    require_model_tokens: bool,
    base_ecosystem: str | None,
    base_subject_id: int | None,
    min_match_percent: int,
    enforce_gender: bool,
    max_price: Decimal,
    exclude_wb_item_id: int,
    limit: int,
    skip_ids: set[int],
) -> list[WbSimilarProduct]:
    query = _build_search_query(base_title)
    if not query:
        return []

    encoded_query = quote_plus(query)
    seen_ids: set[int] = set(skip_ids)
    similar: list[WbSimilarProduct] = []

    for template in SEARCH_WB_URLS:
        for page in range(1, 3):
            url = template.format(page=page, query=encoded_query)
            data = await _get_json_with_retries(session, url)
            if not isinstance(data, dict):
                continue

            products_raw = data.get("products")
            if isinstance(products_raw, list):
                products = products_raw
            else:
                nested = data.get("data")
                nested_products = (
                    nested.get("products") if isinstance(nested, dict) else None
                )
                products = nested_products if isinstance(nested_products, list) else []

            for product in products:
                if not isinstance(product, dict):
                    continue

                nm_id = _parse_int(product.get("id") or product.get("nmId"))
                if nm_id is None:
                    continue

                if nm_id in seen_ids or nm_id == exclude_wb_item_id:
                    continue

                if base_subject_id is not None:
                    candidate_subject_id = _parse_int(
                        product.get("subjectId") or product.get("subjectID")
                    )
                    if (
                        candidate_subject_id is not None
                        and candidate_subject_id != base_subject_id
                    ):
                        continue

                price = _extract_price(product)
                if price is None or price >= max_price:
                    continue

                title = str(
                    product.get("name") or product.get("imt_name") or f"WB #{nm_id}"
                )
                candidate_text = f"{title} {str(product.get('entity') or '')}"
                if enforce_gender and base_gender is not None:
                    candidate_gender = _detect_gender(candidate_text)
                    if candidate_gender is not None and candidate_gender != base_gender:
                        continue

                candidate_tokens = _characteristic_tokens(candidate_text)
                if not _is_ecosystem_compatible(base_ecosystem, candidate_tokens):
                    continue
                if require_model_tokens and not _model_tokens_compatible(
                    base_model_tokens,
                    candidate_text,
                    candidate_tokens,
                ):
                    continue
                if base_brand_tokens:
                    candidate_brand_tokens = set(
                        _tokenize(str(product.get("brand") or ""))
                    )
                    if candidate_brand_tokens and (
                        _match_count(base_brand_tokens, candidate_brand_tokens) == 0
                    ):
                        continue
                if required_anchor_matches > 0:
                    anchor_hits = _match_count(base_anchor_tokens, candidate_tokens)
                    if anchor_hits < required_anchor_matches:
                        continue
                if (
                    base_type_tokens
                    and _match_count(base_type_tokens, candidate_tokens) == 0
                ):
                    continue

                if min_match_percent > 0:
                    match_percent = _match_percent(
                        base_tokens,
                        candidate_tokens,
                    )
                    if match_percent < min_match_percent:
                        continue

                similar.append(
                    WbSimilarProduct(
                        wb_item_id=nm_id,
                        title=title,
                        price=price,
                        url=f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx",
                    )
                )
                seen_ids.add(nm_id)

            if len(similar) >= limit:
                break
        if len(similar) >= limit:
            break

    similar.sort(key=lambda p: p.price)
    return similar[:limit]


async def _load_catalog_categories(session: ClientSession) -> list[WbCatalogCategory]:
    global _MENU_CACHE, _MENU_CACHE_TS

    now = time.monotonic()
    if _MENU_CACHE is not None and now - _MENU_CACHE_TS < _MENU_CACHE_TTL_SEC:
        return _MENU_CACHE

    raw_menu = await _get_json_with_retries(session, MENU_URL)
    if raw_menu is None:
        return _MENU_CACHE or []

    categories: list[WbCatalogCategory] = []
    seen: set[tuple[str, str]] = set()

    def walk(items: list[object]) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue

            name = str(item.get("name") or "").strip()
            shard = item.get("shard")
            query = item.get("query")
            if (
                isinstance(shard, str)
                and shard
                and shard not in {"blackhole", "c2c"}
                and isinstance(query, str)
                and query
            ):
                key = (shard, query)
                if key not in seen:
                    seen.add(key)
                    categories.append(
                        WbCatalogCategory(
                            name=name,
                            shard=shard,
                            query=query,
                            tokens=frozenset(_tokenize(name)),
                        )
                    )

            children = item.get("childs") or item.get("children") or []
            if isinstance(children, list):
                walk(children)

    if isinstance(raw_menu, list):
        walk(raw_menu)

    _MENU_CACHE = categories
    _MENU_CACHE_TS = now
    return categories


async def _fetch_catalog_products(
    session: ClientSession,
    *,
    shard: str,
    query: str,
    page: int,
    subject_id: int | None = None,
) -> list[dict[str, object]]:
    subject_part = f"&subject={subject_id}" if subject_id is not None else ""
    url = f"{CATALOG_URL.format(shard=shard, page=page)}&{query}{subject_part}"
    data = await _get_json_with_retries(session, url)
    if not isinstance(data, dict):
        return []

    products = data.get("products")
    if not isinstance(products, list):
        return []
    return [p for p in products if isinstance(p, dict)]


async def _search_similar_with_catalog(
    session: ClientSession,
    *,
    base_title: str,
    base_entity: str | None,
    base_gender: str | None,
    base_tokens: set[str],
    base_brand_tokens: set[str],
    base_anchor_tokens: set[str],
    base_type_tokens: set[str],
    base_model_tokens: set[str],
    required_anchor_matches: int,
    require_model_tokens: bool,
    base_ecosystem: str | None,
    base_subject_id: int | None,
    min_match_percent: int,
    enforce_gender: bool,
    min_relevance: int,
    max_price: Decimal,
    exclude_wb_item_id: int,
    limit: int,
    skip_ids: set[int],
) -> list[WbSimilarProduct]:
    if not base_tokens:
        return []
    if min_relevance < 0:
        min_relevance = 0

    categories = await _load_catalog_categories(session)
    scored_categories: list[tuple[int, WbCatalogCategory]] = []
    for category in categories:
        score = _overlap_score(base_tokens, category.tokens)
        if score > 0:
            scored_categories.append((score, category))

    if not scored_categories:
        return []

    scored_categories.sort(key=lambda x: x[0], reverse=True)
    candidates: list[tuple[int, WbSimilarProduct]] = []
    seen_ids: set[int] = set(skip_ids)
    max_categories = 8

    for _, category in scored_categories[:max_categories]:
        for page in range(1, 3):
            products = await _fetch_catalog_products(
                session,
                shard=category.shard,
                query=category.query,
                page=page,
                subject_id=base_subject_id,
            )
            if not products and base_subject_id is not None:
                products = await _fetch_catalog_products(
                    session,
                    shard=category.shard,
                    query=category.query,
                    page=page,
                    subject_id=None,
                )
            if not products:
                break

            for product in products:
                nm_id = _parse_int(product.get("id") or product.get("nmId"))
                if nm_id is None:
                    continue

                if nm_id in seen_ids or nm_id == exclude_wb_item_id:
                    continue

                if base_subject_id is not None:
                    candidate_subject_id = _parse_int(
                        product.get("subjectId") or product.get("subjectID")
                    )
                    if (
                        candidate_subject_id is not None
                        and candidate_subject_id != base_subject_id
                    ):
                        continue

                price = _extract_price(product)
                if price is None or price >= max_price:
                    continue

                title = str(
                    product.get("name") or product.get("imt_name") or f"WB #{nm_id}"
                )
                candidate_text = f"{title} {str(product.get('entity') or '')}"
                if enforce_gender and base_gender is not None:
                    candidate_gender = _detect_gender(candidate_text)
                    if candidate_gender is not None and candidate_gender != base_gender:
                        continue

                candidate_tokens = _characteristic_tokens(candidate_text)
                if not _is_ecosystem_compatible(base_ecosystem, candidate_tokens):
                    continue
                if require_model_tokens and not _model_tokens_compatible(
                    base_model_tokens,
                    candidate_text,
                    candidate_tokens,
                ):
                    continue
                if base_brand_tokens:
                    candidate_brand_tokens = set(
                        _tokenize(str(product.get("brand") or ""))
                    )
                    if candidate_brand_tokens and (
                        _match_count(base_brand_tokens, candidate_brand_tokens) == 0
                    ):
                        continue
                if required_anchor_matches > 0:
                    anchor_hits = _match_count(base_anchor_tokens, candidate_tokens)
                    if anchor_hits < required_anchor_matches:
                        continue
                if (
                    base_type_tokens
                    and _match_count(base_type_tokens, candidate_tokens) == 0
                ):
                    continue

                if min_match_percent > 0:
                    match_percent = _match_percent(
                        base_tokens,
                        candidate_tokens,
                    )
                    if match_percent < min_match_percent:
                        continue

                relevance = _overlap_score(base_tokens, set(_tokenize(title)))
                if relevance < min_relevance:
                    continue

                candidates.append(
                    (
                        relevance,
                        WbSimilarProduct(
                            wb_item_id=nm_id,
                            title=title,
                            price=price,
                            url=f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx",
                        ),
                    )
                )
                seen_ids.add(nm_id)

            if len(candidates) >= limit:
                break
        if len(candidates) >= limit:
            break

    candidates.sort(key=lambda x: (x[1].price, -x[0]))
    return [item for _, item in candidates[:limit]]


async def _fetch_and_cache(
    session: ClientSession,
    redis: "Redis",
    url: str,
    wb_item_id: int,
) -> WbProductSnapshot | None:
    async with session.get(url, timeout=20, proxy=WB_HTTP_PROXY) as resp:
        if resp.status != 200:
            return None
        data = await resp.json(content_type=None)

    products = data.get("products") or data.get("data", {}).get("products", [])
    if not products:
        return None

    p = products[0]
    if not isinstance(p, dict):
        return None
    title = str(p.get("name") or p.get("imt_name") or f"WB #{wb_item_id}")
    sizes_data = p.get("sizes", []) if isinstance(p.get("sizes"), list) else []

    def _norm_size(raw: object) -> str | None:
        val = str(raw or "").strip()
        if not val or val in {"0", "00", "none", "None"}:
            return None
        return val

    sizes = sorted(
        {
            size
            for s in sizes_data
            for size in [_norm_size(s.get("name"))]
            if size is not None
        }
    )

    in_stock = False
    total_qty = 0
    for s in sizes_data:
        for stock in s.get("stocks") or []:
            if isinstance(stock, dict):
                qty = int(stock.get("qty", 0))
                if qty > 0:
                    in_stock = True
                total_qty += max(0, qty)

    price = _extract_price(p)
    rating = _extract_rating(p)
    reviews = _extract_reviews(p)

    snap = WbProductSnapshot(
        wb_item_id=wb_item_id,
        title=title,
        price=price,
        rating=rating,
        reviews=reviews,
        in_stock=in_stock,
        total_qty=total_qty,
        sizes=sizes,
        brand=str(p.get("brand")) if p.get("brand") else None,
        entity=str(p.get("entity")) if p.get("entity") else None,
        subject_id=_parse_int(p.get("subjectId")),
    )

    await WbItemCacheRD(
        wb_item_id=wb_item_id,
        title=snap.title,
        price=str(snap.price) if snap.price is not None else None,
        rating=str(snap.rating) if snap.rating is not None else None,
        reviews=snap.reviews,
        in_stock=snap.in_stock,
        total_qty=snap.total_qty,
        sizes=snap.sizes,
    ).save(redis)

    return snap
