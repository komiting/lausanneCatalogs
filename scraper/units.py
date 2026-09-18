"""Pack-size parsing and unit-price helpers.

Sizes come in many shapes ("720g", "6 x 1l", "4x 250 g", "env. 600 g",
"Le kg", "10 pièces", "0,75 l"). ``parse_size`` turns such a label into
quantities per *dimension*: ``kg`` for mass, ``l`` for volume and ``pc`` for
pieces. A label can carry several dimensions ("10 pièces 53g+"), and the
basket item decides which one it compares on.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

MASS = {"mg": 0.000001, "g": 0.001, "gr": 0.001, "kg": 1.0, "kilo": 1.0, "kilos": 1.0}
VOLUME = {"ml": 0.001, "cl": 0.01, "dl": 0.1, "l": 1.0, "lt": 1.0, "litre": 1.0, "litres": 1.0, "liter": 1.0}
UNIT_WORDS = "|".join(sorted(list(MASS) + list(VOLUME), key=len, reverse=True))
NUM = r"(\d+(?:\.\d+)?)"

_MULTI = re.compile(rf"{NUM}\s*x\s*{NUM}\s*({UNIT_WORDS})(?![a-z])")
_MULTI_REV = re.compile(rf"{NUM}\s*({UNIT_WORDS})\s*x\s*{NUM}(?![\d.])")
_SIMPLE = re.compile(rf"{NUM}\s*({UNIT_WORDS})(?![a-z])")
_PIECES = re.compile(
    rf"{NUM}\s*(?:x\s*)?(pieces|piece|pces|pce|pcs|pc|stuck|stk|st\b|er\b|oeufs|eier|oeuf|portions|tranches|sachets|rouleaux|capsules)"
)
_PER_KG = re.compile(r"\b(?:le|au|par|pro|per|prix au)\s*kg\b|(?<![\d\s])\s*/\s*kg\b|\b(?:au|le|par) kilo\b|^\s*kg\s*$")
_PER_L = re.compile(r"\b(?:le|au|par|pro|per)\s*(?:l|litre|liter)\b|(?<![\d\s])\s*/\s*l\b|^\s*l\s*$")
_PER_PC = re.compile(r"\b(?:la|par|pro|per)\s*(?:piece|pce|stuck|stk)\b|^\s*(?:piece|pce|stuck)\s*$")
_APPROX = re.compile(r"\b(?:env|environ|ca|circa|ungefahr|approx\w*)\b")


def fold(text: str | None) -> str:
    """Lowercase, strip accents and normalise separators for matching."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("œ", "oe").replace("æ", "ae").replace("ß", "ss")
    text = text.replace(" ", " ").replace("×", "x").replace("’", "'")
    return text


def _num(s: str) -> float:
    return float(s)


def _normalise_numbers(text: str) -> str:
    # "0,75 l" -> "0.75 l"; "1'000 g" -> "1000 g"
    text = re.sub(r"(\d),(\d)", r"\1.\2", text)
    text = re.sub(r"(\d)'(\d{3})", r"\1\2", text)
    return text


def _to_dim(value: float, unit: str) -> tuple[str, float] | None:
    unit = unit.lower()
    if unit in MASS:
        return "kg", value * MASS[unit]
    if unit in VOLUME:
        return "l", value * VOLUME[unit]
    return None


@dataclass
class Size:
    """Quantities of one pack, by dimension."""

    dims: dict[str, float] = field(default_factory=dict)
    approx: bool = False
    per_unit: str | None = None  # "kg"/"l"/"pc" when the price is quoted per unit

    def get(self, dim: str) -> float | None:
        return self.dims.get(dim)

    def __bool__(self) -> bool:
        return bool(self.dims)


def parse_size(label: str | None) -> Size:
    """Parse a pack-size label into quantities (kg / l / pc)."""
    size = Size()
    text = _normalise_numbers(fold(label))
    if not text.strip():
        return size
    size.approx = bool(_APPROX.search(text))

    for rx, dim in ((_PER_KG, "kg"), (_PER_L, "l")):
        if rx.search(text) and not _SIMPLE.search(rx.sub(" ", text)):
            size.dims[dim] = 1.0
            size.per_unit = dim
            size.approx = True
            return size

    m = _MULTI.search(text)
    if m:
        count, value, unit = _num(m.group(1)), _num(m.group(2)), m.group(3)
        dim = _to_dim(count * value, unit)
        if dim:
            size.dims[dim[0]] = dim[1]
            size.dims.setdefault("pc", count)
    else:
        m = _MULTI_REV.search(text)
        if m:
            value, unit, count = _num(m.group(1)), m.group(2), _num(m.group(3))
            dim = _to_dim(count * value, unit)
            if dim:
                size.dims[dim[0]] = dim[1]
                size.dims.setdefault("pc", count)
        else:
            for m in _SIMPLE.finditer(text):
                dim = _to_dim(_num(m.group(1)), m.group(2))
                if dim and dim[0] not in size.dims:
                    size.dims[dim[0]] = dim[1]

    m = _PIECES.search(text)
    if m:
        size.dims["pc"] = _num(m.group(1))
    elif not size.dims and _PER_PC.search(text):
        size.dims["pc"] = 1.0
        size.per_unit = "pc"
    return size


def unit_label(dim: str) -> str:
    return {"kg": "kg", "l": "l", "pc": "kom"}.get(dim, dim)


def round_price(value: float | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(float(value) + 1e-9, digits)


def per_unit_from_display(display: str | None) -> tuple[str, float] | None:
    """Parse a reference like "CHF 1.40/100 g" -> ("kg", 14.0) CHF per kg/l/pc."""
    text = _normalise_numbers(fold(display))
    m = re.search(rf"{NUM}\s*/\s*{NUM}?\s*({UNIT_WORDS}|pce|piece|pc|stk|stuck)\b", text)
    if not m:
        return None
    price = _num(m.group(1))
    amount = _num(m.group(2)) if m.group(2) else 1.0
    unit = m.group(3)
    if unit in ("pce", "piece", "pc", "stk", "stuck"):
        return "pc", price / amount
    dim = _to_dim(amount, unit)
    if not dim or dim[1] <= 0:
        return None
    return dim[0], price / dim[1]
