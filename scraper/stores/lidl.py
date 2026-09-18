"""Lidl Switzerland via the Lidl Plus digital leaflet API.

Lidl does not publish its permanent food prices online, so only leaflet
offers (current week and the upcoming one) are available.
"""
from __future__ import annotations

import datetime as dt
import html
import re
from typing import Any

from ..models import Offer
from ..units import parse_size
from .base import Store, ddmm_to_date

BASE = "https://digital-leaflet.lidlplus.com/api/v1/CH"
NONFOOD_CAMPAIGN = re.compile(
    r"mode|v[êe]tement|sous-v[êe]tement|fleur|plante|bulbe|d[ée]co|ustensile|fonte|animal|crivit|looks|"
    r"cartes?\b|bons? d'achat|salle de bain|beaut[ée]|bureau|technolog|m[ée]nage|jardin|bricol|parkside|"
    r"livarno|silvercrest|esmara|lupilu|pepperts|jouet|textile|chaussure|sport|outil|linge|lingerie|joop|"
    r"jack|vero moda|planter|maison|chez[- ]toi|enfants?\b|b[ée]b[ée]|[ée]lectrique|multim[ée]dia|voiture|v[ée]lo",
    re.I,
)
_RANGE = re.compile(r"(\d{1,2})\.(\d{1,2})\.?\s*[-–]\s*(\d{1,2})\.(\d{1,2})\.?")
_TAG = re.compile(r"<[^>]+>")
ENRICH_LIMIT = 700


class Lidl(Store):
    key = "lidl"
    name = "Lidl"
    mode = "pool"
    headers = {"Accept": "application/json", "Accept-Language": "fr"}
    delay = 0.5
    note = "Lidl objavljuje samo cene iz letka (akcije ove i sledeće nedelje)."

    def promotions(self) -> list[Offer]:
        groups = self.http.get(f"{BASE}/campaignGroups").json().get("groups") or []
        offers: dict[str, Offer] = {}
        skipped = 0
        for gi, group in enumerate(groups):
            upcoming_group = gi > 0 or "prochain" in (group.get("title") or "").lower()
            for camp in group.get("campaigns") or []:
                title = camp.get("title") or ""
                if NONFOOD_CAMPAIGN.search(title):
                    skipped += 1
                    continue
                try:
                    data = self.http.get(f"{BASE}/campaigns/{camp['id']}").json()
                except Exception as exc:
                    self.warn(f"letak {title}: {exc}")
                    continue
                for p in data.get("products") or data.get("items") or []:
                    o = parse_product(p, title, self.today, upcoming_group)
                    if not o:
                        continue
                    old = offers.get(o.pid)
                    if old is None or (old.upcoming and not o.upcoming):
                        offers[o.pid] = o
        self.stats["campaigns_skipped"] = skipped
        result = list(offers.values())
        self.enrich([o for o in result if not o.size])
        return result

    def enrich(self, offers: list[Offer]) -> None:
        """Read pack sizes from product details (cached between runs)."""
        fetched = 0
        failures = 0
        for o in offers:
            if o.size or not o.extra.get("lidl_id"):
                continue
            lid = o.extra["lidl_id"]
            entry = self.cache.get(lid)
            if entry is None:
                if fetched >= ENRICH_LIMIT or failures >= 5:
                    continue
                try:
                    detail = self.http.get(f"{BASE}/products/{lid}").json()
                    fetched += 1
                except Exception as exc:
                    failures += 1
                    self.warn(f"detalji {o.name}: {exc}")
                    continue
                text = " ".join(filter(None, [detail.get("subtitle"), _TAG.sub(" ", html.unescape(detail.get("description") or ""))]))
                entry = {"t": _size_text(text) or ""}
                self.cache[lid] = entry
            # refreshed weekly only, so the cache file does not change every day
            if entry.get("seen", "") < (self.today - dt.timedelta(days=7)).isoformat():
                entry["seen"] = self.today.isoformat()
            size = parse_size(entry.get("t"))
            if size:
                o.size = size
                o.size_label = o.size_label or entry["t"]
        self.stats["details_fetched"] = self.stats.get("details_fetched", 0) + fetched


_SIZE_TEXT = re.compile(
    r"(le kg|le litre|la pi[èe]ce|\d+(?:[.,]\d+)?\s*(?:x|×)\s*\d+(?:[.,]\d+)?\s*(?:kg|g|l|dl|cl|ml)\b"
    r"|\d+(?:[.,]\d+)?\s*(?:kg|g|l|dl|cl|ml|pi[èe]ces?)\b)", re.I)


def _size_text(text: str | None) -> str | None:
    m = _SIZE_TEXT.search(text or "")
    return m.group(1) if m else None


def parse_product(p: dict[str, Any], campaign: str, today: dt.date, upcoming_group: bool = False) -> Offer | None:
    mp = p.get("mainPrice") or {}
    price = mp.get("price")
    if price is None:
        return None
    art = str(p.get("articleNumber") or p.get("wawiId") or p.get("id"))
    plus = (mp.get("priceType") or "").lower() == "lidlplus"
    old = mp.get("oldPrice")
    promo = old is not None and old > price + 0.001
    label = mp.get("discount") or None
    if plus:
        label = f"Lidl Plus {label}" if label else "Lidl Plus"

    valid_from = valid_to = None
    for b in p.get("badges") or []:
        m = _RANGE.search(b.get("title") or "")
        if m:
            valid_from = ddmm_to_date(int(m.group(1)), int(m.group(2)), today)
            valid_to = ddmm_to_date(int(m.group(3)), int(m.group(4)), valid_from)
            if valid_to < valid_from:
                valid_to = valid_to.replace(year=valid_to.year + 1)
            break
    upcoming = upcoming_group or (valid_from is not None and valid_from > today)
    if valid_to is not None and valid_to < today:
        return None

    info = " ".join(x for x in [p.get("subtitle"), p.get("additionalInfo")] if x)
    size = parse_size(info) or parse_size(p.get("title"))
    name = " ".join(x for x in [p.get("title"), p.get("subtitle")] if x)
    return Offer(
        store="lidl",
        pid=f"{art}-plus" if plus else art,
        name=name,
        brand=(p.get("brand") or "").strip() or None,
        price=price,
        regular_price=old if promo else price,
        size_label=(p.get("additionalInfo") or "").strip() or _size_text(info) if info else None,
        size=size,
        promo=promo or plus,
        promo_label=label,
        promo_from=valid_from.isoformat() if valid_from else None,
        promo_to=valid_to.isoformat() if valid_to else None,
        conditional=plus,
        upcoming=upcoming,
        category=campaign,
        food=True,
        url=None,
        image=p.get("imageUrl"),
        extra={"lidl_id": p.get("id")},
    )
