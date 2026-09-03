# Pliki wymagane przez aplikację

Ten dokument opisuje KAŻDY plik, którego aplikacja oczekuje na dysku, żeby
działać poprawnie — gdzie ma leżeć, jaką ma mieć strukturę, co się stanie,
jeśli go zabraknie. Przydatne przy wdrażaniu na nowym komputerze/serwerze
oraz przy rozszerzaniu katalogów o nowe karty/ceny.

---

## 1. `.env` — sekrety (WYMAGANY do uruchomienia)

**Lokalizacja:** katalog główny projektu, obok `app.py`.
**Czy jest w repo:** NIE (w `.gitignore`) — tworzysz go sam z `.env.example`.

```
APP_PASSWORD=twoje_haslo_dostepu
GEMINI_API_KEY=twoj_klucz_z_google_ai_studio
```

| Zmienna | Wymagana? | Co się stanie bez niej |
|---|---|---|
| `APP_PASSWORD` | Tak | Aplikacja pokaże błąd i zatrzyma się (`st.stop()`) — bez hasła w ogóle nie odpali ekranu logowania. |
| `GEMINI_API_KEY` | Tylko do ścieżki AI | Ścieżka „Policz I/O z Excela (bez AI)" działa bez klucza. Przycisk „Ekstrahuj przez AI" pokaże błąd „Brak klucza API Gemini". |

Alternatywa dla Streamlit Cloud: te same dwie zmienne w
`.streamlit/secrets.toml` (format TOML, nie `.env`) — ustawiane przez panel
hostingu, nie plik w repo.

---

## 2. `katalogi/*.csv` — katalogi kart sterowników (WYMAGANE)

**Lokalizacja:** `katalogi/beckhoff_cx.csv`, `katalogi/beckhoff_cx7000.csv`,
`katalogi/siemens_et200sp.csv`.
**Czy jest w repo:** TAK — to dane techniczne (numery katalogowe, kanały),
nie ceny, więc bezpieczne do publikacji.
**Separator:** średnik `;`.

**Wymagane kolumny (dokładnie te nazwy w nagłówku):**

```
typ;nr_katalogowy;opis;kanaly;rola;grupa_rabatowa
```

| Kolumna | Opis | Przykład |
|---|---|---|
| `typ` | Klucz identyfikujący rolę karty w doborze. Wartości specjalne rozpoznawane przez kod: `CPU`, `DI`, `DO`, `AI`, `AO`, `LICENSE`, `SERIAL`, `ETH`, `SDCARD`, `BUSPSU`, `BUSADAPTER`, `ENDCAP`, `BASEUNIT_LIGHT`, `BASEUNIT_DARK`. Każdy typ może wystąpić **raz** na plik (to słownik, nie lista). | `DI` |
| `nr_katalogowy` | Dokładny numer producenta — to on trafia do raportów i musi zgadzać się znak w znak z `cennik.csv`, żeby cena się dopasowała. | `EL1008` |
| `opis` | Czytelny opis wyświetlany w interfejsie i dokumentach. | `8x wejście cyfrowe 24V DC` |
| `kanaly` | Liczba kanałów I/O na karcie (int). **Puste** dla pozycji systemowych/montażowych (CPU, licencja itd.) — tylko `DI`/`DO`/`AI`/`AO` muszą mieć tu liczbę. | `8` |
| `rola` | Grupa do klasyfikacji w kosztorysie: `io`, `systemowy`, `montaz`. | `io` |
| `grupa_rabatowa` | Klucz łączący pozycję z suwakiem rabatu w panelu (`BECKHOFF`, `SIEMENS`, `ASIX`, `APARATURA`, `KABLE`). | `BECKHOFF` |

**Co się stanie bez tego pliku:** `FileNotFoundError` — aplikacja się wywali
przy próbie doboru PLC dla tej platformy. To jedyny z plików danych, który
NIE ma łagodnego fallbacku (bo bez niego nie da się nic dobrać).

**Jak dodać nową platformę:** stwórz nowy plik CSV w tym formacie, dopisz
wpis w `core/plc_selector.py` do słownika `PLATFORMY = {"Nazwa": "plik.csv"}`.

---

## 3. `cennik.csv` — ceny katalogowe (OPCJONALNY, z fallbackiem)

**Lokalizacja:** katalog główny, obok `app.py`.
**Czy jest w repo:** NIE (w `.gitignore`) — zawiera realne ceny i rabaty firmy.
**Separator:** średnik `;`.

**Wymagane kolumny:**

```
Kategoria;Producent;Nr_katalogowy;Nazwa;Jednostka;Cena_Katalogowa;Waluta;Grupa_Rabatowa;Zrodlo_ceny
```

| Kolumna | Opis | Przykład |
|---|---|---|
| `Kategoria` | Informacyjna (PLC / APARATURA / KABLE / SCADA) — nieużywana w logice, tylko do porządku. | `PLC` |
| `Producent` | Informacyjny. | `Beckhoff` |
| `Nr_katalogowy` | **Klucz dopasowania.** Musi być identyczny znak w znak z `nr_katalogowy` w katalogach kart (sekcja 2) lub z numerami ASIX. | `EL1008` |
| `Nazwa` | Informacyjna, pokazywana jeśli katalog nie ma własnej nazwy. | `8x wejście cyfrowe 24V DC` |
| `Jednostka` | Informacyjna. | `szt.` |
| `Cena_Katalogowa` | **Liczba** (kropka jako separator dziesiętny, nie przecinek!). Puste pole = brak ceny, kosztorys pokaże „BRAK". | `145.00` |
| `Waluta` | Informacyjna, obecnie zawsze PLN. | `PLN` |
| `Grupa_Rabatowa` | Musi zgadzać się z `grupa_rabatowa` z katalogów kart, żeby rabat z suwaka się zastosował. | `BECKHOFF` |
| `Zrodlo_ceny` | Informacyjna notatka, skąd cena pochodzi i z kiedy. | `Mall 08/2026` |

**Co się stanie bez tego pliku:** aplikacja automatycznie wczyta
`cennik_szablon.csv` (ten sam format, bez cen — jest w repo). Kosztorys
zadziała, ale wszystkie pozycje pokażą „BRAK CENY". Aplikacja **nie wywali
się**, tylko będzie mniej użyteczna finansowo.

**Jak uzupełnić:** skopiuj `cennik_szablon.csv` jako `cennik.csv`, wypełnij
kolumnę `Cena_Katalogowa` dla pozycji, które Cię interesują. Numer
katalogowy musi zgadzać się dokładnie z katalogiem kart — spacje, wielkość
liter, myślniki mają znaczenie.

---

## 4. `assets/fonts/*.ttf` — czcionki do PDF (WYMAGANE do eksportu PDF)

**Lokalizacja:** `assets/fonts/DejaVuSans.ttf`, `assets/fonts/DejaVuSans-Bold.ttf`.
**Czy jest w repo:** TAK (licencja wolna, bezpieczne do publikacji).

Nic nie edytujesz w tych plikach — to gotowe czcionki dołączone do projektu,
żeby polskie znaki (ą, ę, ż...) poprawnie renderowały się w eksportowanym
PDF niezależnie od systemu operacyjnego.

**Co się stanie bez nich:** kod ma fallback na czcionkę `Helvetica` (wbudowaną
w reportlab), ale ta **nie ma polskich znaków** — w PDF pojawią się czarne
kwadraty zamiast liter z ogonkami. Aplikacja się nie wywali, ale PDF będzie
nieczytelny w miejscach z polskimi znakami.

---

## 5. Plik wejściowy — zestawienie urządzeń (WYMAGANY do analizy)

**Lokalizacja:** dowolna — wgrywany przez interfejs (`file_uploader`), nie
leży na stałe w projekcie.
**Format:** `.xlsx` lub `.xls`. Aplikacja czyta **pierwszy arkusz** pliku.

**Kolumny rozpoznawane po nazwie** (fragmenty dopasowywane, niewrażliwe na
wielkość liter — patrz `_COLUMN_ALIASES` w `core/parser.py`):

| Kolumna w pliku (przykłady akceptowanych nagłówków) | Do czego służy |
|---|---|
| `L.p.` / `Lp` / `Nr` | Numer porządkowy |
| `Układ` / `Obszar` / `System` | Nazwa układu technologicznego |
| `Urządzenie` / `Ozn. proj.` / `Oznaczenie` / `Tag` | Oznaczenie projektowe (np. „P1") |
| `Typ / Opis` / `Opis odbiornika` / `Nazwa` | Opis urządzenia — **kluczowa kolumna**, na jej podstawie działa fallback typu urządzenia |
| `Ilość` / `Ilosc` / `Szt` | Liczba sztuk (obsługuje „min. 10", „np. 4") |
| `Moc jedn.` / `Moc [kW]` | Moc urządzenia (obsługuje „np. 15,0") |
| `Napięcie zasil.` | Napięcie zasilające |
| `Sygnał Analogowy` / `4-20mA` | Opis sygnału analogowego (np. „Zadawanie prędkości (AO)") |
| `Sygnał Cyfrowy` / `DI/DO` | Opis sygnału cyfrowego (np. „Start, Praca, Awaria") |
| `Komunikacja` | Magistrala (Modbus, Profinet...) |
| `Pomiar?` | `lokalny` / `zdalny` — **wpływa na bilans I/O**, patrz niżej |
| `Uwagi` | Dowolny tekst |

**Kolumna `Pomiar?` (opcjonalna, ale znacząca).** Jeśli plik ją ma, wiersz
oznaczony jako `lokalny` traktowany jest jako wskaźnik czytany wzrokowo na
obiekcie (manometr, termometr tarczowy) — **nie generuje sygnału do
sterownika**, więc reguła typu urządzenia nie jest dla niego stosowana.
Wiersz zostaje na liście urządzeń (może wchodzić w zakres dostawy AKPiA
i podlegać wycenie w sekcji 1a), tylko bez I/O. Ma to znaczenie
w zestawieniach, gdzie ten sam punkt pomiarowy jest rozpisany na **parę**
wierszy `lokalny` + `zdalny` o tym samym oznaczeniu — sygnał liczy wtedy
wyłącznie `zdalny`. Wszystko inne (`zdalny`, pusta komórka, inna
konwencja) zachowuje się jak dotąd. Sygnał wpisany JAWNIE w kolumnie
sygnałów ma pierwszeństwo nawet przy `lokalny` — parser zgłasza wtedy
sprzeczność do weryfikacji.

**Żadna kolumna nie jest bezwzględnie wymagana w sensie "aplikacja się wywali"**
— ale w praktyce kolumna `opis` (Typ / Opis odbiornika) jest krytyczna: bez niej
parser nie rozpozna żadnego wiersza jako urządzenia i zwróci **0 urządzeń**
(z ostrzeżeniem "nie znaleziono kolumny opisu"). Brak kolumn sygnałów
(`analog`, `cyfrowy`) jest łagodniejszy — urządzenia się sparsują, ale ich
sygnały będą w całości wywnioskowane z reguły typu urządzenia (fallback
opisany w README, sekcja "Zasady zaszyte w kodzie"). Puste komórki w
`Ilość` domyślnie przyjmują wartość 1 (z ostrzeżeniem). Pozostałe dodatkowe
kolumny w pliku (np. `Producent`, `Dostawa`, `Napęd?`) są ignorowane — nie
przeszkadzają, ale też nie są wykorzystywane (patrz README, sekcja o
formacie OFE_381).

---

## 6. `ustawienia_sesji.json` i `nauczone_decyzje_sygnalow.json` — pamięć aplikacji (OPCJONALNE)

**Lokalizacja:** katalog główny, obok `app.py`.
**Czy jest w repo:** NIE (w `.gitignore`) — tworzone i nadpisywane automatycznie
przez aplikację, mogą odzwierciedlać rzeczywiste rabaty firmy i nazewnictwo
z realnych projektów, więc traktowane tak samo ostrożnie jak `cennik.csv`.

| Plik | Co przechowuje | Skąd |
|---|---|---|
| `ustawienia_sesji.json` | Ostatnio użyte ustawienia panelu bocznego (platforma, rezerwa, rabaty, trasa kablowa, współczynnik ASIX) | Zapisywane automatycznie przy każdym renderze panelu bocznego |
| `nauczone_decyzje_sygnalow.json` | Podpowiedzi DI/DO/AI/AO dla sekcji „1b. Rozstrzygnij sygnały BRAK DANYCH”, kluczowane treścią sygnału | Zapisywane po kliknięciu „Zastosuj” przy rozstrzyganiu sygnału |

**Co się stanie bez tych plików:** aplikacja startuje z wbudowanymi wartościami
domyślnymi (rezerwa 30%, rabaty 0%, brak podpowiedzi) — dokładnie jak wcześniej,
zanim te pliki istniały. Uszkodzony/nieczytelny JSON jest traktowany identycznie
jak brak pliku (nigdy nie wywala startu appki).

**Ważne:** to WYŁĄCZNIE podpowiedzi w UI — `nauczone_decyzje_sygnalow.json`
nigdy nie zmienia sygnału automatycznie, inżynier zawsze musi kliknąć
„Zastosuj”. Zasada „nie zgadujemy” z `core/signal_rules.py`/`core/device_rules.py`
zostaje nienaruszona.

---

## Podsumowanie — co jest krytyczne, a co opcjonalne

| Plik | Wymagany do startu appki? | Wymagany do pełnej funkcjonalności? |
|---|---|---|
| `.env` (APP_PASSWORD) | **TAK — bez tego appka się nie odpali** | — |
| `.env` (GEMINI_API_KEY) | Nie | Tak, do ścieżki AI |
| `katalogi/*.csv` | Nie przy starcie, **TAK przy doborze PLC** | Tak |
| `cennik.csv` | Nie (fallback na szablon) | Tak, do realnych cen |
| `assets/fonts/*.ttf` | Nie (fallback na Helvetica) | Tak, do czytelnego PDF |
| `ustawienia_sesji.json` / `nauczone_decyzje_sygnalow.json` | Nie (wygoda, nie funkcjonalność) | Nie |
| Plik wejściowy (Excel) | Wgrywany ręcznie przez użytkownika | — |
