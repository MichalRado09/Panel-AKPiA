"""
core/budget.py
==============
Moduł budżetowania: ceny katalogowe, rabaty firmowe, ceny netto.

Zasady:
- Ceny katalogowe z pliku cennikowego (CSV) lub z danych doboru PLC.
- Rabaty jako POJEDYNCZY % na grupę rabatową (BECKHOFF, SIEMENS, ASIX, APARATURA, KABLE).
  Inżynier wpisuje je ręcznie w panelu bocznym.
- Cena netto = cena_katalogowa * (1 - rabat/100), zaokrąglona do 2 miejsc na poziomie pozycji.
- Suma liczona z zaokrąglonych pozycji (tak robi księgowość).

Cennik PLC nie jest tu zaszywany — ceny kart Beckhoff/Siemens są specyficzne
dla umowy dystrybucyjnej firmy i muszą być wprowadzone do pliku cennikowego
lub przyjdą z odpowiedzi opiekuna. Na razie moduł obsługuje:
  - pozycje ASIX (cennik gotowy, wyciągnięty z dokumentacji handlowej),
  - pozycje PLC (bez cen katalogowych, do uzupełnienia — sygnalizowane jako "BRAK CENY").
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field

CENNIK_DIR = os.path.dirname(os.path.dirname(__file__))

# Domyślne grupy rabatowe i wartości startowe (0% = brak rabatu)
GRUPY_RABATOWE = {
    "BECKHOFF": 0,
    "SIEMENS": 0,
    "ASIX": 0,
    "APARATURA": 0,
    "OBUDOWY": 0,           # obudowa rozdzielnicy, korytka, szyny - inny
                            # dostawca niż aparatura, więc i inny rabat
    "KABLE": 0,
    "AKPIA_URZADZENIA": 0,  # przetworniki i inne urządzenia obiektowe
                            # wycenione ręcznie przez inżyniera - patrz device_budget.py
}


@dataclass
class BudgetItem:
    """Jedna pozycja kosztorysu."""
    kategoria: str
    nr_katalogowy: str
    nazwa: str
    ilosc: int
    jednostka: str = "szt."
    cena_katalogowa: float | None = None   # None = brak ceny w cenniku
    grupa_rabatowa: str = ""
    rabat_pct: float = 0.0
    cena_netto_jed: float | None = None    # po rabacie, za sztukę
    wartosc_netto: float | None = None     # cena_netto_jed * ilosc


@dataclass
class Budget:
    """Pełny kosztorys z podsumowaniem."""
    items: list[BudgetItem] = field(default_factory=list)
    rabaty: dict[str, float] = field(default_factory=dict)

    @property
    def suma_katalogowa(self) -> float:
        return sum(
            (it.cena_katalogowa or 0) * it.ilosc
            for it in self.items
        )

    @property
    def suma_netto(self) -> float:
        return sum(it.wartosc_netto or 0 for it in self.items)

    @property
    def brak_ceny(self) -> list[BudgetItem]:
        """Pozycje bez ceny katalogowej — do uzupełnienia."""
        return [it for it in self.items if it.cena_katalogowa is None]


# Cache wczytanego cennika, kluczowany (ścieżka, mtime pliku) - Streamlit
# przelicza cały skrypt przy KAŻDEJ interakcji UI (suwak, checkbox...), a
# calculate_budget()/select_asix()/build_device_budget() wczytują cennik
# za każdym razem od nowa, więc bez cache jeden rerender = kilka odczytów
# tego samego pliku z dysku. Klucz po mtime (nie tylko ścieżce) gwarantuje,
# że edycja cennik.csv w trakcie sesji jest widoczna od razu, bez potrzeby
# restartu aplikacji - nowy mtime to inny klucz cache, stare wpisy dla tej
# samej ścieżki są usuwane. Celowo bez st.cache_data: core/ ma zostać wolne
# od zależności od Streamlit (patrz README) - to jest zwykły cache w pamięci
# modułu, działa identycznie w testach i w CLI.
_cennik_cache: dict[tuple[str, float], dict[str, dict]] = {}


def _parse_cena(raw) -> float | None:
    """
    Cena z komórki CSV. Pusta komórka albo śmieć -> None ("BRAK CENY").

    PRZYJMUJE POLSKI ZAPIS LICZBY, i to nie jest kosmetyka. Cennik uzupełnia
    inżynier ręcznie, zwykle kopiując z oferty dostawcy albo z Excela, gdzie
    separatorem dziesiętnym jest PRZECINEK, a tysiące bywają rozdzielone
    spacją ("1 234,50"). Poprzednia wersja robiła na tym gołe float(), łapała
    ValueError i po cichu ustawiała None - czyli wpisana cena znikała bez
    jednego słowa wyjaśnienia, a pozycja pokazywała "BRAK CENY" i wypadała
    z sumy. Dokładnie ten rodzaj cichego zaniżenia oferty, którego ta
    aplikacja ma pilnować.

    Spacje (także niełamliwe, wklejane z Excela i stron WWW) są usuwane,
    przecinek zamieniany na kropkę.
    """
    if raw is None:
        return None
    s = str(raw).replace(" ", "").replace(" ", "").strip()
    if not s:
        return None
    s = s.replace(",", ".")
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def load_cennik(filename: str = "cennik.csv") -> dict[str, dict]:
    """
    Wczytuje cennik z CSV. Zwraca dict: nr_katalogowy -> {nazwa, cena, waluta, grupa}.

    Jeśli `cennik.csv` (plik z realnymi cenami, poza repo — patrz .gitignore)
    nie istnieje, próbuje `cennik_szablon.csv` (bez cen, w repo) — dzięki temu
    aplikacja startuje od razu po sklonowaniu, tylko bez cen katalogowych
    (kosztorys pokaże "BRAK CENY" zamiast się wywalić).
    """
    path = os.path.join(CENNIK_DIR, filename)
    if not os.path.exists(path):
        # Fallback: szablon bez cen (bezpieczny, w repo)
        template = os.path.join(CENNIK_DIR, "cennik_szablon.csv")
        if os.path.exists(template):
            path = template
        else:
            return {}

    cache_key = (path, os.path.getmtime(path))
    cached = _cennik_cache.get(cache_key)
    if cached is not None:
        return cached

    cennik: dict[str, dict] = {}
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            nr = row.get("Nr_katalogowy", "").strip()
            if not nr:
                continue
            cena = _parse_cena(row.get("Cena_Katalogowa"))
            cennik[nr] = {
                "nazwa": row.get("Nazwa", "").strip(),
                "cena": cena,
                "waluta": row.get("Waluta", "PLN").strip(),
                "grupa": row.get("Grupa_Rabatowa", "").strip(),
            }

    # Usuń tylko przestarzałe wpisy DLA TEJ SAMEJ ścieżki (inny mtime = plik
    # edytowany w międzyczasie) - nie cały cache, żeby nie kasować wpisu dla
    # drugiej możliwej ścieżki (cennik.csv / cennik_szablon.csv) niepotrzebnie.
    for k in [k for k in _cennik_cache if k[0] == path]:
        del _cennik_cache[k]
    _cennik_cache[cache_key] = cennik
    return cennik


def _round_netto(katalogowa: float, rabat_pct: float) -> float:
    """Cena netto za sztukę, zaokrąglona do 2 miejsc."""
    return round(katalogowa * (1 - rabat_pct / 100.0), 2)


@dataclass
class PozycjaOferty:
    """
    Jedna pozycja oferty, sprowadzona do wspólnego kształtu.

    Poszczególne moduły doboru nazywają pola inaczej (PlcItem.nr kontra
    CabinetItem.nr_katalogowy kontra CableItem.typ_kabla), a calculate_budget
    oczekuje jednego kształtu. Ta klasa jest tym tłumaczeniem.
    """
    nr: str
    opis: str
    ilosc: float
    grupa_rabatowa: str
    zrodlo: str = ""          # PLC / Szafa / SCADA / Kable / HMI
    jednostka: str = "szt."


def zbierz_pozycje_oferty(plc_sel=None, cabinet_sel=None, asix_sel=None,
                          cable_sel=None, hmi_sel=None) -> list[PozycjaOferty]:
    """
    Wszystko, co ma trafić do kosztorysu, w JEDNYM miejscu.

    DLACZEGO TO ISTNIEJE: kosztorys był składany osobno w trzech miejscach
    i każde liczyło CO INNEGO:
      - sekcja 9 na ekranie ... sam sterownik,
      - raport Word ............ sam sterownik,
      - eksport Excel .......... sterownik + SCADA + szafa + HMI.
    Kable nie wchodziły NIGDZIE, mimo że cennik ma ich ceny za metr, a przy
    realnym projekcie to tysiące metrów.

    Skutek był taki, że "Suma netto" na ekranie nie była ofertą, tylko ceną
    samego sterownika - a inżynier, który uzupełnił cennik o obudowę, nie
    zobaczyłby po tym ŻADNEJ zmiany w tej sumie i miałby prawo uznać, że
    cennik nie działa.

    Trzy miejsca składające to samo z ręki musiały się rozjechać. Teraz jest
    jedno źródło; każda z sekcji przekazuje to, co ma, a czego nie ma (None),
    to po prostu nie wchodzi.

    zrodlo pozwala pokazać w tabeli, z której części oferty jest pozycja -
    bez tego kosztorys jest jedną długą listą bez podziału.
    """
    pozycje: list[PozycjaOferty] = []

    if plc_sel is not None:
        for it in plc_sel.items:
            pozycje.append(PozycjaOferty(
                nr=it.nr, opis=it.opis, ilosc=it.ilosc,
                grupa_rabatowa=it.grupa_rabatowa, zrodlo="PLC",
            ))

    if cabinet_sel is not None:
        for it in cabinet_sel.items:
            pozycje.append(PozycjaOferty(
                nr=it.nr_katalogowy, opis=it.nazwa, ilosc=it.ilosc,
                grupa_rabatowa=it.grupa_rabatowa or "APARATURA",
                zrodlo="Szafa", jednostka=it.jednostka,
            ))

    if asix_sel is not None:
        for it in asix_sel.items:
            pozycje.append(PozycjaOferty(
                nr=it.nr_katalogowy, opis=it.nazwa, ilosc=it.ilosc,
                grupa_rabatowa=it.grupa_rabatowa or "ASIX", zrodlo="SCADA",
            ))

    if cable_sel is not None:
        for it in cable_sel.items:
            # Ilość kabla to METRAŻ, nie sztuki - cennik ma ceny za metr.
            pozycje.append(PozycjaOferty(
                nr=it.typ_kabla, opis=it.opis, ilosc=it.metraz_m,
                grupa_rabatowa=it.grupa_rabatowa or "KABLE",
                zrodlo="Kable", jednostka="m",
            ))

    if hmi_sel is not None:
        for it in hmi_sel.items:
            # Numerem katalogowym jest MODEL panelu, nie sztuczne "HMI-1":
            # dzięki temu wpis w cenniku pod nazwą modelu w ogóle ma szansę
            # się dopasować.
            opis = f"{it.nazwa} ({it.lokalizacja})" if it.lokalizacja else it.nazwa
            pozycje.append(PozycjaOferty(
                nr=it.nazwa, opis=opis, ilosc=it.ilosc,
                grupa_rabatowa=it.grupa_rabatowa or "APARATURA", zrodlo="HMI",
            ))

    return pozycje


def calculate_budget(
    plc_items: list,
    rabaty: dict[str, float] | None = None,
    cennik_file: str = "cennik.csv",
) -> Budget:
    """
    Buduje kosztorys na podstawie pozycji z doboru PLC.

    plc_items: lista PlcItem z plc_selector (mają .nr, .opis, .ilosc, .grupa_rabatowa).
    rabaty: dict {GRUPA: procent}, np. {"BECKHOFF": 15, "SIEMENS": 20, "ASIX": 10}.
    cennik_file: plik CSV z cenami katalogowymi (na razie głównie ASIX).
    """
    if rabaty is None:
        rabaty = dict(GRUPY_RABATOWE)

    cennik = load_cennik(cennik_file)
    budget = Budget(rabaty=rabaty)

    for plc in plc_items:
        nr = plc.nr
        grupa = plc.grupa_rabatowa
        rabat = rabaty.get(grupa, 0.0)

        # Szukaj ceny w cenniku
        cena_kat = None
        if nr in cennik:
            cena_kat = cennik[nr].get("cena")

        # Oblicz netto
        cena_netto = None
        wartosc = None
        if cena_kat is not None:
            cena_netto = _round_netto(cena_kat, rabat)
            wartosc = round(cena_netto * plc.ilosc, 2)

        budget.items.append(BudgetItem(
            # Kategoria z pozycji, jeśli ją niesie (PozycjaOferty.zrodlo:
            # PLC / Szafa / SCADA / Kable / HMI). Zaszyte "PLC" dla wszystkiego
            # było prawdą tylko dopóki kosztorys obejmował sam sterownik.
            kategoria=getattr(plc, "zrodlo", "") or "PLC",
            nr_katalogowy=nr,
            nazwa=plc.opis,
            ilosc=plc.ilosc,
            jednostka=getattr(plc, "jednostka", "szt."),
            cena_katalogowa=cena_kat,
            grupa_rabatowa=grupa,
            rabat_pct=rabat,
            cena_netto_jed=cena_netto,
            wartosc_netto=wartosc,
        ))

    return budget


def format_budget(budget: Budget) -> str:
    """Tekstowe podsumowanie kosztorysu."""
    lines = ["Kosztorys:"]
    lines.append(f"  {'Nr katalogowy':<22} {'Ilość':>5} {'Kat.':>10} {'Rab.%':>6} {'Netto':>10} {'Wartość':>12}")
    lines.append("  " + "-" * 70)

    for it in budget.items:
        kat = f"{it.cena_katalogowa:.2f}" if it.cena_katalogowa else "BRAK"
        net = f"{it.cena_netto_jed:.2f}" if it.cena_netto_jed else "-"
        val = f"{it.wartosc_netto:.2f}" if it.wartosc_netto else "-"
        lines.append(f"  {it.nr_katalogowy:<22} {it.ilosc:>5} {kat:>10} {it.rabat_pct:>5.0f}% {net:>10} {val:>12}")

    lines.append("  " + "-" * 70)
    lines.append(f"  Suma katalogowa: {budget.suma_katalogowa:>12.2f} PLN")
    lines.append(f"  Suma netto:      {budget.suma_netto:>12.2f} PLN")

    if budget.brak_ceny:
        lines.append(f"\n  UWAGA: {len(budget.brak_ceny)} pozycji BEZ CENY KATALOGOWEJ (do uzupełnienia w cenniku)")

    return "\n".join(lines)
