# Przekazanie projektu

Instrukcja dla osoby, która przejmuje Panel Inżyniera AKPiA — i dla osoby,
która go przekazuje. Wszystko, czego nie ma w repozytorium (bo być nie może),
oraz kolejność kroków, żeby nic nie zostało po drodze.

Do samej pracy z aplikacją wystarczy [README.md](README.md).
Lista plików, których aplikacja oczekuje — [WYMAGANE_PLIKI.md](WYMAGANE_PLIKI.md).

---

## Czego NIE ma w repozytorium

To jedyne rzeczy, które trzeba przekazać **poza gitem** — mailem firmowym,
menedżerem haseł, osobiście. Nigdy commitem.

| Co | Gdzie tego szukać u dotychczasowego właściciela |
|---|---|
| `APP_PASSWORD` — hasło do panelu | lokalny plik `.env` |
| `GEMINI_API_KEY` — klucz do ścieżki AI | lokalny `.env` oraz Streamlit → *Settings* → *Secrets* |

Szablon obu zmiennych jest w [`.env.example`](.env.example) — nowa osoba
kopiuje go do `.env` i wypełnia.

**Aplikacja działa w całości bez klucza Gemini.** Bez niego nieaktywna jest
tylko ścieżka „wyciągnij urządzenia z PDF-a przez AI"; cała część
deterministyczna (bilans I/O, dobór sterownika, szafa, kable, kosztorys)
liczy się normalnie. Hasło `APP_PASSWORD` jest natomiast **wymagane** —
bez niego panel nie wystartuje.

---

## Kroki przekazania

### 1. Dostęp do repozytorium

**Współpraca (dotychczasowy właściciel zostaje):**
GitHub → repo → *Settings* → *Collaborators* → **Add people**.

**Przekazanie na stałe:**
*Settings* → *Danger Zone* → **Transfer ownership**. Przenosi repo z całą
historią, pull requestami i konfiguracją CI. Po przeniesieniu dotychczasowy
właściciel **traci dostęp**, o ile nie zostanie dodany jako współpracownik.

> Jeśli projekt ma należeć do firmy, a nie do konkretnej osoby — przenieś go
> na **konto organizacji** na GitHubie. Wtedy odejście kogokolwiek nie zabiera
> projektu ze sobą.

### 2. Klucz API — wygeneruj nowy, nie przekazuj starego

Klucz Gemini jest podpięty pod konto Google dotychczasowego właściciela i to
jego obciążają koszty zapytań. Przy przekazaniu na stałe:

1. nowa osoba generuje **własny** klucz w [Google AI Studio](https://aistudio.google.com/apikey),
2. dotychczasowy właściciel **kasuje swój** po przełączeniu.

### 3. Streamlit Community Cloud — wdrożenie nie przechodzi automatycznie

Aplikacja jest wdrożona z konta GitHub dotychczasowego właściciela. Na
Streamlit Community Cloud nie ma „przekazania aplikacji" — nowa osoba wdraża
ją u siebie:

1. dostęp do repozytorium (krok 1),
2. logowanie na [share.streamlit.io](https://share.streamlit.io) swoim GitHubem,
3. **New app** → to repozytorium → gałąź `main` → plik główny `app.py`,
4. *Settings* → *Secrets* → wklej:

   ```toml
   APP_PASSWORD = "..."
   GEMINI_API_KEY = "..."
   ```

Nowa instancja dostaje **nowy adres URL**. Stary przestanie działać, gdy
dotychczasowy właściciel skasuje swoją — uzgodnijcie moment przełączenia,
żeby nikt nie trafił w martwy link.

### 4. Dane z dotychczasowej pracy (opcjonalnie)

Te katalogi są celowo poza repozytorium, bo zawierają dane klientów:

- `historia_projektow/` — snapshoty JSON wykonanych analiz,
- `outputs/` — wygenerowane oferty (Word, Excel, PDF).

Jeśli nowa osoba ma mieć dostęp do wcześniejszych analiz, przekaż te katalogi
osobno — i sprawdź wcześniej, czy ich zawartość może trafić do tej osoby.

---

## Weryfikacja, że przekazanie się udało

Nowa osoba uruchamia u siebie:

```bash
git clone <adres-repo>
cd Panel-AKPiA
pip install -r requirements.txt
cp .env.example .env          # i wpisuje APP_PASSWORD
python -m pytest -q           # ma przejść komplet testów
streamlit run app.py
```

Potem wgrywa dowolne zestawienie urządzeń i sprawdza, czy **sekcja 9
(Kosztorys)** pokazuje kwoty, a nie „BRAK CENY". Jeśli pokazuje same braki —
patrz `cennik.csv` niżej.

---

## O czym nowa osoba musi wiedzieć

### `cennik.csv` jest w repozytorium celowo

Zawiera ceny **katalogowe**, pobrane z ogólnodostępnych źródeł. To nie są dane
handlowe firmy — rabaty, czyli jedyna faktycznie poufna rzecz, ustawia się
suwakami w panelu bocznym na czas sesji i trafiają do
`ustawienia_sesji.json`, który **jest** ignorowany przez gita.

**Nie usuwaj tego pliku z repozytorium.** Bez niego każda pozycja kosztorysu
pokazuje „BRAK CENY" i wypada z sumy — oferta wychodzi zaniżona.

`cennik_szablon.csv` to ten sam zestaw pozycji bez cen — punkt wyjścia dla
kogoś, kto chce prowadzić własny cennik.

Gdyby kiedyś trafiły tam ceny **negocjowane**, a nie katalogowe — decyzję
o widoczności repozytorium trzeba podjąć od nowa.

### Co jest zwalidowane, a co jest oszacowaniem

Najważniejsza tabela w [README.md](README.md#czemu-można-ufać-a-co-jest-szacunkiem)
rozdziela jedno od drugiego. W skrócie:

- **zwalidowane na realnych projektach wykonawczych:** dobór sterownika
  (Beckhoff CX9020 — DPK2 Wujek, Siemens ET200SP — Malbork), liczby złączek
  i przekaźników,
- **oszacowania:** rozmiar obudowy, metraż kabli, współczynnik zmiennych ASIX,
  konfiguracja S7-1500.

Aplikacja oznacza oszacowania w interfejsie. Nowa osoba powinna o tym
wiedzieć, **zanim** wyśle pierwszą ofertę do klienta.

### Gdzie zmienia się dane bez ruszania kodu

| Co | Plik |
|---|---|
| Karty i moduły sterowników | `katalogi/*.csv` |
| Ceny katalogowe | `cennik.csv` |
| Reguły typu urządzenia → sygnały | `core/device_rules.py` |
| Współczynniki złączek, katalog obudów | `core/cabinet.py` |
| Progi przekrojów kabli falownikowych | `core/cables.py` |

Rdzeń w `core/` jest **wolny od Streamlita** — da się go testować i uruchamiać
bez interfejsu. Przy każdej zmianie reguł uruchom `python -m pytest -q`;
komplet testów pilnuje m.in. tego, żeby cennik pokrywał wszystko, co dobór
potrafi zaproponować.

---

## Lista kontrolna

Dla przekazującego:

- [ ] repozytorium przekazane (współpracownik albo transfer własności)
- [ ] `APP_PASSWORD` przekazane poza gitem
- [ ] nowa osoba ma własny klucz Gemini, stary skasowany
- [ ] nowa instancja na Streamlit działa, adres przekazany zainteresowanym
- [ ] `historia_projektow/` i `outputs/` przekazane albo świadomie pominięte
- [ ] stara instancja Streamlit skasowana (dopiero po potwierdzeniu, że nowa działa)

Dla przejmującego:

- [ ] `pytest` przechodzi na świeżym klonie
- [ ] `streamlit run app.py` startuje i przyjmuje hasło
- [ ] testowa analiza przechodzi do sekcji 9 i pokazuje kwoty
- [ ] przeczytana tabela „Czemu można ufać, a co jest szacunkiem" w README
