"""
core/scada_asix.py
==================
Dobór pakietu licencyjnego SCADA ASIX oraz sugestia architektury.

Źródła reguł:
  - Cennik ASIX z dokumentacji handlowej 03/2026 (scalony do cennik.csv)
  - Reguła liczenia zmiennych procesowych z dokumentacji technicznej ASIX

Progi licencyjne (realne, z cennika — NIE MA pakietu 2048!):
  128, 256, 512, 1024, 4096, 8192, bez limitu

Metoda liczenia zmiennych procesowych:
  Każdy sygnał fizyczny (DI/DO/AI/AO) = 1 zmienna.
  Zmienne wirtualne niearchiwizowane NIE liczą się do limitu.
  Zmienne dwustanowe = 1 zmienna (nie 1/32).

Reguła uproszczona (do weryfikacji przez opiekuna):
  zmienne = suma sygnałów I/O (po rezerwie) × współczynnik
  Współczynnik 1.2 uwzględnia zmienne pomocnicze (alarmy, statusy, nastawy).
  Inżynier może go zmienić w panelu.

Sugestia architektury (na podstawie skali projektu):
  - do 256 zmiennych: stacja operatorska (1 stanowisko)
  - 257-1024 zmiennych: serwer + 1 terminal operatorski
  - 1025-4096 zmiennych: serwer + 2 terminale
  - >4096 zmiennych: serwer redundantny + terminale
  Inżynier ZAWSZE może nadpisać sugestię — to jest propozycja, nie decyzja.
"""

from __future__ import annotations

import math
import csv
import os
from dataclasses import dataclass, field

# Realne progi licencyjne ASIX (z cennika — sprawdzone, NIE MA 2048!)
PROGI = [128, 256, 512, 1024, 4096, 8192]
PROG_BEZ_LIMITU = "BEZ_LIMITU"

# Cennik pliku CSV
CENNIK_DIR = os.path.dirname(os.path.dirname(__file__))


@dataclass
class AsixItem:
    """Pozycja SCADA w kosztorysie."""
    nr_katalogowy: str
    nazwa: str
    ilosc: int = 1
    cena_katalogowa: float | None = None
    grupa_rabatowa: str = "ASIX"


@dataclass
class AsixSelection:
    """Wynik doboru SCADA ASIX."""
    # Obliczone zmienne
    zmienne_io: int = 0           # surowa suma I/O
    wspolczynnik: float = 1.2
    zmienne_obliczone: int = 0    # po współczynniku
    prog_licencyjny: int = 0      # dobrany próg
    prog_nazwa: str = ""

    # Sugestia architektury
    typ_licencji: str = ""        # "stacja" lub "serwer"
    sugestia_terminale: int = 0   # sugerowana liczba terminali
    sugestia_opis: str = ""       # tekstowy opis sugestii

    # Pozycje do kosztorysu
    items: list[AsixItem] = field(default_factory=list)

    warnings: list[str] = field(default_factory=list)


def _load_asix_prices() -> dict[str, dict]:
    """Wczytuje ceny ASIX z cennika CSV. Fallback na szablon bez cen (patrz budget.load_cennik)."""
    path = os.path.join(CENNIK_DIR, "cennik.csv")
    if not os.path.exists(path):
        template = os.path.join(CENNIK_DIR, "cennik_szablon.csv")
        if os.path.exists(template):
            path = template
        else:
            return {}
    prices: dict[str, dict] = {}
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            nr = row.get("Nr_katalogowy", "").strip()
            try:
                cena = float(row.get("Cena_Katalogowa", "0"))
            except (ValueError, TypeError):
                cena = None
            prices[nr] = {"nazwa": row.get("Nazwa", ""), "cena": cena}
    return prices


def _find_prog(zmienne: int) -> tuple[int, str]:
    """Dobiera najbliższy wyższy próg licencyjny."""
    for p in PROGI:
        if zmienne <= p:
            return p, f"{p} zmiennych"
    return 0, "Bez limitu"


def _suggest_architecture(zmienne: int) -> tuple[str, int, str]:
    """
    Sugeruje architekturę SCADA na podstawie liczby zmiennych.
    Zwraca (typ_licencji, liczba_terminali, opis).
    """
    if zmienne <= 256:
        return "stacja", 0, (
            "Stacja operatorska (1 stanowisko). "
            "Wystarczająca dla małych węzłów do 256 zmiennych."
        )
    elif zmienne <= 1024:
        return "serwer", 1, (
            "Serwer operatorski + 1 terminal. "
            "Zalecane dla średnich instalacji (257-1024 zmiennych). "
            "Terminal umożliwia obsługę z dodatkowego stanowiska."
        )
    elif zmienne <= 4096:
        return "serwer", 2, (
            "Serwer operatorski + 2 terminale. "
            "Dla dużych instalacji (1025-4096 zmiennych). "
            "Umożliwia obsługę z wielu stanowisk jednocześnie."
        )
    else:
        return "serwer", 3, (
            "Serwer redundantny + 3 terminale. "
            "Dla bardzo dużych/krytycznych instalacji (>4096 zmiennych). "
            "Redundancja serwerów zapewnia ciągłość pracy SCADA."
        )


def select_asix(balance, wspolczynnik: float = 1.2) -> AsixSelection:
    """
    Dobiera pakiet SCADA ASIX na podstawie bilansu I/O.

    balance: IOBalance z io_counter.
    wspolczynnik: mnożnik I/O → zmienne (1.2 = +20% na zmienne pomocnicze).
    """
    sel = AsixSelection(wspolczynnik=wspolczynnik)
    prices = _load_asix_prices()

    # 1. Oblicz zmienne
    sel.zmienne_io = sum(balance.reserved.get(t, 0) for t in ("DI", "DO", "AI", "AO"))
    sel.zmienne_obliczone = math.ceil(sel.zmienne_io * wspolczynnik)

    # 2. Dobierz próg
    sel.prog_licencyjny, sel.prog_nazwa = _find_prog(sel.zmienne_obliczone)

    # 3. Sugestia architektury
    sel.typ_licencji, sel.sugestia_terminale, sel.sugestia_opis = (
        _suggest_architecture(sel.zmienne_obliczone)
    )

    # 4. Dobierz pozycje do kosztorysu
    if sel.typ_licencji == "stacja":
        # Stacja operatorska z limitem
        if sel.prog_licencyjny > 0:
            nr = f"ASIX-WA{sel.prog_licencyjny}W+1R PM"
        else:
            nr = "ASIX-WANLW+1R PM"
        p = prices.get(nr, {})
        sel.items.append(AsixItem(
            nr_katalogowy=nr,
            nazwa=p.get("nazwa", f"Stacja operatorska, limit {sel.prog_nazwa}"),
            cena_katalogowa=p.get("cena"),
        ))
    else:
        # Serwer operatorski z limitem
        if sel.prog_licencyjny > 0:
            nr = f"ASIX-WA{sel.prog_licencyjny}S+1R PM"
        else:
            nr = "ASIX-WANLS+1R PM"
        p = prices.get(nr, {})
        sel.items.append(AsixItem(
            nr_katalogowy=nr,
            nazwa=p.get("nazwa", f"Serwer operatorski, limit {sel.prog_nazwa}"),
            cena_katalogowa=p.get("cena"),
        ))

        # Terminale operatorskie
        if sel.sugestia_terminale > 0:
            nr_t = "ASIX-WANLO + 1R PM"
            p_t = prices.get(nr_t, {})
            sel.items.append(AsixItem(
                nr_katalogowy=nr_t,
                nazwa=p_t.get("nazwa", "Terminal operatorski"),
                ilosc=sel.sugestia_terminale,
                cena_katalogowa=p_t.get("cena"),
            ))

    # Ostrzeżenia
    if sel.zmienne_obliczone > 8192:
        sel.warnings.append(
            "Liczba zmiennych przekracza 8192 — rozważ pakiet bez limitu "
            "lub podział na segmenty."
        )

    return sel


def format_asix(sel: AsixSelection) -> str:
    lines = [
        "Dobór SCADA ASIX:",
        f"  Sygnałów I/O (po rezerwie): {sel.zmienne_io}",
        f"  Współczynnik zmiennych: ×{sel.wspolczynnik}",
        f"  Zmiennych procesowych: {sel.zmienne_obliczone}",
        f"  Pakiet licencyjny: {sel.prog_nazwa}",
        f"",
        f"  Sugestia architektury: {sel.sugestia_opis}",
        f"",
        f"  Pozycje:"
    ]
    for it in sel.items:
        cena = f"{it.cena_katalogowa:.2f} PLN" if it.cena_katalogowa else "BRAK CENY"
        lines.append(f"    {it.ilosc}x {it.nr_katalogowy} — {it.nazwa} ({cena})")
    if sel.warnings:
        lines.append("")
        for w in sel.warnings:
            lines.append(f"  ! {w}")
    return "\n".join(lines)
