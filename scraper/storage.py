"""File-based storage kept in the git repository (data/)."""
from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path
from typing import Any, Iterable

from .basket import Candidate
from .models import Offer
from .units import round_price

PRICE_FIELDS = ["date", "pid", "price", "regular", "promo", "label", "until"]
BASKET_FIELDS = ["date", "item", "store", "pid", "unit_price", "price", "regular_unit_price", "promo"]
MAX_RUNS = 120


def _read_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def _compact(data) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _lines(data, depth: int) -> str:
    """JSON with the first `depth` levels spread one entry per line, the rest compact.

    Keeps the files small while git diffs stay readable (one changed product = one changed line).
    """
    if depth <= 0 or not isinstance(data, (dict, list)) or not data:
        return _compact(data)
    if isinstance(data, dict):
        body = ",\n".join(f"{json.dumps(k, ensure_ascii=False)}:{_lines(v, depth - 1)}" for k, v in data.items())
        return "{\n" + body + "\n}"
    return "[\n" + ",\n".join(_lines(v, depth - 1) for v in data) + "\n]"


def _write_json(path: Path, data, depth: int | None = None) -> None:
    """depth=None: indented (small files); otherwise see _lines()."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if depth is None:
        text = json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True)
    else:
        text = _lines(data, depth)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text + "\n", encoding="utf-8")
    tmp.replace(path)


def last_seen(product: dict[str, Any], store_last_ok: str | None) -> str | None:
    """Last day a product was observed. An open span (end = null) lasts until the store's last good run."""
    spans = product.get("sp") or []
    if not spans:
        return product.get("l") or product.get("f")
    end = spans[-1][1]
    return (store_last_ok or spans[-1][0]) if end is None else end


def resolved_spans(product: dict[str, Any], store_last_ok: str | None) -> list[list[str]]:
    out = []
    for start, end in product.get("sp") or []:
        out.append([start, end if end is not None else max(start, store_last_ok or start)])
    return out


def _fmt(v: float | None) -> str:
    return "" if v is None else f"{round_price(v):.2f}"


class DataStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    # ── state ───────────────────────────────────────────────────────────
    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    def load_state(self) -> dict[str, Any]:
        return _read_json(self.state_path, {"stores": {}, "runs": []})

    def save_state(self, state: dict[str, Any]) -> None:
        state["runs"] = state.get("runs", [])[-MAX_RUNS:]
        _write_json(self.state_path, state)

    # ── products + price change-log ─────────────────────────────────────
    def products_path(self, store: str) -> Path:
        return self.root / "products" / f"{store}.json"

    def prices_path(self, store: str) -> Path:
        return self.root / "prices" / f"{store}.csv"

    def load_products(self, store: str) -> dict[str, Any]:
        return _read_json(self.products_path(store), {})

    def record_offers(self, store: str, offers: Iterable[Offer], today: dt.date, prev_ok: str | None,
                      close_missing: bool = True, max_gap_days: int = 7) -> int:
        """Update product metadata and append changed prices. Returns rows written.

        Each product keeps "sp", the spans of days it was seen: [start, end]. The current span stays
        open (end = null) while the product keeps showing up, so the file only changes when something
        actually changes. `prev_ok` is the store's previous successful run (before today); products
        missing today get their open span closed at that date, unless `close_missing` is False
        (a second run on the same day, or a run that returned suspiciously few products): then the
        run only adds. After a long pause (more than `max_gap_days` without a good run) every span
        is closed, so charts show a gap instead of guessing.
        """
        products = self.load_products(store)
        day = today.isoformat()
        if prev_ok and (today - dt.date.fromisoformat(prev_ok)).days > max_gap_days:
            for p in products.values():
                spans = p.get("sp") or []
                if spans and spans[-1][1] is None:
                    spans[-1][1] = max(spans[-1][0], prev_ok)
            prev_ok = None
        rows = []
        seen: set[str] = set()
        for o in offers:
            if o.price is None or o.upcoming or o.pid in seen:
                continue
            seen.add(o.pid)
            p = products.get(o.pid) or {"f": day, "sp": []}
            p.pop("l", None)  # older format
            p["n"] = o.name
            for key, value in (("b", o.brand), ("s", o.size_label), ("u", o.dim()), ("c", o.category),
                               ("url", o.url), ("img", o.image)):
                if value:
                    p[key] = value
                else:
                    p.pop(key, None)
            spans = p.setdefault("sp", [])
            if spans and (spans[-1][1] is None or spans[-1][1] == day or (prev_ok and spans[-1][1] == prev_ok)):
                spans[-1][1] = None
                new_span = False
            else:
                spans.append([day, None])
                new_span = True
            snapshot = [round_price(o.price), round_price(o.regular_price), int(o.promo),
                        o.promo_label or "", o.promo_to or ""]
            if new_span or p.get("lp") != snapshot:
                rows.append({
                    "date": day, "pid": o.pid, "price": _fmt(o.price), "regular": _fmt(o.regular_price),
                    "promo": int(o.promo), "label": o.promo_label or "", "until": o.promo_to or "",
                })
            p["lp"] = snapshot
            products[o.pid] = p
        if close_missing:
            for pid, p in products.items():
                spans = p.get("sp") or []
                if pid not in seen and spans and spans[-1][1] is None:
                    spans[-1][1] = max(spans[-1][0], prev_ok or spans[-1][0])
        _write_json(self.products_path(store), dict(sorted(products.items())), depth=1)
        if rows:
            path = self.prices_path(store)
            path.parent.mkdir(parents=True, exist_ok=True)
            new = not path.exists()
            with path.open("a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=PRICE_FIELDS)
                if new:
                    w.writeheader()
                w.writerows(rows)
        return len(rows)

    def read_prices(self, store: str) -> list[dict[str, str]]:
        path = self.prices_path(store)
        if not path.exists():
            return []
        with path.open(newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    # ── basket history ──────────────────────────────────────────────────
    @property
    def basket_path(self) -> Path:
        return self.root / "basket.csv"

    def record_basket(self, today: dt.date, store: str, results: dict[str, list[Candidate]]) -> None:
        day = today.isoformat()
        existing = [r for r in self.read_basket() if not (r["date"] == day and r["store"] == store)]
        for item_id, cands in results.items():
            if not cands:
                continue
            best = cands[0]
            existing.append({
                "date": day, "item": item_id, "store": store, "pid": best.offer.pid,
                "unit_price": f"{best.unit_price:.3f}", "price": _fmt(best.offer.effective_price),
                "regular_unit_price": "" if best.regular_unit_price is None else f"{best.regular_unit_price:.3f}",
                "promo": int(best.offer.promo and not best.offer.conditional),
            })
        existing.sort(key=lambda r: (r["date"], r["item"], r["store"]))
        self.basket_path.parent.mkdir(parents=True, exist_ok=True)
        with self.basket_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=BASKET_FIELDS)
            w.writeheader()
            w.writerows(existing)

    def read_basket(self) -> list[dict[str, str]]:
        if not self.basket_path.exists():
            return []
        with self.basket_path.open(newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    # ── per-store cache (e.g. Lidl product sizes) ───────────────────────
    def cache_path(self, store: str) -> Path:
        return self.root / "cache" / f"{store}.json"

    def load_cache(self, store: str) -> dict[str, Any]:
        return _read_json(self.cache_path(store), {})

    def save_cache(self, store: str, cache: dict[str, Any], today: dt.date, keep_days: int = 60) -> None:
        if not cache:
            return
        cutoff = (today - dt.timedelta(days=keep_days)).isoformat()
        cache = {k: v for k, v in cache.items() if not isinstance(v, dict) or v.get("seen", "9999") >= cutoff}
        _write_json(self.cache_path(store), dict(sorted(cache.items())), depth=1)

    # ── latest snapshot per store ───────────────────────────────────────
    def latest_path(self, store: str) -> Path:
        return self.root / "latest" / f"{store}.json"

    def write_latest(self, store: str, today: dt.date, promos: list[Offer],
                     basket: dict[str, list[Candidate]], extra: dict[str, Any] | None = None) -> None:
        promos = sorted(promos, key=lambda o: (-(o.discount_pct or 0), o.name))
        _write_json(self.latest_path(store), {
            "date": today.isoformat(),
            **(extra or {}),
            "promos": [o.to_dict() for o in promos],
            "basket": {k: [c.to_dict() for c in v] for k, v in basket.items()},
        }, depth=2)

    def read_latest(self, store: str) -> dict[str, Any] | None:
        return _read_json(self.latest_path(store), None)
