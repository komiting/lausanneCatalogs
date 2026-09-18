"""Normalised product offer shared by all store adapters."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .units import Size, fold, round_price

# Pet food and litter sometimes sit in "Actions" or food campaigns without a usable category.
PET_WORDS = re.compile(
    r"\bpour (?:chats?|chatons?|chiens?|chiots?|animaux|oiseaux|rongeurs|lapins)\b|\blitiere\b|"
    r"\b(?:whiskas|felix|sheba|pedigree|catsan|dreamies|friskies|kitekat|purina|perfect fit|gourmet gold)\b"
)


def looks_like_pet_food(*texts: str | None) -> bool:
    return bool(PET_WORDS.search(fold(" ".join(t for t in texts if t))))


@dataclass
class Offer:
    store: str
    pid: str
    name: str
    price: float | None                 # shelf price of the pack right now (promo included)
    regular_price: float | None = None  # price without promotion
    brand: str | None = None
    size_label: str | None = None
    size: Size = field(default_factory=Size)
    unit_prices: dict[str, float] = field(default_factory=dict)  # store-provided CHF per kg/l/pc (current price)
    promo: bool = False
    promo_label: str | None = None
    promo_from: str | None = None
    promo_to: str | None = None
    conditional: bool = False           # promo needs a condition (buy 2, app, online only)
    upcoming: bool = False              # promo not valid yet
    category: str | None = None
    food: bool | None = None            # False = clearly not food (bags, pet food, ...)
    url: str | None = None
    image: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.name = " ".join((self.name or "").split())
        if self.regular_price is None:
            self.regular_price = self.price
        if self.food is not False and looks_like_pet_food(self.brand, self.name):
            self.food = False

    # ── prices ──────────────────────────────────────────────────────────
    @property
    def effective_price(self) -> float | None:
        """Price a shopper pays without meeting special conditions."""
        if self.conditional and self.regular_price:
            return self.regular_price
        return self.price

    @property
    def discount_pct(self) -> float | None:
        if not self.promo or not self.price or not self.regular_price:
            return None
        if self.regular_price <= self.price:
            return None
        return round((1 - self.price / self.regular_price) * 100, 1)

    def qty_in(self, unit: str, density: float | None = None) -> float | None:
        qty = self.size.get(unit)
        if qty is None and density:
            if unit == "kg" and self.size.get("l"):
                qty = self.size.get("l") * density
            elif unit == "l" and self.size.get("kg"):
                qty = self.size.get("kg") / density
        return qty

    def unit_price(self, unit: str, density: float | None = None, regular: bool = False) -> float | None:
        price = self.regular_price if regular else self.effective_price
        if not price or price <= 0:
            return None
        if unit in self.unit_prices and self.price:
            value = self.unit_prices[unit]
            if price != self.price:
                value = value * price / self.price
            return value
        qty = self.qty_in(unit, density)
        if not qty or qty <= 0:
            return None
        return price / qty

    # ── serialisation ───────────────────────────────────────────────────
    def dim(self) -> str | None:
        for d in ("kg", "l", "pc"):
            if d in self.unit_prices or self.size.get(d):
                return d
        return None

    def to_dict(self) -> dict[str, Any]:
        """Compact representation used in data files and the website."""
        d = self.dim()
        out: dict[str, Any] = {
            "id": self.pid,
            "n": self.name,
            "p": round_price(self.price),
        }
        if self.brand:
            out["b"] = self.brand
        if self.regular_price is not None and self.regular_price != self.price:
            out["r"] = round_price(self.regular_price)
        if self.size_label:
            out["s"] = self.size_label
        if d:
            up = self.unit_price(d)
            if up is not None:
                out["up"] = round_price(up, 3)
                out["u"] = d
        if self.size.approx:
            out["ax"] = 1
        if self.promo:
            out["pr"] = 1
            if self.promo_label:
                out["pl"] = self.promo_label
            if self.discount_pct is not None:
                out["pct"] = self.discount_pct
            if self.promo_from:
                out["from"] = self.promo_from
            if self.promo_to:
                out["to"] = self.promo_to
        if self.conditional:
            out["cd"] = 1
        if self.upcoming:
            out["soon"] = 1
        if self.category:
            out["c"] = self.category
        if self.url:
            out["url"] = self.url
        if self.image:
            out["img"] = self.image
        return out
