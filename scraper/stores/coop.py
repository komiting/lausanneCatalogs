"""Coop via the coop.ch REST API (same API as the Coop app)."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

from ..models import Offer
from ..units import Size, parse_size
from .base import Store, first

BASE = "https://www.coop.ch/rest/v2/coopathome"
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Mobile/15E148")
# "Toutes les actions" sub-categories that contain food
PROMO_CATEGORIES = {
    "m_1434": "Fruits & légumes",
    "m_1377": "Produits laitiers & œufs",
    "m_1380": "Viandes & poissons",
    "m_1248": "Pains & viennoiseries",
    "m_9650": "Garde-manger",
    "m_9651": "Friandises & snacks",
    "m_2161": "Plats cuisinés & surgelés",
    "m_1807": "Boissons",
}
FOOD_PATHS = ("lebensmittel", "getraenke")
SEARCH_PAGE_SIZE = 40
MAX_PROMO_PAGES = 4


class Coop(Store):
    key = "coop"
    name = "Coop"
    mode = "search"
    impersonate = "safari_ios"
    headers = {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
               "Accept-Language": "fr-CH,fr;q=0.9"}
    delay = 1.5
    note = "Cene sa coop.ch (iste kao u većini prodavnica)."

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        return self.http.get(f"{BASE}{path}", params=params).json()

    def search(self, query: str) -> list[Offer]:
        data = self._get(f"/products/search/{quote(query, safe='')}", {
            "currentPage": 0, "pageSize": SEARCH_PAGE_SIZE,
            "query": "availableOnline:false", "language": "fr",
        })
        return [o for o in (parse_product(p) for p in data.get("products") or []) if o]

    def promotions(self) -> list[Offer]:
        out: dict[str, Offer] = {}
        for cat_id, cat_name in PROMO_CATEGORIES.items():
            for page in range(MAX_PROMO_PAGES):
                try:
                    data = self._get("/products/category/m_1111", {
                        "currentPage": page, "pageSize": 100, "language": "fr",
                        "sort": "specialOffers",
                        "query": f"::promotionSubcategoryFacet:{cat_id}",
                    })
                except Exception as exc:  # keep what we have
                    self.warn(f"akcije {cat_name}: {exc}")
                    break
                products = data.get("products") or []
                for p in products:
                    o = parse_product(p)
                    if o and o.promo and o.pid not in out:
                        o.extra["group"] = cat_name
                        out[o.pid] = o
                pages = (data.get("pagination") or {}).get("totalPages") or 0
                # results are sorted with real price cuts first; stop once a page has none
                if not any(p.get("originalPrice") for p in products) or page + 1 >= pages:
                    break
        return list(out.values())


def _label(p: dict[str, Any]) -> str | None:
    labels = []
    for raw in p.get("listPromotions") or []:
        raw = " ".join(str(raw).split())
        if raw.lower() == "action":
            continue
        if raw[:1].isdigit() and "%" in raw:
            raw = "-" + raw
        labels.append(raw)
    if not labels and p.get("discountPercentage"):
        labels.append(f"-{p['discountPercentage']}%")
    if not labels and p.get("listPromotions"):
        labels.append("Action")
    return " · ".join(labels) or None


def parse_product(p: dict[str, Any]) -> Offer | None:
    code = p.get("code")
    if not code:
        return None
    price = (p.get("price") or {}).get("value")
    original = (p.get("originalPrice") or {}).get("value")
    flat = original is not None and price is not None and original > price + 0.001
    label = _label(p)
    conditional_promo = (not flat) and bool(p.get("hasPromotion")) and bool(p.get("listPromotions"))
    promo = flat or conditional_promo
    online_only = bool(p.get("onlyOnlinePromotion")) or ("online" in (label or "").lower())

    content = p.get("content")
    unit = p.get("contentUnit") or ""
    size_label = f"{content} {unit}".strip() if content not in (None, "") else None
    size = parse_size(size_label)
    if not size and price and (p.get("basePrice") or {}).get("value"):
        ref = parse_size(f"{p.get('baseCapacity') or 1} {p.get('basePriceUnit') or ''}")
        dim = next((d for d in ("kg", "l", "pc") if ref.get(d)), None)
        if dim:
            size = Size({dim: price / p["basePrice"]["value"] * ref.get(dim)}, approx=True)
    path = p.get("categoryPathForTracking") or ""
    return Offer(
        store="coop",
        pid=str(code),
        name=p.get("title") or p.get("name") or "",
        brand=(p.get("brand") or {}).get("name") if isinstance(p.get("brand"), dict) else None,
        price=price,
        regular_price=original if flat else price,
        size_label=size_label,
        size=size,
        promo=promo,
        promo_label=label if promo else None,
        conditional=(conditional_promo or (promo and online_only)),
        category=(p.get("primaryCategory") or {}).get("name"),
        food=None if not path else path.startswith(FOOD_PATHS),
        url=f"https://www.coop.ch/fr/p/{code}",
        image=first(p.get("images") or [], {}).get("url"),
    )
