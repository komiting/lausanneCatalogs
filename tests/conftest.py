import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scraper.http import HttpError, Response  # noqa: E402

FIX = ROOT / "tests" / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


class FakeHttp:
    """Routes requests to canned responses by regex on 'METHOD url'."""

    def __init__(self, routes):
        self.routes = [(re.compile(p), r) for p, r in routes]
        self.calls = []
        self.count = 0

    def request(self, method, url, *, params=None, json_body=None, headers=None):
        full = url
        if params:
            full += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        key = f"{method} {full}"
        self.calls.append((key, json_body))
        self.count += 1
        for rx, resp in self.routes:
            if rx.search(key):
                value = resp(key, json_body) if callable(resp) else resp
                if isinstance(value, Response):
                    return value
                if isinstance(value, Exception):
                    raise value
                text = value if isinstance(value, str) else json.dumps(value)
                return Response(200, full, text, {"leshopch": "TOKEN"})
        raise HttpError(f"unrouted {key}", 404)

    def get(self, url, params=None, headers=None):
        return self.request("GET", url, params=params, headers=headers)

    def post(self, url, json_body=None, headers=None):
        return self.request("POST", url, json_body=json_body, headers=headers)


def fake_routes():
    migros_cards = load("migros_product_cards.json")
    ids = [c["uid"] for c in migros_cards]

    def cards(key, body):
        wanted = set(body["productFilter"]["uids"])
        return [c for c in migros_cards if c["uid"] in wanted]

    coop = load("coop_products.json")
    aldi = load("aldi_search.json")
    aligro = load("aligro_actions.json")

    def aligro_page(key, body):
        if "11-viande" in key and "offset=1" in key:
            return aligro
        return {"categories": [], "articles": {"current_page_number": 1, "items_per_page": 192, "total_items": 0, "items": []}}

    return [
        (r"GET .*migros\.ch/authentication", {}),
        (r"POST .*migros\.ch/.*/products/search$", {"items": [{"id": i, "type": "PRODUCT"} for i in ids], "numberOfProducts": len(ids)}),
        (r"POST .*migros\.ch/.*/product-cards", cards),
        (r"POST .*migros\.ch/.*/promotion/search", {"items": [{"id": 100202065, "type": "PRODUCT"}, {"id": "1", "type": "GROUP_PROMOTION"}],
                                                   "startDate": "2026-09-10", "endDate": "2026-09-16"}),
        (r"GET .*coop\.ch/.*/products/search/", coop),
        (r"GET .*coop\.ch/.*/products/category/m_1111", coop),
        (r"GET .*api\.aldi-suisse\.ch/v3/product-search.*offset=0", aldi),
        (r"GET .*api\.aldi-suisse\.ch/v3/product-search", {"meta": {"pagination": {"totalCount": 4}}, "data": []}),
        (r"GET .*lidlplus\.com/api/v1/CH/campaignGroups", load("lidl_campaign_groups.json")),
        (r"GET .*lidlplus\.com/api/v1/CH/campaigns/10102253", load("lidl_campaign.json")),
        (r"GET .*lidlplus\.com/api/v1/CH/campaigns/", {"products": []}),
        (r"GET .*lidlplus\.com/api/v1/CH/products/", load("lidl_product_detail.json")),
        (r"GET .*aligro\.ch/cart/change-market", "<html></html>"),
        (r"GET .*aligro\.ch/actions$", "<html><span>du 14.9 au 19.9</span></html>"),
        (r"GET .*aligro\.ch/actions/.*\.json", aligro_page),
    ]


@pytest.fixture
def fake_http():
    return FakeHttp(fake_routes())
