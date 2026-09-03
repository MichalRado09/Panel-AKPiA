# Panel Inżyniera AKPiA — Krok 1: Rdzeń deterministyczny

Czysty rozdział AI ↔ Python. AI ekstrahuje surową listę urządzeń;
cały dobór i zliczanie robi Python w `core/`. Zero zależności od Streamlit
i od API — moduły uruchamiasz i testujesz samodzielnie.

## Struktura

```
core/
  signal_rules.py   # słownik DI/DO/AI/AO (Start→DO, Awaria→DI, jawny (AO) ma priorytet)
  device_rules.py   # słownik typów urządzeń → domyślne sygnały (gdy kolumny puste)
  parser.py         # parser prostego formatu, mapuje kolumny PO NAZWIE, obsługuje brudy
  io_counter.py     # zliczanie I/O + rezerwa (math.ceil, zawsze w górę)
tests/
  test_core.py      # 16 testów jednostkowych
asix_cennik.csv     # realny fragment cennika ASIX (z Info handlowe 3/2026)
```

## Jak uruchomić

```bash
# Parser na Twoim pliku:
python -m core.parser "Zestawienie_aparatury_i_urządzeń.xlsx"

# Bilans I/O (arg2 = rezerwa %, arg3 = numer arkusza):
python -m core.io_counter "Zestawienie_aparatury_i_urządzeń.xlsx" 30 0

# Testy:
python -m pytest tests/ -v        # jeśli masz pytest
```

## Zasady zaszyte w kodzie (audytowalne)

1. **Sygnał jawny w kolumnie > reguła typu urządzenia.** Reguła typu włącza się
   tylko, gdy kolumny sygnałów są puste. Każdy taki sygnał ma `source="typ_urzadzenia"`
   i trafia do ostrzeżeń „do weryfikacji".
2. **Nie zgadujemy.** Sygnał, którego słownik nie rozpoznał → `BRAK DANYCH`,
   liczony osobno, NIE wchodzi do DI/DO/AI/AO.
3. **Rezerwa zawsze w górę:** `ceil(baza * (1 + r/100))`, osobno na typ.
4. **Kolumny mapowane po nazwie** — odporność na przesunięcia i różnice nagłówków.

## Wynik na realnym pliku (arkusz „Sheet1”, rezerwa 30%)

| Typ | Baza | +Rezerwa |
|-----|------|----------|
| DI  | 10   | 13       |
| DO  | 15   | 20       |
| AI  | 34   | 45       |
| AO  | 11   | 15       |

Źródło: 51 z kolumn, 19 z reguły typu urządzenia, 1 pozycja BRAK DANYCH
(impuls energii ciepłomierzy — do decyzji inżyniera).

## Integracja z app.py — ZROBIONE (Krok 1b)

`app.py` przebudowany. AI już NIE generuje BOM/doboru. Nowy przepływ:

1. **AI zwraca tylko JSON** z listą urządzeń (`core/ai_contract.py` — nowy prompt + parser odpowiedzi).
2. `parse_ai_devices()` (JSON) lub `parse_devices()` (Excel) → lista Device.
3. `count_io(devices, reserve)` → bilans I/O.
4. **Sekcja HITL**: tabela urządzeń z kolumną „Źródło" (kolumna vs typ_urzadzenia),
   metryki bilansu I/O, ostrzeżenia BRAK DANYCH — do weryfikacji przez inżyniera.
5. Pobranie: raport Word (bilans I/O + uwagi) i Excel (2 zakładki: urządzenia, bilans).
6. Historia zapisywana jako **snapshot JSON** (audytowalny, odtwarzalny).

Dwie ścieżki w UI:
- **„Policz I/O z Excela (bez AI)"** — parsuje arkusz bezpośrednio rdzeniem, działa offline bez klucza API.
- **„Ekstrahuj przez AI"** — dla PDF/mieszanych źródeł; AI robi tylko ekstrakcję, rdzeń liczy.

### Uruchomienie aplikacji
```bash
pip install streamlit pandas openpyxl python-docx google-genai python-dotenv
streamlit run app.py
```

## Krok 2 — dobór PLC Beckhoff (ZROBIONE)

`core/plc_beckhoff.py` — dobór sterownika na bilansie I/O (po rezerwie).
Reguły z realnego projektu DPK2 Wujek. Karty: EL1808(8DI), EL2008(8DO),
EL3058(8AI), EL4024(4AO) + CX9020-0115, EL6070-0033, EL6021, EL9410, EL9011.

**Walidacja referencyjna:** dobór na I/O z Wujka (80/24/56/16) odtwarza realną
listwę co do sztuki: 10×EL1808, 3×EL2008, 7×EL3058, 4×EL4024. Test w `tests/`.

Wpięte w app.py: sekcja „Dobór sterownika" w wynikach, tabela w Word,
zakładka „Sterownik PLC" w Excelu (z grupą rabatową pod moduł budżetowania).

## Model AI (zaktualizowany)

`GEMINI_MODEL = "gemini-3.5-flash"` (poprzedni `-thinking-exp` był przestarzały).
Ekstrakcja wymusza **tryb JSON ze schematem**: `response_mime_type=application/json`
+ `response_schema` (`build_response_schema()`). Wg dokumentacji Gemini to jedyny
sposób na gwarancję poprawnego składniowo JSON. Do zmiany na `gemini-3.1-pro`
wystarczy podmienić stałą, jeśli potrzebujesz mocniejszego rozumowania.

## Test aplikacji (przeprowadzony)

Pełny przepływ na realnym pliku (arkusz RTO): wczytanie → parse → bilans
(70→93) → dobór PLC → tabela → Word (37KB) → Excel (3 zakładki) → zapis snapshotu.
Wszystkie kroki przeszły. 24/24 testów jednostkowych PASS.



---
_Poprzednia treść (Krok 1) niżej._



## Krok 3 — dwie platformy PLC + katalog CSV (ZROBIONE)

Dobór PLC przeniesiony na uniwersalny silnik `core/plc_selector.py` czytający
katalog kart z plików CSV w `katalogi/`:
- `katalogi/beckhoff_cx.csv` — karty EL (8-kan cyfrowe, walidacja: Wujek)
- `katalogi/siemens_et200sp.csv` — moduły ET200SP (16-kan cyfrowe, walidacja: Eco Malbork)

**Dodanie platformy/karty = edycja CSV, BEZ zmian w kodzie.** To realizuje
uniwersalność: każdy przyszły projekt liczony automatycznie, nowe karty dopisujesz
w arkuszu.

Reguła doboru (wspólna): `liczba modułów = ceil(kanały_po_rezerwie / kanały_na_moduł)`.
Rezerwa WYŁĄCZNIE z suwaka % (nie dokładamy modułów). Przy 30% dobór Siemensa
odtwarza realny projekt Malbork co do modułu (3 DI / 2 DO / 2 AI / 2 AO).

**Wybór platformy** w panelu bocznym (Beckhoff CX / Siemens ET200SP). Raport Word
i Excel automatycznie pod wybraną platformę.

Uwaga do BaseUnit (ET200SP): reguła uproszczona (1 jasny + reszta ciemne).
Realny podział na grupy potencjałowe (więcej jasnych) to głębsza wiedza projektowa
— suma podstawek jest poprawna, podział jasny/ciemny do ewentualnego dopracowania.

Format wejściowy: TYLKO Excel (.xlsx/.xls). CSV świadomie nieobsługiwany.

Model AI: gemini-3.5-flash. Testów jednostkowych: 29/29 PASS.

## Rozszerzone testy — walidacja na dodatkowych plikach

**Ważna korekta metodologiczna:** wcześniejsze liczenie złączek w BOM-ach
(przez `uniq -c` na wierszach tekstu) było błędne — każdy wiersz BOM ma
kolumnę ilości (np. "64 szt PT 4-HESI"), a nie zawsze jest to "1 szt" x64
wierszy. Po poprawce regexu na sumowanie kolumny ilości, dane z Wujka
pozostały bez zmian, ale ujawniły się poprawne dane z DPK1 Niwka.

**Walidacja szafy na DRUGIM, niezależnym projekcie (DPK1 Niwka):**
Reguły wyprowadzone z Wujka przetestowano na zupełnie innym BOM (DPK1).
Błąd rośnie z 0-12% (Wujek, źródło reguł) do 7-33% (DPK1, walidacja
niezależna) — to normalne i oczekiwane przy generalizacji reguły z jednego
przykładu. Testy dokumentują rzeczywisty zakres niepewności, nie ukrywają go.

**OFE_381 — format z kolumnami Napęd?/Pomiar?:**
- Parser poprawnie wczytuje plik mimo dodatkowych kolumn (mapowanie po nazwie działa).
- ODKRYCIE: w całym pliku (124 wiersze) kolumny sygnałów są w 100% puste —
  wszystkie sygnały pochodzą z reguły typu urządzenia. Każda pozycja wymaga
  weryfikacji inżyniera.
- ZNANA GRANICA: kolumna "Pomiar? lokalny/zdalny" (39 par w pełnym pliku)
  nie jest rozpoznawana przez prosty format — "lokalny" (wskaźnik na
  obiekcie, bez transmisji do PLC) liczy się tak samo jak "zdalny" (sygnał
  AI). To może zawyżać bilans AI dla tego typu plików. Udokumentowane
  świadomie, zgodnie z wcześniejszą decyzją o prostym formacie wejściowym.

Testy używają lekkiej próbki `tests/fixtures/ofe381_sample.xlsx` (14 wierszy
wyciętych z realnego pliku), więc działają niezależnie od dostępu do
oryginalnej dokumentacji projektowej — **46 testów, wszystkie przechodzą
w pełnej izolacji**.

## Publikacja jako open source / hosting na Streamlit Cloud

**Przed pierwszym `git init` / `git push` przeczytaj to w całości.**

### Co jest chronione przez `.gitignore` (i dlaczego)

| Plik/folder | Dlaczego wrażliwy |
|---|---|
| `.env` | Klucz API Gemini i hasło dostępu do panelu |
| `cennik.csv` | Realne ceny katalogowe i rabaty firmowe — dane handlowe |
| `historia_projektow/`, `outputs/` | Mogą zawierać dane konkretnych klientów/projektów |

Do repo trafia **`cennik_szablon.csv`** — te same 62 pozycje, ale bez cen.
Aplikacja startuje z nim od razu po sklonowaniu (kosztorys pokaże "BRAK CENY"),
a każdy, kto ją wdraża u siebie, uzupełnia własny `cennik.csv` lokalnie.

### Checklist przed `git push` do publicznego repo

1. **Sprawdź, co git widzi jako nowe pliki:**
   ```bash
   git status
   ```
   Upewnij się, że NIE ma na liście: `.env`, `cennik.csv`, plików w
   `historia_projektow/` ani `outputs/` (poza `.gitkeep`).

2. **Jeśli używasz `git add .`, zweryfikuj przed commitem:**
   ```bash
   git add .
   git status   # jeszcze raz - co faktycznie trafi do commita
   ```

3. **Skopiuj `.env.example` do `.env` i uzupełnij WŁASNYMI wartościami**
   (ten krok jest lokalny, `.env` nigdy nie trafia do repo):
   ```bash
   cp .env.example .env
   # edytuj .env: APP_PASSWORD=..., GEMINI_API_KEY=...
   ```

4. **Na Streamlit Cloud (albo innym hostingu)** klucz API i hasło ustawia się
   przez panel sekretów hostingu (np. Streamlit Cloud → Settings → Secrets),
   NIE przez wgranie pliku `.env` do repo. Format w Streamlit Cloud to
   `.streamlit/secrets.toml` — też jest w `.gitignore`, ustawiasz go
   bezpośrednio w panelu hostingu, nie commitujesz.

### Jeśli coś wrażliwego już trafiło do repo

Samo dopisanie do `.gitignore` NIE usuwa plików już zacommitowanych —
zostają w historii git, nawet jeśli usuniesz je w nowym commicie. Jeśli
`cennik.csv` z cenami albo `.env` trafiły kiedyś do commita (szczególnie
jeśli repo było już push'nięte publicznie):
```bash
git rm --cached cennik.csv
git commit -m "Usunięcie danych wrażliwych z repo"
```
To zatrzymuje śledzenie na przyszłość, ale **nie czyści historii**. Jeśli
repo było już publiczne, traktuj klucz API jako ujawniony — wygeneruj nowy
w Google AI Studio. Do trwałego czyszczenia historii służy `git filter-repo`
(zaawansowane, nieodwracalne — nie rób tego bez pewności co robisz).

## Korekty wg odpowiedzi przełożonego (po urlopie)

**Klasyfikacja sygnałów — poprawka zaworu regulacyjnego:**
Poprzednia reguła (AO + 2×DI) pomijała sprzężenie zwrotne pozycji.
Poprawiono wg wytycznej: **AO + AI + opcjonalnie 2×DI**. Zawór regulacyjny
ma zarówno sterowanie (AO), jak i sygnał zwrotny rzeczywistej pozycji
siłownika (AI) — to różni go od zaworu ON/OFF (tylko DO+2DI, bez analogów).
Pozostałe reguły (pompa z falownikiem, zawór odcinający, przetwornik,
przepływomierz) potwierdzone jako zgodne z praktyką firmy.

**S7-1200:** NIE dodany do listy platform — przełożony szuka realnego
projektu jako wzorca, decyzja odłożona do czasu, aż się znajdzie.

**Reguły złączek w szafie:** potwierdzone jako wystarczające przybliżenie
("zostawmy 1 na 1") — świadoma akceptacja uproszczenia.

**HMI — nowy moduł, osobny od SCADA (`core/hmi.py`):**
Zgodnie z wytyczną "HMI osobno, ASIX osobno" — dodano sekcję 6 w interfejsie
z manualnym dodawaniem paneli operatorskich (model + ilość + lokalizacja).
Wybór manualny, nie automatyczny dobór — jedyny znaleziony przykład HMI
(Malbork) był dostarczany przez producenta kotła, brak zwalidowanego
wzorca firmy do naśladowania (podobna sytuacja jak S7-1200). HMI trafia
do raportu Word (tabela) i zakładki "HMI" w Excelu, wliczane do kosztorysu.

Interfejs ma teraz **11 sekcji**, Excel **9 zakładek**. **56 testów, wszystkie
przechodzą.**

## Wybór arkusza przy wgrywaniu Excela

Naprawiono realne źródło pomyłki: pliki z wieloma arkuszami (np.
`Zestawienie_aparatury_i_urządzeń.xlsx` ma "Sheet1 (2)" i "Sheet1") wcześniej
były czytane po cichu z pierwszego arkusza — łatwo było pomylić, który zestaw
danych faktycznie się analizuje.

Teraz: jeśli wgrany plik ma więcej niż jeden arkusz, pojawia się selectbox
"Arkusz do wczytania" z pełną listą zakładek. Nazwa wybranego arkusza trafia
też do etykiety projektu (nazwy plików wynikowych) — dwa raporty z tego
samego pliku, ale różnych zakładek, nie będą już miały identycznej nazwy.

Zweryfikowano na realnym pliku: arkusz domyślny (pierwszy) daje bilans
DI=8/DO=4/AI=24/AO=10 (po rezerwie 30%), arkusz "Sheet1" (drugi) daje
DI=13/DO=20/AI=45/AO=15 — oba poprawnie dostępne przez wybór w interfejsie.

## Wykryty i naprawiony problem: podwójne liczenie w zestawieniach hierarchicznych

**Kontekst:** test porównawczy bez-AI vs z-AI na tym samym pliku dał znacząco
różne bilanse (70 vs 48 sygnałów I/O). Wstępna diagnoza ("AI gubi wiersze")
okazała się BŁĘDNA po dokładnej analizie danych źródłowych.

**Rzeczywista przyczyna:** niektóre zestawienia mają strukturę hierarchiczną —
wiersz z numerem L.p. i jawnie podaną Ilość > 1 (np. "TI (różne)" Ilość=12)
to już ZBIORCZY licznik, a sąsiednie wiersze bez L.p. o tym samym temacie
(np. kolejne "Przetwornik temperatury") to WYPISANE Z NAZWY EGZEMPLARZE tego
licznika — nie dodatkowe, osobne urządzenia. Dowód: liczba nazwanych
"Przetwornik ciśnienia PI" (3) zgadzała się DOKŁADNIE ze zbiorczym licznikiem
"PI (różne)" Ilość=3.

Parser liczący każdy wiersz niezależnie (zarówno ścieżka bez AI, jak
pierwotnie i AI po błędnej korekcie promptu) liczy takie urządzenia PODWÓJNIE.

**Rozwiązanie (świadomie ostrożne — bez cichego zgadywania):**
- `core/parser.py`: nowa funkcja `_detect_possible_double_counting()` wykrywa
  ten wzorzec (agregat + ≥2 tematycznie podobne bezimienne wiersze) i dodaje
  JAWNE ostrzeżenie w interfejsie — NIE zmienia cicho matematyki liczenia,
  bo to wymaga potwierdzenia konwencji danego pliku przez inżyniera (podobnie
  jak wcześniejsze przypadki "pomiar"/lokalny-zdalny).
- `core/ai_contract.py`: złagodzono wcześniejszą (błędną) instrukcję "nigdy
  nie pomijaj wierszy bez L.p." na nuansowaną zasadę rozróżniającą oba
  scenariusze, z bezpieczną domyślną interpretacją (nie podwajaj przy
  niepewności) i wymogiem zaznaczenia niejednoznaczności w polu "uwagi".

Zweryfikowano brak fałszywych alarmów na 3 innych plikach/arkuszach bez tego
wzorca (wzorcowy plik testowy, pierwszy arkusz tego samego Excela, próbka
OFE_381). **60 testów, wszystkie przechodzą.**

**Lekcja:** pierwsza diagnoza rozbieżności AI vs bez-AI była pochopna —
założono, że deterministyczny parser jest "prawdą odniesienia", a AI się myli.
Głębsza analiza danych pokazała odwrotnie: to parser miał lukę (brak
rozpoznawania agregatów), a rozbieżność ujawniła realny problem w danych
wejściowych, nie w ekstrakcji AI.

## Naprawa: automatyczna deduplikacja hierarchicznych zestawień

Wcześniejsze wykrywanie (samo ostrzeganie) zastąpiono **faktyczną deduplikacją**
w `core/parser.py` — funkcja `_deduplicate_hierarchical_aggregates()`:

- Wykrywa wiersz zbiorczy (L.p. + jawna Ilość>1) i tematycznie podobne wiersze
  bezimienne (bez L.p. ani własnego oznaczenia) — usuwa te drugie z liczenia,
  zostawiając jeden, autorytatywny licznik zbiorczy.
- **Kody obszaru instalacji rozstrzygają nad ogólnymi słowami.** Wykryto i
  naprawiono realny błąd: "Siłownik 01PCB10 AA401" (grupa D1-D4) błędnie
  wchłonął "Siłownik 01PCB40 AA401" (INNY obszar instalacji) przez wspólne
  ogólne słowa "siłownik"/"napędem" i przypadkowo powtórzony numer tagu.
  Teraz kod obszaru (wzorzec: cyfry+litery+cyfry, np. "01pcb10") musi się
  zgadzać dokładnie, inaczej urządzenia NIE są łączone.
- **Wiersze z własnym oznaczeniem projektowym nigdy nie są usuwane** — np.
  "01PCB20 AT001" ma unikalny tag, więc to konkretnie zidentyfikowany
  przyrząd, nie bezimienny duplikat, nawet jeśli tematycznie pasuje.
- Pełny audyt: usunięcie zawsze generuje ostrzeżenie z listą wykluczonych
  opisów ORAZ dopisek w polu "uwagi" pozycji zbiorczej — nic nie znika
  po cichu, inżynier zawsze widzi, co i dlaczego wykluczono.

**Wynik na realnym pliku (Wujek, Sheet1):** bilans bazowy spadł z zawyżonych
70 do 56 (bliżej niezależnie liczonej ścieżki AI: 48) — 30 urządzeń
zredukowano do 16 po usunięciu 14 zduplikowanych wierszy w 3 grupach.

Zweryfikowano brak fałszywych alarmów na 3 innych plikach bez tego wzorca.
**63 testy, wszystkie przechodzą**, w tym 2 nowe testy regresyjne chroniące
przed dokładnie tym błędem (mieszanie obszarów instalacji, usuwanie
oznaczonych przyrządów).

## Audyt reguł: dwa błędy zawyżające ofertę + zabezpieczenie środowiska

Przegląd modułów, które wcześniej nie były audytowane pod kątem samych
reguł (nie tylko przepływu). Oba znalezione błędy działały **po cichu** —
nic się nie wywalało, po prostu liczby w ofercie były złe.

**1. Marker `ma` dopasowywany jako dowolny podciąg (`core/signal_rules.py`).**
Heurystyka sygnału analogowego szukała w tekście `"ma"` (od miliamperów),
ale robiła to jako zwykły podciąg — więc trafiała w przypadkowe słowa
i po cichu klasyfikowała je jako AI. Zmierzone na realistycznych frazach:
„Auto**ma**tyczna regulacja", „Sygnał z **ma**gistrali", „Nor**ma**lny tryb",
„Infor**ma**cja z szafy", a najgorzej: „**Ma**nometr wskazujący" — czyli
lokalny wskaźnik BEZ sygnału do sterownika — oraz „Nie **ma** sygnału",
które znaczy dokładnie coś przeciwnego. Każde takie trafienie zawyżało
bilans AI, a przez to liczbę kart analogowych i kosztorys.
Naprawa: `mA`/`mV` rozpoznawane wyłącznie jako JEDNOSTKA (wymagana cyfra
przed: `4-20mA`, `20 mV`). Reszta wraca do BRAK DANYCH — zgodnie
z nadrzędną zasadą modułu, że czego nie rozpoznajemy, tego nie zgadujemy.

**2. Fałszywy czerwony BŁĄD dla projektów z falownikami (`core/validator.py`).**
Projekt złożony z pomp z falownikiem (bardzo typowy w AKPiA) dostawał
w sekcji 10 czerwony błąd „brak kabla ekranowanego", mimo że kabel
ekranowany BYŁ na liście. Przyczyna: `core/cables.py` świadomie scala
urządzenie AO+DO w jedną pozycję „FALOWNIK" (kabel BiTservo, ekranowany,
niesie sterowanie AO), a walidator szukał wyłącznie pozycji typu AI/AO.
Inżynier był więc wysyłany za błędem, którego nie było.

**Zabezpieczenie środowiska (nie zmienia logiki, chroni przed powtórką):**
- `requirements.txt` ma teraz **przypięte wersje**. Bez nich świeża
  instalacja brała najnowsze biblioteki z dnia wdrożenia — dokładnie tak
  powstał wcześniejszy crash startowy po zmianie zachowania `st.secrets`
  w nowszym Streamlit, bez żadnej zmiany w naszym kodzie.
- `use_container_width` (22 wywołania) zamienione na `width="stretch"` /
  `width="content"` — stary parametr jest w Streamlit oznaczony jako
  przestarzały i zapowiedziany do usunięcia.
- **CI (`.github/workflows/testy.yml`)** uruchamia pełny pakiet testów
  przy każdym pushu i pull requeście, plus sprawdza, czy `app.py` się
  importuje (testy `core/` celowo nie zależą od Streamlit, więc same
  tego nie złapią).

**108 testów, wszystkie przechodzą**, w tym 3 nowe regresyjne pilnujące
obu powyższych błędów. Zweryfikowano też ręcznym przebiegiem aplikacji
(upload → bilans → dobór → walidacja → raporty).
