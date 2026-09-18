"""Common helpers for store adapters."""
from __future__ import annotations

import datetime as dt
import logging
import re
from typing import Any

from ..models import Offer

log = logging.getLogger(__name__)


class Store:
    key = ""
    name = ""
    #: "search"  – basket items are looked up with the shop's search
    #: "catalog" – the whole catalogue is downloaded once (Aldi)
    #: "pool"    – only promotional items have public prices (Lidl, Aligro)
    mode = "search"
    impersonate = "chrome"
    headers: dict[str, str] = {}
    delay = 0.8
    note = ""

    def __init__(self, http, today: dt.date | None = None):
        self.http = http
        self.today = today or dt.date.today()
        self.warnings: list[str] = []
        self.stats: dict[str, Any] = {}
        self.cache: dict[str, Any] = {}

    # adapters override what they support
    def promotions(self) -> list[Offer]:
        return []

    def search(self, query: str) -> list[Offer]:
        raise NotImplementedError

    def catalog(self) -> list[Offer]:
        raise NotImplementedError

    def enrich(self, offers: list[Offer]) -> None:
        """Fill in missing pack sizes for a few offers (optional)."""

    def warn(self, msg: str) -> None:
        log.warning("[%s] %s", self.key, msg)
        self.warnings.append(msg)


_CHF = re.compile(r"(\d+(?:[.,]\d+)?)")


def parse_chf(text: str | None) -> float | None:
    """'CHF 3.79' -> 3.79, '21.-' -> 21.0"""
    if not text:
        return None
    m = _CHF.search(str(text).replace("'", ""))
    return float(m.group(1).replace(",", ".")) if m else None


def pct_label(value: float | None) -> str | None:
    if value is None or value <= 0:
        return None
    return f"-{round(value)}%"


def ddmm_to_date(day: int, month: int, today: dt.date) -> dt.date:
    """Resolve a day.month without year to the closest date around today."""
    best = None
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            d = dt.date(year, month, day)
        except ValueError:
            continue
        if best is None or abs((d - today).days) < abs((best - today).days):
            best = d
    return best or today


def first(seq, default=None):
    for x in seq or ():
        return x
    return default
