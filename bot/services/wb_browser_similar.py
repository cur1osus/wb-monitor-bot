from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation

from bot.services.wb_client import WB_HTTP_HEADERS, WbSimilarProduct

try:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    from playwright.async_api import async_playwright
except Exception:  # pragma: no cover
    PlaywrightTimeoutError = TimeoutError  # type: ignore[assignment]
    async_playwright = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_VISUAL_SIMILAR_URL = "https://www.wildberries.ru/recommendation/catalog?type=visuallysimilar&forproduct={wb_item_id}"
_WB_ITEM_IN_URL_RE = re.compile(r"/catalog/(\d{6,15})(?:/detail\.aspx)?", re.IGNORECASE)
_ANTIBOT_MARKERS = (
    "почти готово",
    "verify you are human",
    "captcha",
    "access denied",
)
_ANTIBOT_URL_HINTS = ("/security/", "anti_bot", "captcha")


def _parse_price(text: str) -> Decimal | None:
    cleaned = (
        text.replace("\xa0", " ")
        .replace("₽", "")
        .replace("руб", "")
        .replace("р.", "")
        .strip()
    )
    if not cleaned:
        return None

    match = re.search(r"\d[\d\s.,]*", cleaned)
    if not match:
        return None

    number = match.group(0).replace(" ", "")
    if number.count(",") and number.count("."):
        if number.rfind(",") > number.rfind("."):
            number = number.replace(".", "").replace(",", ".")
        else:
            number = number.replace(",", "")
    elif number.count(","):
        number = number.replace(",", ".")

    try:
        value = Decimal(number)
    except InvalidOperation:
        return None
    if value <= 0:
        return None
    return value


def _normalize_title(title: str, fallback: str) -> str:
    raw = " ".join(title.split())
    if raw:
        return raw
    return fallback


def _extract_antibot_marker(content: str) -> str | None:
    lowered = content.lower()
    for marker in _ANTIBOT_MARKERS:
        if marker in lowered:
            return marker
    return None


async def fetch_visual_similar_products(
    wb_item_id: int,
    *,
    limit: int = 20,
    timeout_sec: int = 20,
) -> list[WbSimilarProduct]:
    if async_playwright is None:
        logger.warning("Playwright is not installed; browser similar provider skipped")
        return []

    safe_limit = max(1, int(limit))
    timeout_ms = max(3_000, int(timeout_sec * 1000))
    url = _VISUAL_SIMILAR_URL.format(wb_item_id=wb_item_id)

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                locale="ru-RU",
                user_agent=WB_HTTP_HEADERS["User-Agent"],
                viewport={"width": 1366, "height": 900},
            )
            page = await context.new_page()

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                await page.wait_for_timeout(1200)
                content = await page.content()
                marker = _extract_antibot_marker(content)
                title = (await page.title()).strip()
                final_url = page.url

                if marker or any(
                    hint in final_url.lower() for hint in _ANTIBOT_URL_HINTS
                ):
                    logger.warning(
                        "WB_BROWSER_SIMILAR_ANTIBOT item=%s marker=%s final_url=%s title=%s",
                        wb_item_id,
                        marker or "url_hint",
                        final_url,
                        title,
                    )
                    return []

                try:
                    await page.wait_for_selector(
                        'a[href*="/catalog/"][href*="detail.aspx"]',
                        timeout=timeout_ms,
                    )
                except PlaywrightTimeoutError:
                    title = (await page.title()).strip()
                    logger.warning(
                        "WB_BROWSER_SIMILAR_TIMEOUT item=%s final_url=%s title=%s",
                        wb_item_id,
                        page.url,
                        title,
                    )
                    return []

                raw_items = await page.eval_on_selector_all(
                    'a[href*="/catalog/"][href*="detail.aspx"]',
                    """
                    (anchors) => anchors.map((anchor) => {
                      const card = anchor.closest("article, .product-card, .product-card__wrapper, .swiper-slide") || anchor.parentElement;
                      const titleNode = card?.querySelector(".product-card__name, .goods-name, .j-card-name") || null;
                      const brandNode = card?.querySelector(".product-card__brand, .brand-name") || null;
                      const priceNode = card?.querySelector(".price__lower-price, .lower-price, .price, .wallet-price") || null;
                      const titleParts = [
                        brandNode?.textContent || "",
                        titleNode?.textContent || "",
                        anchor.getAttribute("aria-label") || "",
                        anchor.textContent || "",
                      ].filter(Boolean);
                      return {
                        href: anchor.href || anchor.getAttribute("href") || "",
                        title: titleParts.join(" "),
                        priceText: priceNode?.textContent || "",
                      };
                    });
                    """,
                )
            finally:
                await context.close()
                await browser.close()
    except Exception:
        logger.warning(
            "WB_BROWSER_SIMILAR_ERROR item=%s",
            wb_item_id,
            exc_info=True,
        )
        return []

    if not isinstance(raw_items, list):
        logger.warning(
            "WB_BROWSER_SIMILAR_INVALID_PAYLOAD item=%s",
            wb_item_id,
        )
        return []

    seen: set[int] = set()
    out: list[WbSimilarProduct] = []
    for row in raw_items:
        if not isinstance(row, dict):
            continue
        href = str(row.get("href") or "").strip()
        if not href:
            continue
        if href.startswith("/"):
            href = f"https://www.wildberries.ru{href}"

        match = _WB_ITEM_IN_URL_RE.search(href)
        if not match:
            continue

        try:
            item_id = int(match.group(1))
        except ValueError:
            continue
        if item_id in seen:
            continue

        price = _parse_price(str(row.get("priceText") or ""))
        if price is None:
            continue

        title = _normalize_title(str(row.get("title") or ""), f"WB #{item_id}")
        out.append(
            WbSimilarProduct(
                wb_item_id=item_id,
                title=title,
                price=price,
                url=f"https://www.wildberries.ru/catalog/{item_id}/detail.aspx",
            )
        )
        seen.add(item_id)

        if len(out) >= safe_limit:
            break

    if not out:
        logger.warning("WB_BROWSER_SIMILAR_EMPTY item=%s", wb_item_id)
        return []

    out.sort(key=lambda p: p.price)
    logger.info("WB_BROWSER_SIMILAR_OK item=%s count=%s", wb_item_id, len(out))
    return out[:safe_limit]
