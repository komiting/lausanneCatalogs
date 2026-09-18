"""One scraping run: fetch every store, match the basket, store results."""
from __future__ import annotations

import datetime as dt
import logging
import time
from pathlib import Path
from typing import Callable

from .basket import BasketItem, Candidate, load_basket
from .http import Blocked, LiveHttp
from .models import Offer
from .storage import DataStore
from .stores import STORES
from .stores.base import Store

log = logging.getLogger(__name__)
MIN_SHARE_OF_USUAL = 0.25  # far fewer products than last time -> warn and keep history spans open


def live_http_factory(store_cls: type[Store]):
    return LiveHttp(impersonate=store_cls.impersonate, headers=store_cls.headers, delay=store_cls.delay)


def collect(store: Store, basket: list[BasketItem]) -> tuple[list[Offer], dict[str, list[Candidate]], list[Offer]]:
    """Return (promotions, basket candidates, all observed offers)."""
    observed: dict[str, Offer] = {}
    featured: list[Offer] = []
    try:
        featured = store.promotions()
    except Blocked:
        raise
    except Exception as exc:
        if store.mode == "pool":
            raise
        store.warn(f"akcije nisu preuzete: {exc}")
    for o in featured:
        observed[o.pid] = o
    # leaflets also list items at their normal price: usable for the basket, not "akcije"
    promos = [o for o in featured if (o.promo or o.upcoming) and o.food is not False]

    pool: list[Offer] | None = None
    if store.mode == "catalog":
        pool = store.catalog()
        for o in pool:
            observed.setdefault(o.pid, o)
    elif store.mode == "pool":
        pool = featured

    results: dict[str, list[Candidate]] = {}
    failures = 0
    search_cache: dict[str, list[Offer]] = {}
    for item in basket:
        if pool is not None:
            offers = pool
        else:
            offers = []
            for q in item.queries_for(store.key):
                if q not in search_cache:
                    try:
                        search_cache[q] = store.search(q)
                    except Blocked:
                        raise
                    except Exception as exc:
                        failures += 1
                        store.warn(f"pretraga '{q}': {exc}")
                        search_cache[q] = []
                offers.extend(search_cache[q])
        cands = item.rank(offers, limit=50)
        if store.mode == "pool" and cands is not None:
            # sizes may be missing (Lidl) – enrich the matching offers and rank again
            matching = [o for o in offers if item.matches(o) and not o.size and not o.unit_prices]
            if matching:
                store.enrich(matching)
                cands = item.rank(offers, limit=50)
        results[item.id] = cands[:5]
        for c in cands:
            observed.setdefault(c.offer.pid, c.offer)
    if pool is None and failures and failures == len(search_cache):
        raise RuntimeError("sve pretrage su neuspešne")
    # the history only follows food (Aldi's catalogue also lists socks, pans, cat food, ...)
    return promos, results, [o for o in observed.values() if o.food is not False]


def run(
    stores: list[str] | None = None,
    today: dt.date | None = None,
    data_dir: Path = Path("data"),
    http_factory: Callable[[type[Store]], object] = live_http_factory,
    basket: list[BasketItem] | None = None,
) -> dict:
    today = today or dt.date.today()
    basket = basket or load_basket()
    ds = DataStore(data_dir)
    state = ds.load_state()
    summary = {"date": today.isoformat(), "stores": {}}
    for key in stores or list(STORES):
        cls = STORES[key]
        info = state["stores"].setdefault(key, {})
        started = time.monotonic()
        http = http_factory(cls)
        store = cls(http, today)
        store.cache = ds.load_cache(key)
        log.info("── %s ──", cls.name)
        try:
            promos, results, observed = collect(store, basket)
            if not observed:
                raise RuntimeError("nijedan proizvod nije pronađen (možda se promenio sajt prodavnice)")
            usual = (info.get("counts") or {}).get("products") or 0  # from the previous run
            suspicious = len(observed) < usual * MIN_SHARE_OF_USUAL
            if suspicious:
                store.warn(f"pronađeno samo {len(observed)} proizvoda (inače oko {usual})")
            prev_ok = info.get("last_ok")
            rerun = prev_ok == today.isoformat()
            changes = ds.record_offers(key, observed, today, info.get("prev_ok") if rerun else prev_ok,
                                       close_missing=not (rerun or suspicious))
            ds.record_basket(today, key, results)
            ds.write_latest(key, today, promos, results, {"stats": store.stats})
            ds.save_cache(key, store.cache, today)
            status = "partial" if store.warnings else "ok"
            if prev_ok != today.isoformat():
                info["prev_ok"] = prev_ok
            counts = {"promos": len(promos), "products": len(observed), "changes": changes,
                      "basket": sum(1 for v in results.values() if v)}
            info.update({
                "last_ok": today.isoformat(), "status": status, "error": None,
                "warnings": store.warnings[:10], "counts": counts,
            })
            log.info("%s: %d akcija, %d proizvoda, %d promena cena, korpa %d/%d (%d zahteva)",
                     cls.name, len(promos), len(observed), changes, info["counts"]["basket"], len(basket),
                     getattr(http, "count", 0))
        except Exception as exc:  # the other stores still run
            log.exception("%s failed", cls.name)
            info.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"[:400],
                         "warnings": store.warnings[:10]})
        info["last_run"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        info["requests"] = getattr(http, "count", 0)
        info["seconds"] = round(time.monotonic() - started, 1)
        summary["stores"][key] = info["status"]
    state.setdefault("runs", []).append(summary)
    ds.save_state(state)
    return summary
