import pytest

from scraper.units import fold, parse_size, per_unit_from_display


@pytest.mark.parametrize("label,dims,approx", [
    ("720g", {"kg": 0.72}, False),
    ("6 x 1l", {"l": 6.0, "pc": 6.0}, False),
    ("4x 250 g", {"kg": 1.0, "pc": 4.0}, False),
    ("0,75 l", {"l": 0.75}, False),
    ("env. 600 g", {"kg": 0.6}, True),
    ("Le kg", {"kg": 1.0}, True),
    ("10 pièces", {"pc": 10.0}, False),
    ("6er", {"pc": 6.0}, False),
    ("500g x 2", {"kg": 1.0, "pc": 2.0}, False),
    ("12 x 33 cl", {"l": 3.96, "pc": 12.0}, False),
    ("approximativement 0,4 kg/pièce", {"kg": 0.4}, True),
    ("1'000 g", {"kg": 1.0}, False),
])
def test_parse_size(label, dims, approx):
    s = parse_size(label)
    assert s.dims.keys() == dims.keys()
    for k, v in dims.items():
        assert s.dims[k] == pytest.approx(v)
    assert s.approx == approx


def test_no_size_in_fat_percentage():
    assert not parse_size("Lait entier · Bio, 3.5% de gras")


def test_per_unit_display():
    assert per_unit_from_display("CHF 1.40/100 g") == ("kg", pytest.approx(14.0))
    assert per_unit_from_display("CHF 11.99/1 l") == ("l", pytest.approx(11.99))
    assert per_unit_from_display("CHF 0.45/1 pce") == ("pc", pytest.approx(0.45))
    assert per_unit_from_display(None) is None


def test_fold():
    assert fold("Œufs Élevage") == "oeufs elevage"
    assert fold(None) == ""


def test_kilo_words_and_bom():
    assert parse_size("﻿1 kilo").dims == {"kg": 1.0}
    s = parse_size("Le kilo")
    assert s.dims == {"kg": 1.0} and s.per_unit == "kg"


def test_pet_food_is_never_food():
    from scraper.models import Offer
    assert Offer("aldi", "1", "Poulet à la crème", 1.95, brand="DREAMIES").food is False
    assert Offer("lidl", "2", "Nourriture humide pour chats", 3.0, food=True).food is False
    assert Offer("aldi", "3", "Litière pour chat, hygiène", 6.0, brand="CATSAN").food is False
    assert Offer("coop", "4", "Salade César au poulet", 5.0).food is None
    assert Offer("migros", "5", "Filets de poulet", 5.0, food=True).food is True
