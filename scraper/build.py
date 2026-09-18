"""Turn data/ into the JSON files the static website reads (site/data/)."""
from __future__ import annotations

import datetime as dt
import json
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

from .basket import BasketItem, load_basket
from .storage import DataStore, last_seen, resolved_spans
from .stores import STORES

HISTORY_DAYS = 400
ORDER = list(STORES)


def _dump(path: Path, data: Any) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    path.write_text(text, encoding="utf-8")
    return len(text)


def _float(s: str | None) -> float | None:
    return float(s) if s not in (None, "") else None


def build(data_dir: Path = Path("data"), site_dir: Path = Path("site"), basket: list[BasketItem] | None = None,
          today: dt.date | None = None) -> dict[str, int]:
    ds = DataStore(data_dir)
    basket = basket or load_basket()
    state = ds.load_state()
    out_dir = site_dir / "data"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    sizes: dict[str, int] = {}
    latest = {k: ds.read_latest(k) for k in ORDER}
    last_date = max((v["date"] for v in latest.values() if v), default=None)
    today = today or (dt.date.fromisoformat(last_date) if last_date else dt.date.today())
    since = (today - dt.timedelta(days=HISTORY_DAYS)).isoformat()

    # ── meta ────────────────────────────────────────────────────────────
    stores_meta = {}
    for key in ORDER:
        cls = STORES[key]
        info = state.get("stores", {}).get(key, {})
        snap = latest.get(key) or {}
        stores_meta[key] = {
            "name": cls.name, "note": cls.note, "mode": cls.mode,
            "last_ok": info.get("last_ok"), "last_run": info.get("last_run"),
            "status": info.get("status"), "error": info.get("error"),
            "warnings": info.get("warnings") or [], "counts": info.get("counts") or {},
            "snapshot": snap.get("date"),
        }
    runs = state.get("runs", [])[-30:]
    sizes["meta.json"] = _dump(out_dir / "meta.json", {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "today": today.isoformat(), "stores": stores_meta, "order": ORDER, "runs": runs,
    })

    # ── basket ──────────────────────────────────────────────────────────
    rows = [r for r in ds.read_basket() if r["date"] >= since]
    dates = sorted({r["date"] for r in rows})
    didx = {d: i for i, d in enumerate(dates)}
    series: dict[str, dict[str, list]] = defaultdict(lambda: {k: [None] * len(dates) for k in ORDER})
    promo_flags: dict[str, dict[str, list]] = defaultdict(lambda: {k: [0] * len(dates) for k in ORDER})
    for r in rows:
        if r["store"] not in ORDER:
            continue
        i = didx[r["date"]]
        series[r["item"]][r["store"]][i] = round(float(r["unit_price"]), 2)
        promo_flags[r["item"]][r["store"]][i] = int(r.get("promo") or 0)
    items = []
    for it in basket:
        now = {}
        for key in ORDER:
            snap = latest.get(key)
            if not snap:
                continue
            cands = (snap.get("basket") or {}).get(it.id) or []
            now[key] = {"date": snap["date"], "cands": cands}
        items.append({
            "id": it.id, "name": it.name, "group": it.group, "unit": it.unit, "note": it.note,
            "now": now,
            "hist": {k: v for k, v in series[it.id].items() if any(x is not None for x in v)},
            "promo": {k: v for k, v in promo_flags[it.id].items() if any(v)},
        })
    sizes["basket.json"] = _dump(out_dir / "basket.json", {"dates": dates, "items": items})

    # ── current promotions ─────────────────────────────────────────────
    promos = []
    for key in ORDER:
        snap = latest.get(key)
        if not snap:
            continue
        for p in snap.get("promos") or []:
            p = dict(p)
            p["st"] = key
            p["d"] = snap["date"]
            promos.append(p)
    sizes["promos.json"] = _dump(out_dir / "promos.json", promos)

    # ── per-product history + search index ─────────────────────────────
    index = []
    for key in ORDER:
        products = ds.load_products(key)
        hist: dict[str, list] = defaultdict(list)
        for r in ds.read_prices(key):
            row = [r["date"], _float(r["price"]), _float(r["regular"]), int(r["promo"] or 0), r["label"] or ""]
            rows_for = hist[r["pid"]]
            if rows_for and rows_for[-1][0] == row[0]:
                rows_for[-1] = row  # same-day rerun: the later value wins
            elif not rows_for or rows_for[-1][1:] != row[1:]:
                rows_for.append(row)  # a product seen again at the same price is not a change
        store_ok = (state.get("stores", {}).get(key) or {}).get("last_ok")
        out = {}
        for pid, p in products.items():
            seen = last_seen(p, store_ok) or ""
            if seen < since:
                continue
            h = hist.get(pid, [])
            out[pid] = {k: p[k] for k in ("n", "b", "s", "u", "c", "url", "img", "f") if k in p}
            out[pid]["l"] = seen
            out[pid]["h"] = h
            out[pid]["sp"] = resolved_spans(p, store_ok)
            promo_days = sum(1 for x in h if x[3])
            on_promo_now = int(bool(p.get("lp") and p["lp"][2] and seen == store_ok))
            index.append([key, pid, p["n"], p.get("b") or "", p.get("s") or "", (p.get("lp") or [None])[0],
                          on_promo_now, seen, len(h), promo_days])
        sizes[f"history/{key}.json"] = _dump(out_dir / "history" / f"{key}.json", out)
    sizes["index.json"] = _dump(out_dir / "index.json", index)
    return sizes
