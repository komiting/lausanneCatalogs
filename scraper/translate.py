"""Serbian names for products (the stores publish French, sometimes Italian or German names).

Three layers, best first:

1. ``data/translations/sr.json`` – translation memory: normalised original name -> Serbian name.
   It already holds everything seen so far. `python -m scraper translate` adds new names every
   day when an ``ANTHROPIC_API_KEY`` is available. Lines can be corrected by hand.
2. ``data/translations/sr-terms.json`` – product-type phrases ("hauts de cuisses de poulet" ->
   "pileći bataci"). They give an approximate name (shown with "≈") for anything the memory
   does not know yet.
3. Nothing – the site shows only the original name.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable

from .storage import DataStore, _write_json
from .units import UNIT_WORDS, fold

log = logging.getLogger(__name__)

LANG = "sr"
STOP_WORDS = {
    "a", "au", "aux", "avec", "d", "de", "des", "di", "du", "e", "en", "et", "l", "la", "le", "les",
    "mit", "ou", "par", "pour", "sans", "sur", "und", "un", "une", "con", "al", "alla", "del", "della",
}
# attributes that are safe to append to any approximate name (no grammatical agreement needed)
MODIFIERS = {
    "bio": "bio",
    "vegan": "vegan",
    "vegane": "vegan",
    "veggie": "vegetarijansko",
    "vegetarien": "vegetarijansko",
    "vegetarienne": "vegetarijansko",
    "surgele": "zamrznuto",
    "surgeles": "zamrznuto",
    "surgelee": "zamrznuto",
    "surgelees": "zamrznuto",
    "congele": "zamrznuto",
    "sans lactose": "bez laktoze",
    "sans gluten": "bez glutena",
    "sans sucre": "bez šećera",
    "light": "light",
    "zero": "zero",
}

_APPROX_WORDS = re.compile(r"\b(?:env|environ|ca|circa|approx\w*)\b\.?")
_SIZE = re.compile(
    rf"\d+(?:[.,']\d+)?\s*(?:-\s*\d+(?:[.,]\d+)?\s*)?(?:x\s*\d+(?:[.,]\d+)?\s*)?(?:{UNIT_WORDS})(?![a-z])"
)
_TIMES = re.compile(r"\b\d+\s*x(?![a-z])|(?<![a-z])x\s*\d+\b")
_PIECES = re.compile(r"\b\d+\s*(?:pieces?|pces?|pcs|pc|stuck|stk|er|portions?|tranches?|sachets?|capsules?|rouleaux)\b")
_PERCENT = re.compile(r"\d+(?:[.,]\d+)?\s*%")
_NON_WORD = re.compile(r"[^a-z0-9]+")


def name_key(name: str | None) -> str:
    """Normalised name used to look up translations: accents, sizes, counts and punctuation removed.

    "Filets de cabillaud frais, 200-400 g" -> "filets de cabillaud frais"
    """
    text = fold(name)
    text = re.sub(r"(\d)'(\d{3})", r"\1\2", text)
    text = _APPROX_WORDS.sub(" ", text)
    text = _SIZE.sub(" ", text)
    text = _PERCENT.sub(" ", text)
    text = _PIECES.sub(" ", text)
    text = _TIMES.sub(" ", text)
    text = _NON_WORD.sub(" ", text)
    words = text.split()
    while words and (words[-1] in STOP_WORDS or words[-1].isdigit()):
        words.pop()
    return " ".join(words)


STYLE_GUIDE = """\
You translate supermarket product names from Swiss shops (mostly French, sometimes Italian or German)
into short Serbian product names for a Serbian-speaking shopper in Lausanne.

Rules:
- Serbian, Latin script (č, ć, š, ž, đ), ekavian. Natural, the way a Serbian supermarket labels a product:
  "Pileći bataci", "Dimljeni losos", "Sladoled od čokolade", "Seoske zemičke sa semenkama".
- Say what the product is: type, main ingredient or flavour, cut of meat, preparation (dimljen, pohovan,
  mariniran, sušen) and notable attributes (bio, bez laktoze, integralni, vegan).
- Leave out brands, shop lines and marketing words (Naturaplan, Prix Garantie, M-Budget, Qualité&Prix,
  Karma, Betty Bossi, Fine Food, Primagusto, XXL, Big Pack, original ...), pack sizes, weights, piece
  counts and fat percentages: the shop shows those separately.
- Keep a brand only when the brand is the product (Nutella, Coca-Cola, Toffifee, Rivella, Ovomaltine,
  Kinder Bueno) and add a short description: "Toffifee (bombone sa lešnikom)".
- Wine, beer, spirits: "Crno vino, Toskana", "Belo vino Chenin blanc", "Pivo", "Viski".
- Named cheeses and charcuterie keep their name with a description: "Sir Gruyère", "Pršut San Daniele".
- Preferred terms: poulet = piletina/pileći, bœuf = junetina/juneći, porc = svinjetina/svinjski,
  veau = teletina/teleći, dinde = ćuretina/ćureći, agneau = jagnjetina, saumon = losos,
  thon = tunjevina, cabillaud = bakalar, crevettes = škampi, beurre = maslac, crème entière = slatka
  pavlaka, crème acidulée = kisela pavlaka, séré = posni sir, yogourt = jogurt, fromage = sir,
  lait = mleko, pain = hleb, petits pains = zemičke, pâtes = testenina, riz = pirinač,
  pommes de terre = krompir, tomates = paradajz, poivron = paprika, oignons = crni luk,
  flocons d'avoine = ovsene pahuljice, pois chiches = leblebije, amandes = bademi,
  noisettes = lešnici, noix = orasi, noix de cajou = indijski orah, glace = sladoled, chips = čips,
  jus = sok, eau minérale = mineralna voda.
- At most about 50 characters. Never invent details that are not in the name. If the name is
  unclear, give the most likely generic product type (for example "Čokoladni keks").

For every item also give the product-type phrase: "fr" = the words of the item's "key" that name the
product type, copied exactly as they appear in the key (for example "hauts de cuisses de poulet" or
"glace"), and "hr" = the Serbian equivalent in the nominative ("pileći bataci", "sladoled").
Use empty strings when there is no clear product type (for example a brand-only name).
"""


def translations_dir(ds: DataStore) -> Path:
    return ds.root / "translations"


class Translations:
    """Translation memory + product-type phrases for one language."""

    def __init__(self, names: dict[str, str] | None = None, terms: dict[str, str] | None = None):
        self.names: dict[str, str] = dict(names or {})
        self.terms: dict[str, str] = dict(terms or {})
        self._max_words = max((len(t.split()) for t in self.terms), default=0)

    # ── files ───────────────────────────────────────────────────────────
    @classmethod
    def load(cls, ds: DataStore, lang: str = LANG) -> "Translations":
        folder = translations_dir(ds)
        return cls(_read(folder / f"{lang}.json"), _read(folder / f"{lang}-terms.json"))

    def save(self, ds: DataStore, lang: str = LANG) -> None:
        folder = translations_dir(ds)
        _write_json(folder / f"{lang}.json", dict(sorted(self.names.items())), depth=1)
        _write_json(folder / f"{lang}-terms.json", dict(sorted(self.terms.items())), depth=1)

    # ── lookups ─────────────────────────────────────────────────────────
    def lookup(self, name: str | None) -> tuple[str | None, bool]:
        """(Serbian name, approximate?)"""
        key = name_key(name)
        if not key:
            return None, False
        if key in self.names:
            return self.names[key], False
        guess = self.approximate(key)
        return (guess, True) if guess else (None, False)

    def approximate(self, key: str) -> str | None:
        words = key.split()
        hit = None
        for n in range(min(self._max_words, len(words)), 0, -1):
            for i in range(len(words) - n + 1):
                phrase = " ".join(words[i:i + n])
                if phrase in self.terms:
                    hit = (i, n, self.terms[phrase])
                    break
            if hit:
                break
        if not hit:
            return None
        i, n, head = hit
        rest = " " + " ".join(words[:i] + words[i + n:]) + " "
        extras: list[str] = []
        for fr, sr in MODIFIERS.items():
            if f" {fr} " in rest and sr not in extras and sr not in fold(head):
                extras.append(sr)
        text = head[:1].upper() + head[1:]
        return f"{text} ({', '.join(extras)})" if extras else text

    def missing(self, names: Iterable[str]) -> list[str]:
        out, seen = [], set()
        for name in names:
            key = name_key(name)
            if key and key not in self.names and key not in seen:
                seen.add(key)
                out.append(key)
        return out

    # ── updates ─────────────────────────────────────────────────────────
    def add(self, key: str, sr: str, fr_head: str = "", sr_head: str = "") -> bool:
        sr = clean_translation(sr)
        if not key or not sr:
            return False
        self.names[key] = sr
        self.add_term(key, fr_head, sr_head)
        return True

    def add_term(self, key: str, fr_head: str, sr_head: str) -> bool:
        fr_head = " ".join(fold(fr_head).split())
        sr_head = clean_translation(sr_head).lower() if sr_head else ""
        if not fr_head or not sr_head or fr_head in self.terms:
            return False
        words = fr_head.split()
        if words[0] in STOP_WORDS or words[-1] in STOP_WORDS or any(w.isdigit() for w in words):
            return False
        if f" {fr_head} " not in f" {key} ":
            return False
        self.terms[fr_head] = sr_head
        self._max_words = max(self._max_words, len(words))
        return True


def build_terms(rows: Iterable[tuple[str, str, str]]) -> dict[str, str]:
    """Majority vote over (key, fr_head, sr_head) triples."""
    votes: dict[str, Counter] = {}
    probe = Translations()
    for key, fr_head, sr_head in rows:
        probe.terms.clear()
        if probe.add_term(key, fr_head, sr_head):
            (fr, sr), = probe.terms.items()
            votes.setdefault(fr, Counter())[sr] += 1
    return {fr: c.most_common(1)[0][0] for fr, c in votes.items()}


_CYRILLIC = re.compile(r"[Ѐ-ӿ]")


def clean_translation(text: str | None) -> str:
    text = " ".join((text or "").split()).strip(" .;,")
    if not text or _CYRILLIC.search(text) or len(text) > 90:
        return ""
    return text


def _read(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in data.items() if v}


# ── automatic translation of new names (optional) ───────────────────────
API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-haiku-4-5"


def _post_json(url: str, body: dict[str, Any], headers: dict[str, str], timeout: float = 90) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"content-type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class ClaudeTranslator:
    """Translates batches of names with the Claude API (needs ANTHROPIC_API_KEY)."""

    def __init__(self, api_key: str, model: str | None = None,
                 post: Callable[[str, dict, dict], dict] = _post_json, pause: float = 1.0):
        self.api_key = api_key
        self.model = model or os.environ.get("LK_TRANSLATE_MODEL") or DEFAULT_MODEL
        self.post = post
        self.pause = pause
        self.requests = 0

    def translate(self, items: list[dict[str, str]]) -> dict[str, dict[str, str]]:
        """items: [{"id", "key", "name", "brand", "cat"}] -> {id: {"sr", "fr", "hr"}}"""
        prompt = (
            "Translate these products. Reply with one JSON object only, no prose: "
            '{"<id>": {"sr": "...", "fr": "...", "hr": "..."}, ...}\n\n'
            + json.dumps(items, ensure_ascii=False)
        )
        body = {
            "model": self.model,
            "max_tokens": 8000,
            "system": STYLE_GUIDE,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
        last: Exception | None = None
        for attempt in range(3):
            try:
                self.requests += 1
                data = self.post(API_URL, body, headers)
                text = "".join(part.get("text", "") for part in data.get("content", []) if part.get("type") == "text")
                return parse_reply(text)
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code not in (429, 500, 502, 503, 529):
                    raise
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                last = exc
            time.sleep(self.pause * (attempt + 1) * 5)
        raise RuntimeError(f"prevod nije uspeo: {last}")


def parse_reply(text: str) -> dict[str, dict[str, str]]:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("odgovor nije JSON")
    data = json.loads(text[start:end + 1])
    out = {}
    for k, v in data.items():
        if isinstance(v, str):
            v = {"sr": v}
        if isinstance(v, dict):
            out[str(k)] = {x: str(v.get(x) or "") for x in ("sr", "fr", "hr")}
    return out


def translate_missing(tr: Translations, originals: dict[str, dict[str, str]], translator,
                      limit: int = 600, batch: int = 60) -> int:
    """Translate up to `limit` keys of `originals` ({key: {"name", "brand", "cat"}}) not yet known."""
    todo = [k for k in originals if k not in tr.names][:limit]
    added = 0
    for start in range(0, len(todo), batch):
        chunk = todo[start:start + batch]
        items = [{"id": str(i), "key": key, **originals[key]} for i, key in enumerate(chunk)]
        result = translator.translate(items)
        for i, key in enumerate(chunk):
            got = result.get(str(i)) or {}
            if tr.add(key, got.get("sr", ""), got.get("fr", ""), got.get("hr", "")):
                added += 1
        if getattr(translator, "pause", 0) and start + batch < len(todo):
            time.sleep(translator.pause)
    return added


def names_to_translate(ds: DataStore) -> dict[str, dict[str, str]]:
    """Current promotions of every shop: {key: {"name", "brand", "cat"}}."""
    from .stores import STORES

    out: dict[str, dict[str, str]] = {}
    for key in STORES:
        snap = ds.read_latest(key) or {}
        for p in snap.get("promos") or []:
            k = name_key(p.get("n"))
            if k and k not in out:
                out[k] = {"name": p.get("n") or "", "brand": p.get("b") or "", "cat": p.get("c") or ""}
    return out


def run_translate(data_dir: Path, limit: int = 600) -> int:
    """CLI entry: translate new promotion names if an API key is configured."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    ds = DataStore(data_dir)
    tr = Translations.load(ds)
    originals = names_to_translate(ds)
    todo = [k for k in originals if k not in tr.names]
    if not todo:
        log.info("prevodi: svi nazivi akcija su već prevedeni")
        return 0
    if not api_key:
        log.info("prevodi: %d novih naziva, ali ANTHROPIC_API_KEY nije podešen – koristi se približan prevod",
                 len(todo))
        return 0
    translator = ClaudeTranslator(api_key)
    try:
        added = translate_missing(tr, originals, translator, limit=limit)
    finally:
        tr.save(ds)
    log.info("prevodi: dodato %d od %d novih naziva (%d zahteva, model %s)",
             added, len(todo), translator.requests, translator.model)
    return added
