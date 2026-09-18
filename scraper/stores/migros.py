"""Migros (Genossenschaft Migros Waadt / Vaud) via the public website API."""
from __future__ import annotations

import re
from typing import Any

from ..models import Offer
from ..units import Size, parse_size
from .base import Store, first, pct_label

BASE = "https://www.migros.ch"
REGION = "gmvd"  # Migros Vaud – prices for stores around Lausanne
IMG_STACK = "mo-custom/v-w-200-h-200"
NONFOOD = re.compile(r"animaux|b[ée]b[ée]|maison|mode|beaut|jardin|sant[ée]|m[ée]nage|sport|loisir|[ée]lectro|bureau|jouet|voyage|bricol", re.I)
SEARCH_LIMIT = 30


class Migros(Store):
    key = "migros"
    name = "Migros"
    mode = "search"
    delay = 0.6
    note = "Cene za region Migros Vaud (prodavnice u Lozani)."

    def __init__(self, http, today=None):
        super().__init__(http, today)
        self._token: str | None = None
        self._cards: dict[int, dict[str, Any]] = {}

    # ── API helpers ─────────────────────────────────────────────────────
    def _headers(self) -> dict[str, str]:
        if self._token is None:
            r = self.http.get(f"{BASE}/authentication/public/v1/api/guest?authorizationNotRequired=true",
                              headers={"Accept": "application/json, text/plain, */*"})
            self._token = r.header("leshopch") or ""
            if not self._token:
                raise RuntimeError("Migros guest token missing")
        return {
            "leshopch": self._token,
            "migros-language": "fr",
            "accept-language": "fr",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Peer-Id": "website-js-1256.0.0",
        }

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        return self.http.post(f"{BASE}{path}", json_body=body, headers=self._headers()).json()

    def search_ids(self, query: str) -> list[int]:
        data = self._post("/product-display/public/v2/products/search", {
            "query": query, "language": "fr", "storeType": "OFFLINE", "region": REGION,
            "sortFields": [], "sortOrder": "asc", "from": 0, "limit": 100, "filters": {},
            "searchAlgorithm": "DEFAULT", "enabledSponsoredProducts": False,
        })
        return [int(i["id"]) for i in data.get("items", []) if i.get("type") == "PRODUCT"]

    def cards(self, ids: list[int]) -> list[dict[str, Any]]:
        wanted = [i for i in dict.fromkeys(ids) if i not in self._cards]
        for start in range(0, len(wanted), 50):
            chunk = wanted[start:start + 50]
            data = self._post("/product-display/public/v4/product-cards", {
                "offerFilter": {"storeType": "OFFLINE", "region": REGION,
                                "ongoingOfferDate": f"{self.today.isoformat()}T00:00:00"},
                "productFilter": {"uids": chunk},
            })
            for card in data or []:
                if card.get("uid") is not None:
                    self._cards[int(card["uid"])] = card
        return [self._cards[i] for i in ids if i in self._cards]

    # ── public interface ────────────────────────────────────────────────
    def search(self, query: str) -> list[Offer]:
        ids = self.search_ids(query)[:SEARCH_LIMIT]
        return [o for o in (parse_card(c, self) for c in self.cards(ids)) if o]

    def promotions(self) -> list[Offer]:
        data = self._post("/product-display/public/web/v2/products/promotion/search", {
            "storeType": "OFFLINE", "period": "CURRENT", "language": "fr", "filters": {},
            "sortFields": ["categoryLevel"], "sortOrder": "asc", "from": 0, "until": 1000,
            "region": REGION, "warehouse": "1",
        })
        items = data.get("items", [])
        ids = [int(i["id"]) for i in items if i.get("type") == "PRODUCT"]
        self.stats["group_promotions"] = sum(1 for i in items if i.get("type") != "PRODUCT")
        start, end = data.get("startDate"), data.get("endDate")
        out = []
        for card in self.cards(ids):
            o = parse_card(card, self)
            if not o or not o.promo:
                continue
            o.promo_from = o.promo_from or start
            o.promo_to = o.promo_to or end
            out.append(o)
        return out


def _url(card: dict[str, Any]) -> str | None:
    urls = card.get("productUrls")
    if isinstance(urls, str):
        return urls
    if isinstance(urls, dict):
        return urls.get("fr") or first(urls.values())
    if card.get("migrosId"):
        return f"{BASE}/fr/product/{card['migrosId']}"
    return None


def parse_card(card: dict[str, Any], store: Store | None = None) -> Offer | None:
    offer = card.get("offer") or {}
    price_block = offer.get("price") or {}
    regular = price_block.get("effectiveValue")
    if not regular:
        return None
    promo_block = offer.get("promotionPrice") or {}
    promo_value = promo_block.get("effectiveValue")
    badges = offer.get("badges") or []
    types = {b.get("type") for b in badges}
    is_promo = bool(promo_value) and promo_value < regular - 0.001
    price = promo_value if is_promo else regular

    label = None
    if is_promo:
        pct = first(b for b in badges if b.get("type") == "PERCENTAGE_PROMOTION")
        if pct and pct.get("description"):
            d = pct["description"].strip()
            label = d if d.startswith("-") else f"-{d}"
        else:
            label = pct_label((1 - price / regular) * 100)
        mins = first(b for b in badges if b.get("type") == "MINIMUM_PIECES")
        if mins:
            extra = (mins.get("description") or mins.get("rawDescription") or "").strip()
            label = f"{label} {extra}".strip() if extra else label

    qty_label = (offer.get("quantity") or "").strip()
    unit_price = price_block.get("unitPrice") or {}
    size = Size()
    if offer.get("isVariableWeight") and unit_price.get("value"):
        ref = parse_size(unit_price.get("unit"))
        dim = next((d for d in ("kg", "l") if ref.get(d)), None)
        if dim:
            amount = regular / unit_price["value"] * ref.get(dim)
            size = Size({dim: amount}, approx=True, per_unit=dim if abs(amount - 1) < 1e-6 else None)
    if not size:
        size = parse_size(qty_label)
    if not size and unit_price.get("value") and price_block.get("displayUnitPrice"):
        ref = parse_size(unit_price.get("unit"))
        dim = next((d for d in ("kg", "l", "pc") if ref.get(d)), None)
        if dim:
            size = Size({dim: regular / unit_price["value"] * ref.get(dim)}, approx=True)

    breadcrumb = card.get("breadcrumb") or []
    category = breadcrumb[0]["name"] if breadcrumb else None
    image = first(card.get("images") or [], {}).get("url")
    date_range = offer.get("promotionDateRange") or {}
    return Offer(
        store="migros",
        pid=str(card.get("uid")),
        name=card.get("title") or card.get("name") or "",
        brand=card.get("brand"),
        price=price,
        regular_price=regular,
        size_label=qty_label or None,
        size=size,
        promo=is_promo,
        promo_label=label,
        promo_from=date_range.get("startDate") if is_promo else None,
        promo_to=date_range.get("endDate") if is_promo else None,
        conditional=is_promo and "MINIMUM_PIECES" in types,
        category=category,
        food=None if not category else not NONFOOD.search(category),
        url=_url(card),
        image=image.replace("{stack}", IMG_STACK) if image else None,
    )
