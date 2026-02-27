from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from decimal import Decimal
from typing import Iterable


def _parse_args(argv: Iterable[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch similar Wildberries products by nmId via Selenium.",
    )
    parser.add_argument(
        "nm_id_positional",
        nargs="?",
        help="Wildberries nmId or product URL (positional)",
    )
    parser.add_argument(
        "--nm-id",
        dest="nm_id",
        help="Wildberries nmId or product URL",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of products to return (default: 20)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="Page load timeout in seconds (default: 20)",
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Run Chrome with visible window",
    )
    return parser.parse_args(argv)


def _serialize(item: object) -> object:
    if isinstance(item, Decimal):
        return str(item)
    return item


def _serialize_items(items: list[object]) -> str:
    data = []
    for item in items:
        if hasattr(item, "__dataclass_fields__"):
            payload = asdict(item)
        else:
            payload = item
        data.append(payload)
    return json.dumps(data, ensure_ascii=False, indent=2, default=_serialize)


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(argv)

    from bot.services.wb_client import extract_wb_item_id
    from bot.services.wb_similar_selenium import fetch_similar_products

    raw_nm_id = args.nm_id if args.nm_id is not None else args.nm_id_positional
    if raw_nm_id is None:
        print("Provide nmId via --nm-id or positional argument", file=sys.stderr)
        return 2

    nm_id = extract_wb_item_id(str(raw_nm_id))
    if nm_id is None:
        print("Invalid nmId or URL", file=sys.stderr)
        return 2

    try:
        items = fetch_similar_products(
            nm_id,
            limit=args.limit,
            timeout_sec=args.timeout,
            headless=not args.no_headless,
        )
    except Exception as exc:  # pragma: no cover - CLI fallback
        print(f"Failed to fetch similar products: {exc}", file=sys.stderr)
        return 1

    print(_serialize_items(items))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
