import datetime as dt
import json

from conftest import FakeHttp, fake_routes
from scraper.build import build
from scraper.run import run
from scraper.storage import DataStore


def factory(_cls):
    return FakeHttp(fake_routes())


def test_full_run_and_build(tmp_path):
    data, site = tmp_path / "data", tmp_path / "site"
    day1 = dt.date(2026, 9, 15)
    summary = run(None, day1, data, factory)
    assert set(summary["stores"].values()) <= {"ok", "partial"}, summary

    ds = DataStore(data)
    basket_rows = ds.read_basket()
    assert any(r["item"] == "mleko" and r["store"] == "coop" for r in basket_rows)

    # second day: same prices -> no new price rows, spans extended
    day2 = dt.date(2026, 9, 16)
    before = {k: len(ds.read_prices(k)) for k in ("migros", "coop", "aldi")}
    run(None, day2, data, factory)
    after = {k: len(ds.read_prices(k)) for k in ("migros", "coop", "aldi")}
    assert before == after
    prod = ds.load_products("aldi")["153249"]
    assert prod["sp"] == [["2026-09-15", None]]
    assert ds.products_path("aldi").read_text().count("\n") > 3  # one product per line

    sizes = build(data, site)
    assert "basket.json" in sizes and "promos.json" in sizes
    basket = json.loads((site / "data" / "basket.json").read_text())
    assert basket["dates"] == ["2026-09-15", "2026-09-16"]
    milk = next(i for i in basket["items"] if i["id"] == "mleko")
    assert milk["hist"]["coop"] == [1.9, 1.9]
    assert milk["now"]["coop"]["cands"][0]["id"] == "3081654"
    promos = json.loads((site / "data" / "promos.json").read_text())
    stores = {p["st"] for p in promos}
    assert stores == {"migros", "coop", "aldi", "lidl", "aligro"}
    meta = json.loads((site / "data" / "meta.json").read_text())
    assert meta["stores"]["aldi"]["status"] in ("ok", "partial")
    hist = json.loads((site / "data" / "history" / "aldi.json").read_text())
    assert hist["153249"]["h"][0][1] == 2.79
    assert hist["153249"]["sp"] == [["2026-09-15", "2026-09-16"]] and hist["153249"]["l"] == "2026-09-16"


def test_spans_close_and_reopen(tmp_path):
    from scraper.models import Offer
    ds = DataStore(tmp_path)
    milk = Offer("aldi", "1", "Lait", 1.2)
    bread = Offer("aldi", "2", "Pain", 2.0)
    d = [dt.date(2026, 9, day) for day in (14, 15, 16, 17, 18)]
    assert ds.record_offers("aldi", [milk, bread], d[0], None) == 2
    ds.record_offers("aldi", [milk, bread], d[1], "2026-09-14")
    before = ds.products_path("aldi").read_text()
    ds.record_offers("aldi", [milk, bread], d[2], "2026-09-15")
    assert ds.products_path("aldi").read_text() == before  # nothing changed -> file unchanged
    ds.record_offers("aldi", [milk], d[3], "2026-09-16")  # bread missing
    ds.record_offers("aldi", [bread], d[3], "2026-09-16", close_missing=False)  # same-day rerun only adds
    p = ds.load_products("aldi")
    assert p["2"]["sp"] == [["2026-09-14", None]]
    ds.record_offers("aldi", [milk], d[4], "2026-09-17")  # bread gone for real
    p = ds.load_products("aldi")
    assert p["1"]["sp"] == [["2026-09-14", None]]
    assert p["2"]["sp"] == [["2026-09-14", "2026-09-17"]]
    rows = ds.read_prices("aldi")
    assert [(r["date"], r["pid"]) for r in rows] == [("2026-09-14", "1"), ("2026-09-14", "2")]
    ds.record_offers("aldi", [milk, bread], dt.date(2026, 9, 20), "2026-09-18")  # bread is back
    p = ds.load_products("aldi")
    assert p["2"]["sp"] == [["2026-09-14", "2026-09-17"], ["2026-09-20", None]]
    assert [(r["date"], r["pid"]) for r in ds.read_prices("aldi")][-1] == ("2026-09-20", "2")


def test_price_change_is_logged(tmp_path):
    data = tmp_path / "data"
    run(["aldi"], dt.date(2026, 9, 15), data, factory)
    routes = fake_routes()
    catalog = json.loads(json.dumps(routes[6][1]))
    catalog["data"][0]["price"]["amount"] = 259
    routes[6] = (routes[6][0], catalog)
    run(["aldi"], dt.date(2026, 9, 16), data, lambda _c: FakeHttp(routes))
    rows = [r for r in DataStore(data).read_prices("aldi") if r["pid"] == "153249"]
    assert [(r["date"], r["price"]) for r in rows] == [("2026-09-15", "2.79"), ("2026-09-16", "2.59")]


def test_store_failure_is_isolated(tmp_path):
    data = tmp_path / "data"

    def broken(cls):
        if cls.key == "coop":
            return FakeHttp([(r".*", RuntimeError("boom"))])
        return FakeHttp(fake_routes())

    summary = run(None, dt.date(2026, 9, 16), data, broken)
    assert summary["stores"]["coop"] == "error"
    assert summary["stores"]["migros"] in ("ok", "partial")
    state = DataStore(data).load_state()
    assert "boom" in (state["stores"]["coop"]["error"] or "") or state["stores"]["coop"]["warnings"]


def test_long_pause_breaks_spans(tmp_path):
    from scraper.models import Offer
    ds = DataStore(tmp_path)
    milk = Offer("aldi", "1", "Lait", 1.2)
    ds.record_offers("aldi", [milk], dt.date(2026, 9, 1), None)
    ds.record_offers("aldi", [milk], dt.date(2026, 9, 2), "2026-09-01")
    ds.record_offers("aldi", [milk], dt.date(2026, 9, 20), "2026-09-02")  # 18 days without data
    p = ds.load_products("aldi")["1"]
    assert p["sp"] == [["2026-09-01", "2026-09-02"], ["2026-09-20", None]]
    assert [r["date"] for r in ds.read_prices("aldi")] == ["2026-09-01", "2026-09-20"]


def _aldi_routes(n_products):
    routes = fake_routes()
    catalog = json.loads(json.dumps(routes[6][1]))
    base = catalog["data"][0]
    items = []
    for i in range(n_products):
        item = json.loads(json.dumps(base))
        item["sku"] = f"{900000 + i:018d}"
        item["name"] = f"{base['name']} {i}"
        items.append(item)
    catalog["data"] = items
    catalog["meta"]["pagination"]["totalCount"] = n_products
    routes[6] = (routes[6][0], catalog)
    return routes


def test_empty_or_shrunken_results_do_not_wipe_history(tmp_path):
    data = tmp_path / "data"
    ds = DataStore(data)
    run(["aldi"], dt.date(2026, 9, 15), data, lambda _c: FakeHttp(_aldi_routes(12)))
    before = ds.load_products("aldi")
    assert len(before) >= 12

    # the store suddenly returns nothing: an error, nothing recorded
    summary = run(["aldi"], dt.date(2026, 9, 16), data, lambda _c: FakeHttp(_aldi_routes(0)))
    assert summary["stores"]["aldi"] == "error"
    assert ds.load_products("aldi") == before
    assert ds.load_state()["stores"]["aldi"]["last_ok"] == "2026-09-15"

    # only a small part of the usual catalogue: a warning, and spans stay open
    summary = run(["aldi"], dt.date(2026, 9, 17), data, lambda _c: FakeHttp(_aldi_routes(2)))
    assert summary["stores"]["aldi"] == "partial"
    assert all(p["sp"][-1][1] is None for p in ds.load_products("aldi").values())

    # back to normal: products that are really gone get closed
    summary = run(["aldi"], dt.date(2026, 9, 18), data, lambda _c: FakeHttp(_aldi_routes(10)))
    assert summary["stores"]["aldi"] == "ok"
    products = ds.load_products("aldi")
    closed = sorted(pid for pid, p in products.items() if p["sp"][-1][1] is not None)
    assert closed == ["900010", "900011"]
    assert all(products[pid]["sp"] == [["2026-09-15", "2026-09-17"]] for pid in closed)
