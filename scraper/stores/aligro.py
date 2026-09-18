"""Aligro (cash & carry, open to private customers).

Regular prices are only shown to registered customers; the weekly
"actions" are public and include the normal price, so Aligro data is
limited to items on promotion.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from ..models import Offer
from ..units import Size, parse_size
from .base import Store, ddmm_to_date, pct_label

BASE = "https://www.aligro.ch"
MARKET = 1  # Aligro Chavannes-près-Renens
FOOD_CATEGORIES = [
    "10-fruits-legumes",
    "11-viande-charcuterie",
    "12-poissons-fruits-de-mer",
    "13-fromages-produits-laitiers-traiteur",
    "14-boulangerie-patisserie-petit-dejeuner",
    "15-glaces-glacons-desserts-glaces",
    "16-bieres-boissons-cafe-the",
    "19-produits-de-base-condiments-conserves",
    "20-snacks-confiseries",
]
PAGE = 192
MAX_PAGES = 10
_DATES = re.compile(r"du\s+(\d{1,2})\.(\d{1,2})\.?\s+au\s+(\d{1,2})\.(\d{1,2})")
_PIECES = re.compile(r"(\d+)\s*pi[èe]ces?")


class Aligro(Store):
    key = "aligro"
    name = "Aligro"
    mode = "pool"
    headers = {"Accept": "application/json, text/html;q=0.9", "Accept-Language": "fr-CH,fr;q=0.9"}
    delay = 1.0
    note = "Aligro javno prikazuje samo cene artikala na akciji (tržnica Chavannes)."

    def promotions(self) -> list[Offer]:
        try:
            self.http.get(f"{BASE}/cart/change-market/{MARKET}")
        except Exception as exc:
            self.warn(f"izbor tržnice: {exc}")
        valid_from = valid_to = None
        try:
            page = self.http.get(f"{BASE}/actions").text
            m = _DATES.search(page)
            if m:
                valid_from = ddmm_to_date(int(m.group(1)), int(m.group(2)), self.today)
                valid_to = ddmm_to_date(int(m.group(3)), int(m.group(4)), valid_from)
        except Exception as exc:
            self.warn(f"datumi akcija: {exc}")
        offers: dict[str, Offer] = {}
        failed = 0
        for cat in FOOD_CATEGORIES:
            for page_no in range(1, MAX_PAGES + 1):
                try:
                    data = self.http.get(f"{BASE}/actions/{cat}.json", params={"limit": PAGE, "offset": page_no}).json()
                except Exception as exc:  # one broken category must not sink the whole shop
                    failed += 1
                    self.warn(f"kategorija {cat}: {exc}")
                    break
                block = data.get("articles") or {}
                items = block.get("items") or []
                for item in items:
                    o = parse_article(item)
                    if not o:
                        continue
                    o.promo_from = valid_from.isoformat() if valid_from else None
                    o.promo_to = valid_to.isoformat() if valid_to else None
                    offers.setdefault(o.pid, o)
                total = block.get("total_items") or 0
                if len(items) < PAGE or page_no * PAGE >= total:
                    break
        if failed:
            self.stats["categories_failed"] = failed
        if not offers:
            raise RuntimeError("nijedna kategorija akcija nije preuzeta")
        return list(offers.values())


def _tr(obj: dict[str, Any] | None) -> dict[str, Any]:
    tr = (obj or {}).get("translations") or {}
    return tr.get("fr") or next(iter(tr.values()), {}) if tr else {}


def parse_article(item: dict[str, Any]) -> Offer | None:
    price_obj = item.get("mainArticleDetailPrice") or {}
    if not price_obj or price_obj.get("public") is False or price_obj.get("visible") is False:
        return None
    regular = price_obj.get("salesPriceTTC")
    price = price_obj.get("discountPriceTTC") or regular
    if not price:
        return None
    regular = regular or price
    promo = regular > price + 0.001
    tr = _tr(item)
    parts = [tr.get("advertisingText") or tr.get("description") or ""]
    if tr.get("additionalDesignation"):
        parts.append(tr["additionalDesignation"])
    if tr.get("origin"):
        parts.append(tr["origin"])
    name = ", ".join(p.strip() for p in parts if p and p.strip())

    size_label = (tr.get("weightVolume") or item.get("packagingLabel") or "").strip() or None
    unit_code = ((item.get("quantityUnit") or {}).get("code") or "").upper()
    number = (item.get("quantityUnit") or {}).get("number") or 1
    full = (item.get("quantityLabelForFullPrice") or "").strip()
    if item.get("weightSellable") or unit_code.startswith("KG"):
        size = Size({"kg": 1.0}, approx=True, per_unit="kg")
    else:
        full_size = parse_size(full)
        if full_size.get("kg") or full_size.get("l"):
            # e.g. "5 x 120 g", "6 x 1 l": the price is for the whole pack
            size, size_label = full_size, full
        else:
            size = parse_size(size_label)
            m = _PIECES.search(full)
            count = number if number > 1 else (int(m.group(1)) if m else 1)
            if count > 1 and size and "x" not in (size_label or "").lower():
                size = Size({k: v * count for k, v in size.dims.items() if k != "pc"} | {"pc": count}, approx=size.approx)
                size_label = f"{count} x {size_label}"
    rate = price_obj.get("discountRatePrivate")
    group = (item.get("article") or {}).get("articleGroup") or {}
    images = item.get("images") or {}
    main = images.get("main") or {}
    return Offer(
        store="aligro",
        pid=str(item.get("sKU") or item.get("id")),
        name=name,
        brand=(tr.get("brand") or "").strip() or None,
        price=price,
        regular_price=regular,
        size_label=size_label,
        size=size,
        promo=promo,
        promo_label=(f"-{int(rate * 100 + 1e-6)}%" if rate else pct_label((1 - price / regular) * 100)) if promo else None,
        category=_tr(group).get("wording"),
        food=True,
        url=(item.get("href") or {}).get("self"),
        image=main.get("image/jpeg") or main.get("image/webp"),
    )
