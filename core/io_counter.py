"""
core/io_counter.py
==================
Deterministyczne zliczanie sygnałów I/O + rezerwa projektowa.

Wejście: lista Device z parsera (każde ma .ilosc i .sygnaly z typami).
Wyjście: bilans DI/DO/AI/AO - bazowy i po rezerwie (zaokrąglony w górę).

ZASADY:
- Każdy sygnał liczony jest * ilość urządzenia (ilosc).
- Sygnały BRAK DANYCH liczone są OSOBNO i NIE trafiają do DI/DO/AI/AO
  (nie zgadujemy typu - raportujemy do decyzji inżyniera).
- Rezerwa: math.ceil(baza * (1 + rezerwa/100)) - zaokrąglenie ZAWSZE w górę.
- Zliczanie jest audytowalne: zwracamy też rozbicie źródeł (kolumna vs typ).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .signal_rules import NO_DATA

# Kanoniczne typy sygnałów procesowych
IO_TYPES = ("DI", "DO", "AI", "AO")


@dataclass
class IOBalance:
    """Bilans I/O - bazowy, po rezerwie i metadane do audytu."""
    base: dict[str, int] = field(default_factory=lambda: {t: 0 for t in IO_TYPES})
    reserved: dict[str, int] = field(default_factory=lambda: {t: 0 for t in IO_TYPES})
    reserve_percent: int = 0

    # Ile sygnałów pochodziło z jawnych kolumn, a ile z reguły typu urządzenia
    source_counts: dict[str, int] = field(
        default_factory=lambda: {"kolumna": 0, "typ_urzadzenia": 0}
    )
    # Sygnały nierozstrzygnięte (BRAK DANYCH) - z ilością, do raportu
    undecided: list[dict] = field(default_factory=list)

    @property
    def base_total(self) -> int:
        return sum(self.base.values())

    @property
    def reserved_total(self) -> int:
        return sum(self.reserved.values())


def _apply_reserve(value: int, reserve_percent: int) -> int:
    """math.ceil zaokrągla w górę: 56 @ 30% = ceil(72.8) = 73."""
    if reserve_percent <= 0:
        return value
    return math.ceil(value * (1 + reserve_percent / 100.0))


def count_io(devices: list, reserve_percent: int = 0) -> IOBalance:
    """
    Zlicza I/O z listy urządzeń.

    devices: lista obiektów z atrybutami .ilosc (int) i .sygnaly (list[dict]),
             gdzie sygnał = {"typ": "DI|DO|AI|AO|BRAK DANYCH", "nazwa": str,
                             "source": "kolumna|typ_urzadzenia"}
    """
    bal = IOBalance(reserve_percent=reserve_percent)

    for dev in devices:
        qty = getattr(dev, "ilosc", 1) or 1
        for sig in getattr(dev, "sygnaly", []):
            typ = sig.get("typ")
            src = sig.get("source", "kolumna")

            if typ in IO_TYPES:
                bal.base[typ] += qty
                if src in bal.source_counts:
                    bal.source_counts[src] += qty
            elif typ == NO_DATA:
                bal.undecided.append({
                    "urzadzenie": getattr(dev, "oznaczenie", "") or getattr(dev, "opis", ""),
                    "sygnal": sig.get("nazwa", ""),
                    "ilosc": qty,
                })

    # Rezerwa - osobno dla każdego typu, zaokrąglenie w górę
    for t in IO_TYPES:
        bal.reserved[t] = _apply_reserve(bal.base[t], reserve_percent)

    return bal


def format_balance(bal: IOBalance) -> str:
    """Czytelne podsumowanie tekstowe (do logów / podglądu)."""
    lines = ["Bilans I/O:", f"  {'Typ':<6}{'Baza':>8}{'+Rezerwa':>12}"]
    for t in IO_TYPES:
        lines.append(f"  {t:<6}{bal.base[t]:>8}{bal.reserved[t]:>12}")
    lines.append(f"  {'RAZEM':<6}{bal.base_total:>8}{bal.reserved_total:>12}")
    lines.append(f"  (rezerwa {bal.reserve_percent}%)")
    lines.append(
        f"  Źródło sygnałów: kolumny={bal.source_counts['kolumna']}, "
        f"z typu urządzenia={bal.source_counts['typ_urzadzenia']}"
    )
    if bal.undecided:
        lines.append(f"  BRAK DANYCH (do decyzji inżyniera): {len(bal.undecided)} sygnał(ów)")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    import pandas as pd
    from core.parser import parse_devices

    path = sys.argv[1]
    reserve = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    sheet = int(sys.argv[3]) if len(sys.argv) > 3 else 0

    df = pd.read_excel(path, sheet_name=sheet)
    devs, warns = parse_devices(df)
    bal = count_io(devs, reserve_percent=reserve)

    print(format_balance(bal))
    if bal.undecided:
        print("\nSygnały BRAK DANYCH:")
        for u in bal.undecided:
            print(f"  - {u['urzadzenie']}: {u['sygnal']} (x{u['ilosc']})")
