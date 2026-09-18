# Lozanska korpa

Sajt koji svakog jutra preuzima cene iz prodavnica **Aldi, Migros, Coop, Lidl i Aligro** oko Lozane, pamti kako se menjaju i prikazuje:

- **Korpa** – 31 osnovna namirnica (piletina, jaja, mleko, skyr, ovsene pahuljice, pirinač…) i gde je svaka danas najjeftinija, po kilogramu, litru ili komadu, sa grafikonom kretanja cene.
- **Akcije** – sve trenutne akcije na hrani iz svih pet prodavnica, sa pretragom i filterima.
- **Istorija cena** – svaka promena cene se beleži; za svaki proizvod vidi se grafikon i periodi kada je bio na akciji.

Sve radi besplatno na GitHub-u: GitHub Actions pokreće preuzimanje svakog dana, a sajt se objavljuje na GitHub Pages (može da se otvori i sa telefona u prodavnici).

---

## Postavljanje (≈10 minuta, jednom)

Potrebno: GitHub nalog i `git` na Mac-u (ako ga nemaš, Terminal će ponuditi instalaciju *Command Line Tools* kada prvi put ukucaš `git`).

1. **Napravi repozitorijum** na <https://github.com/new>
   - ime npr. `lozanska-korpa`
   - **Public** (GitHub Pages je besplatan samo za javne repozitorijume)
   - bez README/.gitignore (prazan repozitorijum)

2. **Pošalji kod.** Svi fajlovi su već u folderu `lausanneScraper` (folder `.github` je skriven u Finder-u — prikazuje se sa ⌘ Shift .). U Terminalu:

   ```bash
   cd ~/lausanneScraper
   git init -b main
   git add .
   git commit -m "Prva verzija"
   git remote add origin https://github.com/TVOJE-KORISNICKO-IME/lozanska-korpa.git
   git push -u origin main
   ```

   Kada git traži lozinku, unesi *personal access token* (GitHub → Settings → Developer settings → Personal access tokens), ili se prethodno prijavi sa `gh auth login` ako koristiš GitHub CLI. Može i preko aplikacije GitHub Desktop.

3. **Uključi GitHub Pages:** repozitorijum → *Settings* → *Pages* → *Source*: **GitHub Actions**.
   (Workflow **Objavi sajt** se pokrenuo već pri slanju koda i verovatno je pao jer Pages još nije bio uključen — to je očekivano, sledeće pokretanje prolazi.)

4. **Dozvoli upis za Actions:** *Settings* → *Actions* → *General* → *Workflow permissions*: **Read and write permissions** → *Save*.

5. **Prvo preuzimanje:** kartica *Actions* → **Preuzmi cene** → *Run workflow*. Traje par minuta. Kada završi, automatski se pokreće **Objavi sajt**.

6. Sajt je na `https://TVOJE-KORISNICKO-IME.github.io/lozanska-korpa/`.

Posle toga se sve dešava samo, svakog jutra oko 6:40 (zimi 5:40). Svaki dan postaje jedan commit sa novim cenama u folderu `data/`.

U `data/` je već prvo preuzimanje od 16. 9. 2026, pa sajt ima podatke odmah posle prvog objavljivanja. Grafikoni postaju zanimljivi posle nekoliko dana.

> GitHub gasi zakazane workflow-ove u javnim repozitorijumima posle 60 dana bez aktivnosti. Dnevni commit-ovi to obično sprečavaju; ako se ipak ugasi, u kartici *Actions* klikni **Enable workflow**.

---

## Menjanje korpe

Sve je u fajlu [`basket.toml`](basket.toml). Primer novog proizvoda:

```toml
[[item]]
id = "piletina-cela"          # ne menjaj kasnije — na njega je vezana istorija
name = "Piletina (cela)"
group = "Meso i riba"
unit = "kg"                   # kg, l ili pc (komad)
query = "poulet entier"       # pretraga na francuskom (Migros i Coop)
include = ["poulet", "entier"]            # naziv mora da sadrži sve ovo
exclude = "roti|epice|marine|chat|chien"  # …i ništa od ovoga
min_qty = 0.8                 # preskoči pakovanja manja od 0,8 kg
```

Pravila ne razlikuju velika i mala slova i ignorišu akcente (é = e). Za svaku prodavnicu se bira proizvod sa **najnižom cenom po jedinici**. Posle izmene pokreni testove (`python -m pytest -q`) i pošalji izmenu (`git commit` + `git push`); sledeće jutarnje preuzimanje će je koristiti.

Da proveriš šta će pravilo uhvatiti pre nego što pošalješ izmenu, pokreni preuzimanje lokalno (dole) i pogledaj sajt.

---

## Pokretanje na Mac-u

Dovoljan je Python 3.9 ili noviji (`python3 --version`; onaj koji dolazi sa macOS *Command Line Tools* radi).

```bash
cd ~/lausanneScraper
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m scraper check          # brza provera: da li su sve prodavnice dostupne
python -m scraper all            # preuzmi cene i napravi podatke za sajt
python -m http.server -d site 8000
```

Sajt je tada na <http://localhost:8000>. Korisne opcije:

- `python -m scraper run --stores migros,coop` – samo neke prodavnice
- `python -m scraper run --record snimci/` – sačuvaj sve odgovore (za proveru ili testove)
- `python -m scraper run --replay snimci/*.json` – ponovi preuzimanje iz snimaka, bez interneta

### Ako neka prodavnica blokira GitHub

Prodavnice ponekad odbijaju zahteve sa servera (to se najčešće dešava sa Coop-om). Na stranici **O sajtu** se tada vidi „greška“ za tu prodavnicu, a ostale rade normalno. Ako su cene neke prodavnice starije od 3 dana, u korpi su prikazane bledo i ne ulaze u poređenje. Rešenje je da tu prodavnicu povremeno preuzmeš sa svog Mac-a (kućna mreža se ne blokira):

```bash
git pull
python -m scraper run --stores coop
git add data && git commit -m "Coop sa Mac-a" && git push
```

Kada pošalješ izmene, sajt se automatski ponovo objavljuje.

---

## Kako radi

| Prodavnica | Izvor | Šta dobijamo |
|---|---|---|
| **Aldi** | javni API sajta aldi-suisse.ch (filijala Chavannes-près-Renens) | ceo asortiman (~2300 artikala) i sniženja; nema svežeg voća i povrća |
| **Migros** | API sajta migros.ch, region **Migros Vaud** | pretraga za svaki proizvod iz korpe + nedeljne akcije |
| **Coop** | API sajta coop.ch | pretraga za korpu + akcije na hrani |
| **Lidl** | digitalni letak (Lidl Plus) | samo artikli iz letka ove i sledeće nedelje |
| **Aligro** | aligro.ch, tržnica Chavannes | samo artikli na akciji (redovne cene su dostupne samo registrovanim kupcima) |

- Akcije „uz uslov“ (npr. *dès 2*, *Lidl Plus*, *online*) se prikazuju, ali se u poređenju korpe računa redovna cena.
- Proizvodi koji se prodaju na težinu (meso, voće) imaju približnu cenu po kg.
- Kod Lidl-a i Aligro-a proizvod iz korpe ima cenu samo dok je na akciji.

### Podaci (`data/`)

- `prices/<prodavnica>.csv` – dnevnik promena cena (red se dodaje samo kad se cena promeni)
- `products/<prodavnica>.json` – nazivi, pakovanja, linkovi i periodi kada je proizvod viđen (`"sp": [["2026-09-16", null]]`; `null` znači „i dalje se viđa“, pa se fajl menja samo kad se nešto stvarno promeni)
- `basket.csv` – najjeftinija cena za svaki proizvod iz korpe, po danu i prodavnici
- `latest/<prodavnica>.json` – poslednje preuzimanje (akcije i kandidati za korpu)
- `state.json` – stanje poslednjih preuzimanja

`python -m scraper build` od toga pravi JSON fajlove za sajt u `site/data/` (to radi i workflow **Objavi sajt**).

### Kod

```
scraper/
  stores/      adapteri za svaku prodavnicu (migros.py, coop.py, aldi.py, lidl.py, aligro.py)
  units.py     čitanje pakovanja („6 x 1l“, „env. 600 g“, „Le kg“…) i cene po jedinici
  basket.py    učitavanje basket.toml i izbor najjeftinijeg proizvoda
  storage.py   čuvanje u data/
  run.py       jedno preuzimanje (prodavnice rade nezavisno — greška u jednoj ne zaustavlja ostale)
  build.py     podaci za sajt
site/          statički sajt (HTML, CSS, JavaScript, bez biblioteka)
tests/         testovi na snimljenim odgovorima prodavnica
```

---

## Napomena

Lični projekat, nije povezan ni sa jednom od prodavnica. Koristi javno dostupne podatke sa njihovih sajtova, jednom dnevno i sa pauzama između zahteva. API-ji prodavnica nisu zvanični i mogu da se promene bez najave — ako neka prodavnica prestane da radi, na stranici **O sajtu** će pisati greška, a popravka je obično u odgovarajućem fajlu u `scraper/stores/`.
