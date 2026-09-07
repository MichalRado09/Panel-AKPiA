"""
core/comparison.py
==================
Porównanie wariantów sterowników obok siebie.

Inżynier widzi na jednym ekranie: ile kart, jaki koszt, jakie różnice
dla każdej platformy — i świadomie decyduje. Żadnej automatyki kosztowej,
tylko czytelne zestawienie danych do decyzji.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .plc_selector import select_plc, PLATFORMY, PlcItem
from .budget import calculate_budget
from .io_counter import IO_TYPES


@dataclass
class VariantSummary:
    """Podsumowanie jednego wariantu do porównania."""
    platforma: str
    cpu: str = ""
    karty_io: dict[str, int] = field(default_factory=dict)   # {typ: ilość kart}
    kanaly_io: dict[str, int] = field(default_factory=dict)   # {typ: dostępne kanały}
    total_modules: int = 0
    suma_katalogowa: float = 0.0
    suma_netto: float = 0.0
    brak_ceny: int = 0


def compare_variants(
    balance,
    rabaty: dict[str, float] | None = None,
    platformy: list[str] | None = None,
) -> list[VariantSummary]:
    """
    Porównuje dobór dla każdej platformy na tym samym bilansie I/O.

    balance: IOBalance z io_counter.
    rabaty: dict rabatów per grupa.
    platformy: lista platform do porównania (domyślnie: wszystkie zarejestrowane).
    """
    if platformy is None:
        platformy = list(PLATFORMY.keys())

    results: list[VariantSummary] = []

    for plat in platformy:
        try:
            sel = select_plc(balance, plat)
        except FileNotFoundError:
            # Platforma zarejestrowana w PLATFORMY, ale bez pliku katalogu w
            # katalogi/ (np. dodana do słownika, a CSV jeszcze nie dorobiony) -
            # pomijamy ją w porównaniu zamiast wywalać CAŁĄ tabelę dla
            # wszystkich platform z powodu jednej brakującej.
            continue
        budget = calculate_budget(sel.items, rabaty=rabaty or {})

        vs = VariantSummary(platforma=plat)

        # CPU rozpoznawany po katalog_typ (klucz z CSV), nie po dopasowaniu
        # tekstowym numeru katalogowego - patrz identyczna poprawka i
        # uzasadnienie w core/cabinet.py (numer CPU Siemensa nie zawiera
        # ani "CPU", ani "CX").
        for it in sel.items:
            if it.katalog_typ == "CPU":
                vs.cpu = it.nr
                break

        # Karty I/O i kanały
        for t in IO_TYPES:
            u = sel.utilization.get(t, {})
            vs.karty_io[t] = u.get("kart", 0)
            vs.kanaly_io[t] = u.get("kanałów_dostępnych", 0)

        vs.total_modules = sum(it.ilosc for it in sel.items)
        vs.suma_katalogowa = budget.suma_katalogowa
        vs.suma_netto = budget.suma_netto
        vs.brak_ceny = len(budget.brak_ceny)

        results.append(vs)

    return results
