import pytest

from conftest import load
from scraper.basket import load_basket
from scraper.models import Offer
from scraper.stores.coop import parse_product
from scraper.units import parse_size


@pytest.fixture(scope="module")
def basket():
    return {i.id: i for i in load_basket()}


def offer(name, price, size, **kw):
    return Offer(store="t", pid=name, name=name, price=price, size=parse_size(size), size_label=size, **kw)


def test_basket_file_is_valid(basket):
    assert len(basket) >= 25
    for item in basket.values():
        assert item.unit in ("kg", "l", "pc")
        assert item.queries_for("coop")


@pytest.mark.parametrize("item,name,ok", [
    ("pileca-prsa", "Poitrine de poulet", True),
    ("pileca-prsa", "Escalopes de poulet panées", False),
    ("pileca-prsa", "Poitrine de poulet fumé en tranches", False),
    ("mleko", "Lait entier UHT 3.5%", True),
    ("mleko", "Chocolat au lait entier", False),
    ("jaja", "Œufs d'élevage au sol 53g+", True),
    ("jaja", "Œufs pique-nique", False),
    ("jabuke", "Pommes Gala", True),
    ("jabuke", "Pommes de terre fermes", False),
    ("krompir", "Pommes de terre à chair farineuse", True),
    ("banane", "Migros Travel & Co. · Banane", False),
    ("skyr", "Skyr Nature", True),
    ("skyr", "Skyr Mangue", False),
    ("maslac", "Beurre de crème douce", True),
    ("maslac", "Croissant au beurre", False),
])
def test_matching_rules(basket, item, name, ok):
    assert basket[item].matches(offer(name, 1.0, "1 kg")) is ok


def test_nonfood_offers_never_match(basket):
    o = offer("Poitrine de poulet", 5, "500 g", food=False)
    assert not basket["pileca-prsa"].matches(o)


def test_rank_picks_cheapest_unit_price_and_respects_min_qty(basket):
    item = basket["mleko"]
    offers = [
        offer("Lait entier 1 l", 1.60, "1 l"),
        offer("Lait entier 6 x 1 l", 8.40, "6 x 1l"),
        offer("Lait entier 250 ml", 0.30, "250 ml"),  # cheaper per litre but below min_qty
    ]
    ranked = item.rank(offers)
    assert [c.offer.name for c in ranked] == ["Lait entier 6 x 1 l", "Lait entier 1 l"]
    assert ranked[0].unit_price == pytest.approx(1.40)


def test_conditional_promos_use_regular_price(basket):
    o = offer("Lait entier", 1.0, "1 l", regular_price=2.0, promo=True, conditional=True)
    c = basket["mleko"].evaluate(o)
    assert c.unit_price == pytest.approx(2.0)


def test_density_converts_ml_for_canned_tomatoes(basket):
    o = offer("Tomates concassées bio Tetra Pak", 1.65, "390 ml")
    c = basket["pelat"].evaluate(o)
    assert c.unit_price == pytest.approx(1.65 / 0.39)


def test_weight_sold_items_skip_min_qty(basket):
    o = Offer(store="t", pid="1", name="Poitrine de poulet", price=3.2, size=parse_size("100 g"))
    o.size.approx = True
    assert basket["pileca-prsa"].evaluate(o) is not None


def test_coop_fixture_ranking(basket):
    offers = [parse_product(p) for p in load("coop_products.json")["products"]]
    ranked = basket["mleko"].rank(offers)
    assert ranked[0].offer.pid == "3081654"  # 1.90/l beats 1.95/l


def test_fresh_and_frozen_are_kept_apart():
    from scraper.basket import load_basket
    from scraper.models import Offer
    from scraper.units import Size
    items = {i.id: i for i in load_basket()}
    fresh = Offer("coop", "1", "Poitrines de poulet", 12.0, size=Size({"kg": 1.0}), category="Volaille fraîche")
    frozen = Offer("coop", "2", "Poitrines de poulet", 9.0, size=Size({"kg": 1.0}), category="Produits à base de viande surgelée")
    thawed = Offer("coop", "3", "Crevettes cuites, décongelées", 5.0, size=Size({"kg": 0.2}))
    assert frozen.frozen and not fresh.frozen and not thawed.frozen
    assert frozen.to_dict()["fz"] == 1 and "fz" not in fresh.to_dict()
    assert items["pileca-prsa"].matches(fresh) and not items["pileca-prsa"].matches(frozen)
    assert items["pileca-prsa-smrznuta"].matches(frozen) and not items["pileca-prsa-smrznuta"].matches(fresh)
    assert items["pileca-prsa"].rank([fresh, frozen])[0].offer is fresh
