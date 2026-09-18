"""Aldi Suisse via the public product API (whole catalogue, ~2300 items)."""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from ..models import Offer
from ..units import Size, parse_size, per_unit_from_display
from .base import Store, first, parse_chf, pct_label

BASE = "https://api.aldi-suisse.ch"
SERVICE_POINT = "E468"  # Aldi Chavannes-près-Renens, Avenue de la Concorde 18
PAGE = 60
MAX_PAGES = 80
SPECIAL = {"actions", "la promesse du prix le plus bas", "saveurs suisses", "retour aux sources (bio)",
           "gourmet", "barbecue"}
NONFOOD = re.compile(r"sant[ée], soin|m[ée]nage|v[êe]tement|[ée]clairage|livre|fourniture|d[ée]coration|"
                     r"appareil|jardin|linge|valise|cuisine$|outdoor|plantes|entretien|jouet|animaux|lessive|"
                     r"hygi[èe]ne|cosm[ée]tique|papier", re.I)
_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
PROMO_BADGE = re.compile(r"prix|action|promo|rabais|%|super|offre|baisse", re.I)


def _nice_brand(brand: str | None) -> str | None:
    brand = (brand or "").strip()
    if not brand:
        return None
    if brand.isupper():
        brand = " ".join(w[:1].upper() + w[1:].lower() for w in brand.split())
    return brand


class Aldi(Store):
    key = "aldi"
    name = "Aldi"
    mode = "catalog"
    headers = {"Accept": "application/json", "Accept-Language": "fr-CH"}
    delay = 0.5
    note = "Ceo asortiman sa aldi-suisse.ch (filijala Chavannes-près-Renens). Nema svežeg voća i povrća."

    def __init__(self, http, today=None):
        super().__init__(http, today)
        self._catalog: list[Offer] | None = None

    def catalog(self) -> list[Offer]:
        if self._catalog is not None:
            return self._catalog
        offers: dict[str, Offer] = {}
        offset, total, pages = 0, None, 0
        while (total is None or offset < total) and pages < MAX_PAGES:
            data = self.http.get(f"{BASE}/v3/product-search", params={
                "servicePoint": SERVICE_POINT, "serviceType": "walk-in",
                "offset": offset, "limit": PAGE,
            }).json()
            total = ((data.get("meta") or {}).get("pagination") or {}).get("totalCount") or 0
            items = data.get("data") or []
            for p in items:
                o = parse_product(p, self.today)
                if o:
                    offers.setdefault(o.pid, o)
            pages += 1
            offset += PAGE
            if not items:
                break
        self.stats["catalog"] = len(offers)
        self._catalog = list(offers.values())
        return self._catalog

    def promotions(self) -> list[Offer]:
        return [o for o in self.catalog() if o.promo]


def parse_product(p: dict[str, Any], today: dt.date | None = None) -> Offer | None:
    sku = str(p.get("sku") or "")
    price_obj = p.get("price") or {}
    amount = price_obj.get("amount")
    if not sku or amount is None:
        return None
    today = today or dt.date.today()
    price = amount / 100
    was = parse_chf(price_obj.get("wasPriceDisplay"))
    promo = was is not None and was > price + 0.001

    size_label = p.get("sellingSize")
    size = parse_size(size_label)
    if price_obj.get("perUnit") and price_obj.get("perUnitDisplay"):
        ref = parse_size(price_obj["perUnitDisplay"])
        dim = next((d for d in ("kg", "l", "pc") if ref.get(d)), None)
        if dim:
            size = Size({dim: ref.get(dim)}, approx=True, per_unit=dim)
    unit_prices: dict[str, float] = {}
    ref_price = per_unit_from_display(price_obj.get("comparisonDisplay"))
    if ref_price:
        unit_prices[ref_price[0]] = ref_price[1]

    badges = [i.get("displayText") for b in (p.get("badges") or []) for i in (b.get("items") or [])]
    badges = [b.strip() for b in badges if b and b.strip() and PROMO_BADGE.search(b)]
    label = None
    if promo:
        label = " · ".join(x for x in [pct_label((1 - price / was) * 100)] + badges[:1] if x)

    promo_from = upcoming = None
    on_sale = p.get("onSaleDateDisplay") or ""
    m = _DATE.search(on_sale)
    if m:
        d = dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if d > today:
            upcoming = True
        promo_from = d.isoformat()

    cats = [c.get("name") or "" for c in (p.get("categories") or [])]
    real = [c for c in cats if c and c.lower() not in SPECIAL]
    category = first(real) or first(c for c in cats if c)
    food: bool | None = None
    if cats and any(cats):
        food = not any(NONFOOD.search(c) for c in cats if c)
        if cats[0].lower() == "actions" and len(cats) == 1:
            food = None

    slug = p.get("urlSlugText") or ""
    image = first(p.get("assets") or [], {}).get("url")
    return Offer(
        store="aldi",
        pid=sku.lstrip("0") or sku,
        name=p.get("name") or "",
        brand=_nice_brand(p.get("brandName")),
        price=price,
        regular_price=was if promo else price,
        size_label=size_label,
        size=size,
        unit_prices=unit_prices,
        promo=promo,
        promo_label=label,
        promo_from=promo_from if promo else None,
        upcoming=bool(upcoming),
        category=category,
        food=food,
        url=f"https://www.aldi-suisse.ch/fr/p.{slug}.{sku}.html" if slug else None,
        image=image.replace("{width}", "300").replace("{slug}", slug or "produit") if image else None,
        extra={"available_from": promo_from} if promo_from else {},
    )
