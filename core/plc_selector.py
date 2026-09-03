"""
core/plc_selector.py
====================
Uniwersalny, deterministyczny dobór sterownika PLC na podstawie bilansu I/O.
Katalog kart czytany z pliku CSV (katalogi/*.csv) - dodanie nowej platformy
lub karty = edycja CSV, BEZ zmian w kodzie.

Reguła doboru (wspólna dla wszystkich platform, decyzja inżyniera):
  liczba modułów danego typu = ceil(kanały_po_rezerwie / kanały_na_moduł)
Rezerwa jest już wliczona w bilans (suwak %), tu NIE dokładamy modułów.

Walidacja:
  - Beckhoff na I/O Wujek (80/24/56/16) -> 10/3/7/4 (zgodne z rysunkiem).
  - Siemens ET200SP na I/O Malbork (33/16/4/6) @ rezerwa 30% -> 3/2/2/2
    (zgodne z realnym projektem po doliczeniu suwaka).

BaseUnit (Siemens ET200SP): każdy moduł na szynie potrzebuje podstawki.
Pierwszy moduł w stacji = BaseUnit "jasny" (z zasilaniem), pozostałe =
"ciemne" (mostkujące). Reguła uproszczona: 1 jasny + reszta ciemne na stację.
"""

from __future__ import annotations

import csv
import math
import os
from dataclasses import dataclass, field

IO_TYPES = ("DI", "DO", "AI", "AO")

# Katalog platform: klucz -> plik CSV. Rozszerzasz dopisując wpis + plik.
KATALOGI_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "katalogi")
PLATFORMY = {
    "Beckhoff CX9020": "beckhoff_cx.csv",
    "Beckhoff CX7000": "beckhoff_cx7000.csv",
    "Siemens ET200SP": "siemens_et200sp.csv",
}


@dataclass
class PlcItem:
    nr: str
    opis: str
    ilosc: int
    typ: str = ""
    grupa_rabatowa: str = ""
    # Oryginalny klucz z katalogu CSV (CPU/LICENSE/SERIAL/ETH/SDCARD/DI/DO/AI/AO/...).
    # Potrzebny tam, gdzie liczy się KONKRETNA rola pozycji (np. cabinet.py
    # szuka CPU do bilansu prądowego) - `typ` sam w sobie tego nie rozróżnia,
    # bo dla pozycji "systemowy" (CPU/LICENSE/ETH/SDCARD) jest wspólny.
    katalog_typ: str = ""


@dataclass
class PlcSelection:
    platforma: str = ""
    items: list[PlcItem] = field(default_factory=list)
    utilization: dict[str, dict] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def modules_on_rail(self) -> int:
        """Moduły montowane na szynie (io + szeregowy) - do liczenia BaseUnit."""
        return sum(i.ilosc for i in self.items if i.typ in ("io", "SERIAL"))


# Cache katalogów kart, kluczowany (ścieżka, mtime) - patrz analogiczny
# komentarz przy core/budget.py::_cennik_cache. compare_variants() woła
# select_plc() dla WSZYSTKICH platform pod rząd, więc kasujemy tu tylko
# przestarzałe wpisy DLA TEJ SAMEJ ścieżki, nie cały cache - inaczej
# wczytanie kolejnej platformy usuwałoby katalog poprzedniej.
_catalog_cache: dict[tuple[str, float], dict] = {}


def load_catalog(platforma: str) -> dict:
    """
    Wczytuje katalog kart platformy z CSV.
    Zwraca dict: {typ -> {nr, opis, kanaly, rola, grupa_rabatowa}}.
    Dla typów I/O 'kanaly' to int; dla systemowych puste.
    """
    if platforma not in PLATFORMY:
        raise ValueError(f"Nieznana platforma: {platforma}. Dostępne: {list(PLATFORMY)}")

    path = os.path.join(KATALOGI_DIR, PLATFORMY[platforma])
    if not os.path.exists(path):
        raise FileNotFoundError(f"Brak pliku katalogu: {path}")

    cache_key = (path, os.path.getmtime(path))
    cached = _catalog_cache.get(cache_key)
    if cached is not None:
        return cached

    catalog: dict[str, dict] = {}
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            typ = row["typ"].strip()
            kanaly = row.get("kanaly", "").strip()
            catalog[typ] = {
                "nr": row["nr_katalogowy"].strip(),
                "opis": row["opis"].strip(),
                "kanaly": int(kanaly) if kanaly else None,
                "rola": row.get("rola", "").strip(),
                "grupa_rabatowa": row.get("grupa_rabatowa", "").strip(),
            }

    for k in [k for k in _catalog_cache if k[0] == path]:
        del _catalog_cache[k]
    _catalog_cache[cache_key] = catalog
    return catalog


def _cards_needed(channels_required: int, channels_per_card: int) -> int:
    if channels_required <= 0 or not channels_per_card:
        return 0
    return math.ceil(channels_required / channels_per_card)


def select_plc(balance, platforma: str, use_serial_if: bool = True) -> PlcSelection:
    """
    Dobiera konfigurację PLC dla wybranej platformy na podstawie bilansu I/O.

    balance: IOBalance z core.io_counter (używamy .reserved - po rezerwie).
    platforma: klucz z PLATFORMY.
    use_serial_if: czy dołożyć moduł komunikacji szeregowej.
    """
    catalog = load_catalog(platforma)
    sel = PlcSelection(platforma=platforma)
    reserved = balance.reserved

    def add(typ: str, ilosc: int = 1):
        if typ in catalog and ilosc > 0:
            c = catalog[typ]
            sel.items.append(PlcItem(
                nr=c["nr"], opis=c["opis"], ilosc=ilosc,
                typ=c["rola"] if typ not in IO_TYPES else "io",
                grupa_rabatowa=c["grupa_rabatowa"],
                katalog_typ=typ,
            ))

    # 1) Elementy systemowe zawsze obecne (CPU, ETH, licencja, karta SD...)
    for typ in ("CPU", "LICENSE", "ETH", "SDCARD"):
        add(typ, 1)

    # 2) Interfejs szeregowy (opcjonalny)
    if use_serial_if and "SERIAL" in catalog:
        c = catalog["SERIAL"]
        sel.items.append(PlcItem(c["nr"], c["opis"], 1, typ="SERIAL",
                                 grupa_rabatowa=c["grupa_rabatowa"], katalog_typ="SERIAL"))

    # 3) Karty I/O - liczba wg zapotrzebowania po rezerwie
    for t in IO_TYPES:
        card = catalog.get(t)
        if not card or not card["kanaly"]:
            sel.warnings.append(f"Brak karty typu {t} w katalogu {platforma}.")
            continue
        req = reserved.get(t, 0)
        n = _cards_needed(req, card["kanaly"])
        avail = n * card["kanaly"]
        sel.utilization[t] = {
            "wymagane": req, "kart": n,
            "kanałów_dostępnych": avail, "zapas_kanałów": avail - req,
            "kanałów_na_kartę": card["kanaly"],
        }
        add(t, n)

    # 4) Elementy montażowe zależne od platformy
    _add_platform_extras(sel, catalog)

    return sel


def _add_platform_extras(sel: PlcSelection, catalog: dict) -> None:
    """Dokłada elementy montażowe specyficzne dla platformy."""
    n_modules = sel.modules_on_rail

    # Beckhoff: zasilacz E-bus co 12 terminali + pokrywa końcowa
    if "BUSPSU" in catalog:
        n_psu = max(0, (n_modules - 1) // 12)
        if n_psu > 0:
            c = catalog["BUSPSU"]
            sel.items.append(PlcItem(c["nr"], c["opis"], n_psu, typ="montaz",
                                     grupa_rabatowa=c["grupa_rabatowa"], katalog_typ="BUSPSU"))
            # UWAGA - ta reguła jest NIEZWALIDOWANA i wiemy, że bywa zawyżona.
            # Sprawdzone na projekcie referencyjnym DPK2 Wujek (rysunek
            # PT.E-05-3-404): listwa ma 26 terminali E-bus i DOKŁADNIE JEDEN
            # EL9410 (pozycja -A14, po 12 terminalach, a po nim jeszcze 14).
            # Reguła "co 12 modułów" dawałaby tam 2 sztuki.
            # Fizycznie o liczbie zasilaczy decyduje nie liczba modułów, tylko
            # POBÓR PRĄDU E-bus: CPU zasila magistralę do ok. 2 A, a EL9410
            # odświeża kolejne ok. 2 A - a każdy typ karty pobiera inaczej
            # (karty analogowe ok. dwukrotnie więcej niż cyfrowe). Dokładny
            # dobór wymaga kolumny z poborem E-bus w katalogu kart; do czasu
            # jej uzupełnienia zostawiamy oszacowanie w GÓRĘ (bezpieczniejsze
            # w ofercie niż pominięcie potrzebnego zasilacza) + to ostrzeżenie.
            sel.warnings.append(
                f"Zasilacz magistrali E-bus ({c['nr']}): {n_psu} szt. to SZACUNEK "
                f"wg reguły 'co 12 modułów', nie wynik bilansu prądowego magistrali. "
                f"Na projekcie referencyjnym (DPK2 Wujek, 26 terminali) ta reguła "
                f"zawyżała o 1 szt. - zweryfikuj przy tej konfiguracji."
            )
    if "ENDCAP" in catalog:
        c = catalog["ENDCAP"]
        sel.items.append(PlcItem(c["nr"], c["opis"], 1, typ="montaz",
                                 grupa_rabatowa=c["grupa_rabatowa"], katalog_typ="ENDCAP"))

    # Siemens ET200SP: BaseUnit dla każdego modułu (1 jasny + reszta ciemne)
    #                  + Bus Adapter (interfejs do CPU)
    if "BASEUNIT_LIGHT" in catalog and "BASEUNIT_DARK" in catalog:
        if n_modules > 0:
            cl = catalog["BASEUNIT_LIGHT"]
            cd = catalog["BASEUNIT_DARK"]
            sel.items.append(PlcItem(cl["nr"], cl["opis"], 1, typ="montaz",
                                     grupa_rabatowa=cl["grupa_rabatowa"], katalog_typ="BASEUNIT_LIGHT"))
            if n_modules > 1:
                sel.items.append(PlcItem(cd["nr"], cd["opis"], n_modules - 1, typ="montaz",
                                         grupa_rabatowa=cd["grupa_rabatowa"], katalog_typ="BASEUNIT_DARK"))
    if "BUSADAPTER" in catalog:
        c = catalog["BUSADAPTER"]
        # Bus Adapter: zwykle 1-2 (redundancja portów). Przyjmujemy 1 na stację.
        sel.items.append(PlcItem(c["nr"], c["opis"], 1, typ="montaz",
                                 grupa_rabatowa=c["grupa_rabatowa"], katalog_typ="BUSADAPTER"))


def format_selection(sel: PlcSelection) -> str:
    lines = [f"Dobór PLC ({sel.platforma}):"]
    for it in sel.items:
        lines.append(f"  {it.ilosc:>2}x  {it.nr:<22} {it.opis}")
    lines.append("\nWykorzystanie kart I/O:")
    for t, u in sel.utilization.items():
        lines.append(
            f"  {t}: {u['wymagane']:>3} kan. / {u['kanałów_na_kartę']} na kartę "
            f"-> {u['kart']} kart(y) (zapas {u['zapas_kanałów']} kan.)"
        )
    if sel.warnings:
        lines.append("\nUwagi:")
        for w in sel.warnings:
            lines.append(f"  ! {w}")
    return "\n".join(lines)
