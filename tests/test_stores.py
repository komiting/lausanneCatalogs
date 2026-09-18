import datetime as dt

import pytest

from conftest import load
from scraper.stores.aldi import Aldi, parse_product as aldi_parse
from scraper.stores.aligro import Aligro, parse_article
from scraper.stores.coop import Coop, parse_product as coop_parse
from scraper.stores.lidl import Lidl, parse_product as lidl_parse
from scraper.stores.migros import Migros, parse_card

TODAY = dt.date(2026, 9, 16)


def test_migros_variable_weight_and_promo():
    cards = {c["uid"]: c for c in load("migros_product_cards.json")}
    vw = parse_card(cards[100031081])
    assert vw.size.approx and vw.unit_price("kg") == pytest.approx(22.5)
    promo = parse_card(cards[100202065])
    assert promo.promo and promo.price == 1.55 and promo.regular_price == 2.15
    assert promo.promo_label == "-27%" and promo.promo_to == "2026-09-16"
    assert promo.unit_price("kg") == pytest.approx(15.5)
    assert promo.unit_price("kg", regular=True) == pytest.approx(21.5)
    milk = parse_card(cards[100006357])
    assert milk.unit_price("l") == pytest.approx(1.85) and milk.food is True
    assert milk.url.endswith("/fr/product/204003200000")
    assert "{stack}" not in milk.image


def test_migros_card_without_price_is_skipped():
    assert parse_card({"uid": 1, "title": "x", "offer": {"isVariableWeight": False}}) is None


def test_coop_parsing():
    ps = {p["code"]: coop_parse(p) for p in load("coop_products.json")["products"]}
    salmon = ps["7126974"]
    assert salmon.promo and salmon.promo_label == "-50%" and salmon.regular_price == 23.85
    assert salmon.unit_price("kg") == pytest.approx(11.9 / 0.18)
    cond = ps["5939358"]
    assert cond.promo and cond.conditional and cond.price is None
    multi = ps["3481749"]
    assert multi.size.get("kg") == pytest.approx(1.0) and multi.unit_price("kg") == pytest.approx(8.95)
    milk = ps["4389992"]
    assert not milk.promo and milk.unit_price("l") == pytest.approx(1.95) and milk.food


def test_aldi_parsing():
    ps = {p.pid: p for p in (aldi_parse(x, TODAY) for x in load("aldi_search.json")["data"])}
    nuts = ps["153249"]
    assert nuts.promo and nuts.regular_price == 2.85 and nuts.unit_price("kg") == pytest.approx(14.0)
    assert "Prix cassé" in nuts.promo_label and nuts.brand == "Happy Harvest"
    chicken = ps["288503"]
    assert chicken.size.per_unit == "kg" and chicken.unit_price("kg") == pytest.approx(5.9)
    assert chicken.url == "https://www.aldi-suisse.ch/fr/p.saveurs-suisses-poulet-entier.000000000000288503.html"
    assert ps["107096"].unit_price("kg") is None


def test_lidl_parsing():
    camp = load("lidl_campaign.json")
    offers = [lidl_parse(p, camp["title"], TODAY) for p in camp["products"]]
    apples, pumpkin, pumpkin_plus, chicken = offers
    assert apples.promo and apples.promo_from == "2026-09-10" and apples.promo_to == "2026-09-16"
    assert pumpkin_plus.pid.endswith("-plus") and pumpkin_plus.conditional
    assert pumpkin_plus.effective_price == 2.99
    assert chicken.unit_price("kg") == pytest.approx(6.98)
    expired = lidl_parse(camp["products"][0], "x", dt.date(2026, 9, 20))
    assert expired is None


def test_lidl_enrich_reads_size_from_details(fake_http):
    store = Lidl(fake_http, TODAY)
    offers = store.promotions()
    assert {o.pid for o in offers} >= {"0080220", "0082840", "0082840-plus"}
    apples = next(o for o in offers if o.pid == "0080220")
    store.enrich([apples])
    assert apples.size.per_unit == "kg" and apples.unit_price("kg") == pytest.approx(1.99)


def test_lidl_skips_nonfood_campaigns(fake_http):
    Lidl(fake_http, TODAY).promotions()
    urls = " ".join(c[0] for c in fake_http.calls)
    assert "10102238" not in urls and "10020523" not in urls  # fashion, gift cards


def test_aligro_parsing():
    items = {i["sKU"]: parse_article(i) for i in load("aligro_actions.json")["articles"]["items"]}
    giz = items["454724-KG"]
    assert giz.size.per_unit == "kg" and giz.unit_price("kg") == 4.9 and giz.promo_label == "-19%"
    mince = items["39095-PAK"]
    assert mince.unit_price("kg") == pytest.approx(24.5 / 1.5)
    assert mince.name.startswith("Viande hachée de bœuf frais")
    assert items["80646-ST"].unit_price("l") == pytest.approx(1.35)


def test_aligro_promotions_use_page_dates(fake_http):
    offers = Aligro(fake_http, TODAY).promotions()
    assert len(offers) == 4
    assert all(o.promo_from == "2026-09-14" and o.promo_to == "2026-09-19" for o in offers)


def test_migros_store_flow(fake_http):
    m = Migros(fake_http, TODAY)
    found = m.search("poulet")
    assert len(found) == 4
    promos = m.promotions()
    assert [o.pid for o in promos] == ["100202065"]
    assert m.stats["group_promotions"] == 1
    # the guest token is fetched once and cards are cached
    assert sum("authentication" in c[0] for c in fake_http.calls) == 1


def test_coop_promotions_stop_paging(fake_http):
    promos = Coop(fake_http, TODAY).promotions()
    assert {o.pid for o in promos} == {"7126974", "5939358", "3481749"}


def test_aldi_catalog_is_cached(fake_http):
    a = Aldi(fake_http, TODAY)
    assert len(a.catalog()) == 4
    assert len(a.promotions()) == 2
    assert sum("product-search" in c[0] for c in fake_http.calls) == 1


def test_aligro_multipack_uses_full_price_label():
    item = {
        "sKU": "51921-Z05", "packagingLabel": "120 g", "quantityLabelForFullPrice": "5 x 120 g",
        "quantityUnit": {"code": "Z05", "number": 5}, "weightSellable": False,
        "translations": {"fr": {"advertisingText": "Thon", "weightVolume": "120 g"}},
        "mainArticleDetailPrice": {"salesPriceTTC": 11.3, "discountPriceTTC": 8.3, "discountRatePrivate": 0.265},
        "href": {"self": "https://www.aligro.ch/produits/51921-Z05/thon"},
    }
    o = parse_article(item)
    assert o.size_label == "5 x 120 g"
    assert o.unit_price("kg") == pytest.approx(8.3 / 0.6)
    colis = dict(item, quantityLabelForFullPrice="6 Colis de 6", quantityUnit={"code": "Z06", "number": 6})
    o2 = parse_article(colis)
    assert o2.size.get("kg") == pytest.approx(0.72) and o2.size_label == "6 x 120 g"
