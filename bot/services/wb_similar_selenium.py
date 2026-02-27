from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Iterator

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

logger = logging.getLogger(__name__)

_SIMILAR_SECTION_RE = re.compile(
    r"похожие товары|похожие|с этим товаром покупают|рекомендуем",
    re.IGNORECASE,
)
_NM_ID_RE = re.compile(r"/catalog/(\d{6,15})/detail\.aspx", re.IGNORECASE)
_INT_RE = re.compile(r"\d+")
_DECIMAL_RE = re.compile(r"\d+(?:[.,]\d+)?")
_SIMILAR_URL_HINTS = (
    "similar",
    "related",
    "recommend",
    "recom",
    "cross",
    "analog",
    "same",
    "together",
    "bundle",
)


@dataclass(frozen=True)
class WbSimilarProductItem:
    nm_id: int
    title: str
    brand: str | None
    final_price: Decimal | None
    sale_price: Decimal | None
    rating: Decimal | None
    feedbacks: int | None
    product_url: str


@contextmanager
def _chrome_driver(*, headless: bool, timeout_sec: float) -> Iterator[webdriver.Chrome]:
    options = Options()
    if headless:
        # Snap Chromium on some servers is more stable with classic headless mode.
        options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-setuid-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-zygote")
    options.add_argument("--remote-debugging-port=9222")
    options.add_argument("--window-size=1280,2000")
    options.add_argument("--lang=ru-RU")

    chrome_binary = os.environ.get("WB_CHROME_BINARY")
    if chrome_binary:
        options.binary_location = chrome_binary

    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    service = _build_chrome_service()
    driver: webdriver.Chrome | None = None
    try:
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(timeout_sec)
        driver.set_script_timeout(timeout_sec)
        driver.execute_cdp_cmd("Network.enable", {})
        yield driver
    finally:
        if driver is not None:
            try:
                driver.quit()
            except WebDriverException:
                pass


def _build_chrome_service() -> ChromeService:
    chromedriver_path = os.environ.get("WB_CHROMEDRIVER_PATH")
    if chromedriver_path:
        return ChromeService(executable_path=chromedriver_path)
    # Prefer Selenium Manager auto-resolution to match installed Chrome/Chromium.
    return ChromeService()


def fetch_similar_products(
    nm_id: int,
    *,
    limit: int = 20,
    timeout_sec: float = 20.0,
    headless: bool = True,
) -> list[WbSimilarProductItem]:
    if limit <= 0:
        return []

    recommendation_url = _recommendation_url(nm_id)
    with _chrome_driver(headless=headless, timeout_sec=timeout_sec) as driver:
        try:
            driver.get(recommendation_url)
        except TimeoutException:
            logger.warning("Timeout while loading %s", recommendation_url)

        _wait_for_page(driver, timeout_sec)
        items = _collect_from_recommendation_dom(driver, limit=limit)
        if len(items) >= max(1, min(limit, 3)):
            return items[:limit]

        # Fallback to product page parsing + network extraction.
        url = _product_url(nm_id)
        try:
            driver.get(url)
        except TimeoutException:
            logger.warning("Timeout while loading %s", url)
        _wait_for_page(driver, timeout_sec)
        _scroll_for_similar(driver)

        dom_items = _collect_from_dom(driver, limit=limit)
        network_items = _collect_from_network(driver, limit=limit)
        merged = _merge_items(items + dom_items, network_items, limit=limit)
        return merged[:limit]


def _product_url(nm_id: int) -> str:
    return f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"


def _recommendation_url(nm_id: int) -> str:
    return f"https://www.wildberries.ru/recommendation/catalog?type=visuallysimilar&forproduct={nm_id}"


def _wait_for_page(driver: webdriver.Chrome, timeout_sec: float) -> None:
    try:
        wait = WebDriverWait(driver, timeout_sec)
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    except TimeoutException:
        logger.debug("Timed out waiting for page readiness")


def _scroll_for_similar(driver: webdriver.Chrome) -> None:
    try:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.55);")
        time.sleep(0.6)
    except WebDriverException:
        pass


def _collect_from_recommendation_dom(
    driver: webdriver.Chrome,
    *,
    limit: int,
) -> list[WbSimilarProductItem]:
    try:
        wait = WebDriverWait(driver, 12)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "article")))
    except TimeoutException:
        return []

    articles = driver.find_elements(By.CSS_SELECTOR, "article")
    items: list[WbSimilarProductItem] = []

    for article in articles:
        try:
            anchor = article.find_element(By.CSS_SELECTOR, "a[href*='/catalog/'][href*='detail.aspx']")
        except WebDriverException:
            continue

        href = anchor.get_attribute("href") or ""
        nm_id = _parse_nm_id_from_url(href)
        if nm_id is None:
            continue

        title = (anchor.get_attribute("aria-label") or anchor.get_attribute("title") or anchor.text or "").strip()
        if not title:
            title = _first_text(article, ["h2", "h3"]) or f"Item {nm_id}"

        heading = _first_text(article, ["h2", "h3"])
        brand: str | None = None
        if heading and " / " in heading:
            brand = heading.split(" / ", 1)[0].strip() or None

        final_price = _parse_price_text(_first_text(article, ["ins", "ins bdi", "ins span"]))
        sale_price = _parse_price_text(_first_text(article, ["del", "del bdi", "del span"]))

        rating: Decimal | None = None
        feedbacks: int | None = None
        rating_block = _first_text(article, ["[class*='rating']"])
        if rating_block:
            rating = _parse_decimal(rating_block)
            feedbacks = _parse_int(rating_block)

        items.append(
            WbSimilarProductItem(
                nm_id=nm_id,
                title=title,
                brand=brand,
                final_price=final_price,
                sale_price=sale_price,
                rating=rating,
                feedbacks=feedbacks,
                product_url=_normalize_url(href, nm_id),
            )
        )

        if len(items) >= limit:
            break

    return _dedupe_items(items, limit=limit)


def _collect_from_dom(
    driver: webdriver.Chrome,
    *,
    limit: int,
) -> list[WbSimilarProductItem]:
    section_root = _find_similar_section(driver)
    scope = section_root if section_root is not None else driver
    anchors = scope.find_elements(By.CSS_SELECTOR, "a[href*='/catalog/'][href*='detail.aspx']")
    items: list[WbSimilarProductItem] = []
    for anchor in anchors:
        item = _extract_from_anchor(anchor)
        if item is None:
            continue
        items.append(item)
        if len(items) >= limit:
            break
    return _dedupe_items(items, limit=limit)


def _find_similar_section(driver: webdriver.Chrome):
    headings = driver.find_elements(By.XPATH, "//h1|//h2|//h3|//h4")
    for heading in headings:
        text = (heading.text or "").strip()
        if text and _SIMILAR_SECTION_RE.search(text):
            for xpath in ("./ancestor::section[1]", "./ancestor::div[1]"):
                try:
                    return heading.find_element(By.XPATH, xpath)
                except WebDriverException:
                    continue
    return None


def _extract_from_anchor(anchor) -> WbSimilarProductItem | None:
    href = anchor.get_attribute("href") or ""
    nm_id = _parse_nm_id(anchor.get_attribute("data-nm-id"))
    if nm_id is None:
        nm_id = _parse_nm_id(anchor.get_attribute("data-nm"))
    if nm_id is None:
        nm_id = _parse_nm_id_from_url(href)
    if nm_id is None:
        return None

    card = _closest_card(anchor)
    title = _first_text(
        card,
        [
            ".product-card__name",
            ".product-card__name span",
            ".product-card__name-text",
            ".product-card__name-link",
            ".product-card__title",
        ],
    )
    if not title:
        title = (anchor.get_attribute("title") or anchor.text or "").strip()
    if not title:
        title = f"Item {nm_id}"

    brand = _first_text(
        card,
        [
            ".product-card__brand",
            ".product-card__brand span",
            ".product-card__brand-name",
        ],
    )

    price_text = _first_text(
        card,
        [
            ".price__lower-price",
            ".price__lower",
            ".price__sale",
            ".product-card__price",
            ".price",
        ],
    )
    final_price = _parse_price_text(price_text)

    sale_text = _first_text(
        card,
        [
            ".price__old",
            ".price__upper",
            ".product-card__old-price",
            ".price__basic",
        ],
    )
    sale_price = _parse_price_text(sale_text)

    rating_text = _first_text(
        card,
        [
            ".product-card__rating",
            ".rating",
            ".product-card__rating-count",
        ],
    )
    rating = _parse_decimal(rating_text)

    feedbacks_text = _first_text(
        card,
        [
            ".product-card__count",
            ".product-card__reviews",
            ".rating__count",
        ],
    )
    feedbacks = _parse_int(feedbacks_text)

    return WbSimilarProductItem(
        nm_id=nm_id,
        title=title,
        brand=brand,
        final_price=final_price,
        sale_price=sale_price,
        rating=rating,
        feedbacks=feedbacks,
        product_url=_normalize_url(href, nm_id),
    )


def _closest_card(anchor):
    try:
        return anchor.find_element(
            By.XPATH,
            "./ancestor::*[contains(@class,'product-card')][1]",
        )
    except WebDriverException:
        return anchor


def _first_text(container, selectors: Iterable[str]) -> str | None:
    if container is None:
        return None
    for selector in selectors:
        try:
            elem = container.find_element(By.CSS_SELECTOR, selector)
        except WebDriverException:
            continue
        text = (elem.text or "").strip()
        if text:
            return text
    return None


def _parse_nm_id(raw: str | None) -> int | None:
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _parse_nm_id_from_url(url: str) -> int | None:
    match = _NM_ID_RE.search(url)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _parse_int(text: str | None) -> int | None:
    if not text:
        return None
    match = _INT_RE.search(text.replace("\xa0", " "))
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def _parse_decimal(text: str | None) -> Decimal | None:
    if not text:
        return None
    match = _DECIMAL_RE.search(text.replace("\xa0", " "))
    if not match:
        return None
    normalized = match.group(0).replace(",", ".")
    try:
        return Decimal(normalized)
    except Exception:
        return None


def _parse_price_text(text: str | None) -> Decimal | None:
    if not text:
        return None
    digits = _INT_RE.findall(text.replace("\xa0", " "))
    if not digits:
        return None
    try:
        return Decimal("".join(digits))
    except Exception:
        return None


def _normalize_url(url: str, nm_id: int) -> str:
    if url:
        return url
    return _product_url(nm_id)


def _collect_from_network(
    driver: webdriver.Chrome,
    *,
    limit: int,
) -> list[WbSimilarProductItem]:
    responses = _collect_json_responses(driver)
    items: list[WbSimilarProductItem] = []

    filtered = [resp for resp in responses if _url_has_hint(resp[1])]
    if not filtered:
        filtered = responses

    for body, url in filtered:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            continue
        for product in _extract_products(payload):
            item = _from_product_dict(product, url_hint=url)
            if item is None:
                continue
            items.append(item)
            if len(items) >= limit:
                return _dedupe_items(items, limit=limit)

    return _dedupe_items(items, limit=limit)


def _collect_json_responses(driver: webdriver.Chrome) -> list[tuple[str, str]]:
    try:
        logs = driver.get_log("performance")
    except WebDriverException:
        return []

    responses: list[tuple[str, str]] = []
    seen_request_ids: set[str] = set()
    for entry in logs:
        message_text = entry.get("message")
        if not message_text:
            continue
        try:
            message = json.loads(message_text)
        except json.JSONDecodeError:
            continue
        if message.get("message", {}).get("method") != "Network.responseReceived":
            continue
        params = message.get("message", {}).get("params", {})
        response = params.get("response", {})
        mime_type = response.get("mimeType", "")
        if "json" not in mime_type:
            continue
        request_id = params.get("requestId")
        url = response.get("url")
        if not request_id or not url or request_id in seen_request_ids:
            continue
        seen_request_ids.add(request_id)
        body = _get_response_body(driver, request_id)
        if body is None:
            continue
        responses.append((body, url))
    return responses


def _get_response_body(driver: webdriver.Chrome, request_id: str) -> str | None:
    try:
        data = driver.execute_cdp_cmd(
            "Network.getResponseBody", {"requestId": request_id}
        )
    except WebDriverException:
        return None
    body = data.get("body")
    if not isinstance(body, str):
        return None
    if data.get("base64Encoded"):
        try:
            return base64.b64decode(body).decode("utf-8", "replace")
        except Exception:
            return None
    return body


def _url_has_hint(url: str) -> bool:
    lowered = url.lower()
    return any(hint in lowered for hint in _SIMILAR_URL_HINTS)


def _extract_products(payload: Any) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if _looks_like_product(node):
                products.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return products


def _looks_like_product(node: dict[str, Any]) -> bool:
    nm_id = _first_int(
        node,
        (
            "nmId",
            "nm_id",
            "id",
            "id_nomenclature",
            "nmid",
        ),
    )
    if nm_id is None:
        return False
    has_name = any(
        key in node for key in ("name", "title", "productName", "goodsName")
    )
    has_price = any(
        key in node
        for key in (
            "salePriceU",
            "priceU",
            "salePrice",
            "price",
            "sizes",
        )
    )
    return has_name or has_price


def _first_int(node: dict[str, Any], keys: Iterable[str]) -> int | None:
    for key in keys:
        value = node.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            try:
                return int(value)
            except ValueError:
                continue
        if isinstance(value, str):
            value = value.strip()
            if value.isdigit():
                try:
                    return int(value)
                except ValueError:
                    continue
    return None


def _from_product_dict(product: dict[str, Any], *, url_hint: str) -> WbSimilarProductItem | None:
    nm_id = _first_int(
        product,
        (
            "nmId",
            "nm_id",
            "id",
            "id_nomenclature",
            "nmid",
        ),
    )
    if nm_id is None:
        return None

    title = _first_text_value(
        product,
        ("name", "title", "productName", "goodsName"),
    )
    if not title:
        title = f"Item {nm_id}"

    brand = _first_text_value(
        product,
        ("brand", "brandName", "tradeMark", "supplier", "supplierName"),
    )

    final_price, sale_price = _extract_prices(product)
    rating = _extract_decimal(product, ("nmReviewRating", "reviewRating", "rating"))
    feedbacks = _extract_int(product, ("nmFeedbacks", "feedbacks", "reviews"))

    url = _first_text_value(product, ("url", "link", "productUrl"))
    if not url:
        url = _product_url(nm_id)

    return WbSimilarProductItem(
        nm_id=nm_id,
        title=title,
        brand=brand,
        final_price=final_price,
        sale_price=sale_price,
        rating=rating,
        feedbacks=feedbacks,
        product_url=_normalize_url(url, nm_id),
    )


def _first_text_value(node: dict[str, Any], keys: Iterable[str]) -> str | None:
    for key in keys:
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_prices(product: dict[str, Any]) -> tuple[Decimal | None, Decimal | None]:
    sale_raw = product.get("salePriceU")
    base_raw = product.get("priceU")

    if not isinstance(sale_raw, (int, float)):
        sizes_data = product.get("sizes")
        if isinstance(sizes_data, list) and sizes_data:
            first_size = sizes_data[0]
            if isinstance(first_size, dict):
                price_data = first_size.get("price")
                if isinstance(price_data, dict):
                    sale_raw = price_data.get("product")
                    base_raw = price_data.get("basic")

    if sale_raw is None:
        sale_raw = product.get("salePrice")
    if base_raw is None:
        base_raw = product.get("price")

    final_price = _normalize_price(sale_raw, key_hint="salePriceU")
    sale_price = _normalize_price(base_raw, key_hint="priceU")

    if final_price == sale_price:
        sale_price = None
    return final_price, sale_price


def _normalize_price(value: Any, *, key_hint: str | None = None) -> Decimal | None:
    if not isinstance(value, (int, float)):
        return None
    price = Decimal(str(value))
    if key_hint and key_hint.lower().endswith("u"):
        return price / Decimal("100")
    if price >= Decimal("10000"):
        return price / Decimal("100")
    return price


def _extract_decimal(product: dict[str, Any], keys: Iterable[str]) -> Decimal | None:
    for key in keys:
        value = product.get(key)
        if isinstance(value, (int, float)):
            return Decimal(str(value))
        if isinstance(value, str):
            parsed = _parse_decimal(value)
            if parsed is not None:
                return parsed
    return None


def _extract_int(product: dict[str, Any], keys: Iterable[str]) -> int | None:
    for key in keys:
        value = product.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return max(0, value)
        if isinstance(value, str):
            parsed = _parse_int(value)
            if parsed is not None:
                return max(0, parsed)
    return None


def _merge_items(
    primary: list[WbSimilarProductItem],
    secondary: list[WbSimilarProductItem],
    *,
    limit: int,
) -> list[WbSimilarProductItem]:
    combined = primary + secondary
    return _dedupe_items(combined, limit=limit)


def _dedupe_items(
    items: list[WbSimilarProductItem],
    *,
    limit: int,
) -> list[WbSimilarProductItem]:
    seen: set[int] = set()
    deduped: list[WbSimilarProductItem] = []
    for item in items:
        if item.nm_id in seen:
            continue
        seen.add(item.nm_id)
        deduped.append(item)
        if len(deduped) >= limit:
            break
    return deduped
