"""Basket definition (basket.toml) and product matching."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11 (e.g. the python3 that comes with macOS)
    import tomli as tomllib

from .models import Offer
from .units import fold

BASKET_FILE = Path(__file__).resolve().parent.parent / "basket.toml"
TOP_N = 5


@dataclass
class Candidate:
    offer: Offer
    unit_price: float
    regular_unit_price: float | None

    def to_dict(self) -> dict:
        d = self.offer.to_dict()
        d["iu"] = round(self.unit_price, 3)
        if self.regular_unit_price and abs(self.regular_unit_price - self.unit_price) > 1e-6:
            d["iru"] = round(self.regular_unit_price, 3)
        return d


@dataclass
class BasketItem:
    id: str
    name: str
    group: str
    unit: str
    query: str
    queries: dict[str, list[str]] = field(default_factory=dict)
    include: list[re.Pattern] = field(default_factory=list)
    exclude: re.Pattern | None = None
    min_qty: float | None = None
    max_qty: float | None = None
    density: float | None = None
    note: str | None = None

    def queries_for(self, store: str) -> list[str]:
        q = self.queries.get(store)
        if isinstance(q, str):
            return [q]
        return list(q) if q else [self.query]

    def matches(self, offer: Offer) -> bool:
        if offer.food is False:
            return False
        text = fold(f"{offer.brand or ''} {offer.name}")
        if not all(rx.search(text) for rx in self.include):
            return False
        return not (self.exclude and self.exclude.search(text))

    def evaluate(self, offer: Offer) -> Candidate | None:
        if offer.upcoming or not self.matches(offer):
            return None
        up = offer.unit_price(self.unit, self.density)
        if up is None or up <= 0:
            return None
        qty = offer.qty_in(self.unit, self.density)
        sold_by_weight = offer.size.per_unit is not None or (offer.size.approx and qty is not None and qty <= 0.1001 and self.unit != "pc")
        if qty is not None and not sold_by_weight:
            if self.min_qty is not None and qty + 1e-9 < self.min_qty:
                return None
            if self.max_qty is not None and qty - 1e-9 > self.max_qty:
                return None
        return Candidate(offer, up, offer.unit_price(self.unit, self.density, regular=True))

    def rank(self, offers: list[Offer], limit: int = TOP_N) -> list[Candidate]:
        seen: set[str] = set()
        out: list[Candidate] = []
        for o in offers:
            if o.pid in seen:
                continue
            c = self.evaluate(o)
            if c:
                seen.add(o.pid)
                out.append(c)
        out.sort(key=lambda c: (c.unit_price, c.offer.name))
        return out[:limit]


def _compile(pattern: str) -> re.Pattern:
    return re.compile(fold(pattern))


def load_basket(path: Path | None = None) -> list[BasketItem]:
    data = tomllib.loads(Path(path or BASKET_FILE).read_text(encoding="utf-8"))
    items: list[BasketItem] = []
    seen: set[str] = set()
    for raw in data.get("item", []):
        if raw["id"] in seen:
            raise ValueError(f"duplicate basket id {raw['id']}")
        seen.add(raw["id"])
        if raw.get("unit") not in ("kg", "l", "pc"):
            raise ValueError(f"{raw['id']}: unit must be kg, l or pc")
        items.append(BasketItem(
            id=raw["id"],
            name=raw["name"],
            group=raw.get("group", "Ostalo"),
            unit=raw["unit"],
            query=raw["query"],
            queries=raw.get("queries", {}),
            include=[_compile(p) for p in raw.get("include", [])] or [_compile(re.escape(raw["query"]))],
            exclude=_compile(raw["exclude"]) if raw.get("exclude") else None,
            min_qty=raw.get("min_qty"),
            max_qty=raw.get("max_qty"),
            density=raw.get("density"),
            note=raw.get("note"),
        ))
    return items
