"""
core/cables.py
==============
Deterministyczny dobór okablowania obiektowego na podstawie bilansu I/O.

Reguły typów kabli wyprowadzone z realnych list kablowych:
  - PT_E053201 (DPK2 Wujek): BiT 750 CH/H, BiTservo
  - PT_E063201 (DPK1 Niwka): BiT 750/1000, ETHERLINE

Zasady:
- Każdy typ sygnału -> przypisany typ kabla (konfigurowalne).
- Metraż = ilość_urządzeń × średnia_trasa × naddatek_montażowy (1.15 = +15%).
- Naddatek 15% to standard na podejścia, zarobienie w szafie, zapasy.
- Inżynier podaje średnią trasę suwakiem; aplikacja liczy resztę.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


# Naddatek montażowy (15% na podejścia i zarobienie w szafie)
NADDATEK_MONTAZOWY = 1.15

# Realne średnie długości tras, ZMIERZONE na liście kablowej DPK2 Wujek
# (PT.E-05-3-201, 196 kabli). To NIE są wartości używane w obliczeniach -
# aplikacja liczy z jednej średniej podanej suwakiem przez inżyniera.
# Są tu, bo pokazują dwie rzeczy, o których warto wiedzieć przy ustawianiu
# tego suwaka:
#   1) domyślne 25 m jest realistyczne tylko dla ethernetu w szafie,
#   2) trasy RÓŻNIĄ SIĘ po typie sygnału prawie dwukrotnie - najdłuższe są
#      analogi (przetworniki stoją w terenie), najkrótsze sieć.
# Ustawienie jednej średniej dla wszystkich typów zawyża jedne, a zaniża
# drugie; przy 25 m najbardziej ucierpi metraż kabla ekranowanego, który
# w tym projekcie stanowił blisko połowę całego okablowania sygnałowego.
TRASY_REFERENCYJNE_M = {
    "AI": 65,        # 40 kabli BiT 750®CH 2x1,5 -> 2607 m
    "AO": 65,        # ten sam typ kabla co AI
    "DI": 36,        # 20 kabli BiT 750®H 4x1,5 -> 721 m
    "DO": 34,        # 12 kabli BiT 750®H 3G1,5 -> 405 m
    "FALOWNIK": 46,  # 6 kabli BiTservo -> 277 m
    "ETHERNET": 17,  # 24 kable ETHERLINE -> 408 m
}

# Reguły przypisania kabli do typów sygnałów (z realnych projektów)
# Inżynier może je nadpisać w panelu — to są rozsądne domyślne.
DOMYSLNE_KABLE = {
    "DI": {
        "typ": "BiT 750®H 4x1",
        "opis": "Kabel sterowniczy (sygnały cyfrowe DI)",
        "zyl": 4,
        "uwaga": "Wielożyłowy, wielu DI jednym kablem",
    },
    "DO": {
        "typ": "BiT 750®H 3G1,5",
        "opis": "Kabel sterowniczy (sygnały cyfrowe DO / zasilanie odbiorników)",
        "zyl": 3,
        "uwaga": "Z żyłą ochronną dla odbiorników",
    },
    "AI": {
        "typ": "BiT 750®CH 2x1",
        "opis": "Kabel ekranowany (sygnały analogowe 4-20mA)",
        "zyl": 2,
        "uwaga": "Ekranowany (CH) — wymagany dla pętli prądowych 4-20mA",
    },
    "AO": {
        "typ": "BiT 750®CH 2x1",
        "opis": "Kabel ekranowany (sygnały analogowe wyjściowe AO)",
        "zyl": 2,
        "uwaga": "Taki sam jak AI — pętla prądowa w drugą stronę",
    },
    "FALOWNIK": {
        "typ": "BiTservo®3plus 2XSLCH-J 3G2,5+3G1,5",
        "opis": "Kabel silnikowy do falownika (zasilanie + sterowanie)",
        "zyl": 6,
        # UWAGA: przekrój dobiera się do MOCY silnika, a tu jest zaszyty na
        # sztywno jeden. W DPK2 Wujek użyto trzech różnych: 3G2,5+3G0,5,
        # 3G6+3G1,5 oraz 3G10+3G1,5. Parser czyta moc urządzenia (Device.moc_kw),
        # więc dobór przekroju dałoby się zautomatyzować - wymaga jednak
        # tabeli moc -> przekrój od inżyniera, żeby nie zgadywać.
        "uwaga": "Specjalny kabel servo/falownikowy z ekranem (przekrój wg mocy - do weryfikacji)",
    },
    "ETHERNET": {
        "typ": "ETHERLINE LAN Cat.7 S/FTP",
        "opis": "Kabel sieciowy Ethernet (komunikacja PLC/SCADA)",
        "zyl": 8,
        "uwaga": "Cat.7 ekranowany — standard przemysłowy",
    },
}


@dataclass
class CableItem:
    """Jedna pozycja zestawienia kablowego."""
    typ_kabla: str
    opis: str
    typ_sygnalu: str
    ilosc_urzadzen: int
    srednia_trasa_m: float
    naddatek: float = NADDATEK_MONTAZOWY
    metraz_m: float = 0.0          # wyliczony
    grupa_rabatowa: str = "KABLE"


@dataclass
class CableSelection:
    """Pełne zestawienie kablowe."""
    items: list[CableItem] = field(default_factory=list)
    srednia_trasa: float = 0.0
    naddatek_pct: int = 15

    @property
    def total_metraz(self) -> float:
        return sum(it.metraz_m for it in self.items)


def _count_devices_by_signal(devices: list) -> dict[str, int]:
    """
    Zlicza URZĄDZENIA (nie kanały) per typ sygnału.
    Jeden kabel idzie do jednego urządzenia, niezależnie ile ma kanałów.
    Pompa z AO+DO+2DI = 1 kabel sterowania + 1 kabel falownika, nie 4 osobne.
    
    Uproszczenie: liczymy urządzenia wg dominującego sygnału.
    """
    counts: dict[str, int] = {"DI": 0, "DO": 0, "AI": 0, "AO": 0, "FALOWNIK": 0}
    
    for dev in devices:
        qty = getattr(dev, "ilosc", 1) or 1
        typy = {s.get("typ") for s in getattr(dev, "sygnaly", [])}
        
        # Urządzenie z AO + DO = falownik (kabel servo + kabel sterowania)
        if "AO" in typy and "DO" in typy:
            counts["FALOWNIK"] += qty
            counts["DI"] += qty   # sygnały zwrotne (praca/awaria) idą kablem sterowniczym
        elif "AO" in typy:
            counts["AO"] += qty
            if "DI" in typy:
                counts["DI"] += qty
        elif "AI" in typy:
            counts["AI"] += qty
        elif "DO" in typy:
            counts["DO"] += qty
        elif "DI" in typy:
            counts["DI"] += qty
    
    return counts


def select_cables(
    devices: list,
    srednia_trasa_m: float = 25.0,
    kable_override: dict[str, str] | None = None,
) -> CableSelection:
    """
    Dobiera okablowanie na podstawie listy urządzeń.

    devices: lista Device z parsera.
    srednia_trasa_m: średnia długość trasy kablowej od szafy do urządzenia [m].
    kable_override: opcjonalne nadpisanie typów kabli {typ_sygnalu: nazwa_kabla}.
    """
    sel = CableSelection(srednia_trasa=srednia_trasa_m)
    counts = _count_devices_by_signal(devices)
    
    kable = dict(DOMYSLNE_KABLE)
    if kable_override:
        for k, v in kable_override.items():
            if k in kable:
                kable[k]["typ"] = v

    for sig_type in ("DI", "DO", "AI", "AO", "FALOWNIK"):
        n = counts.get(sig_type, 0)
        if n <= 0:
            continue
        
        kabel = kable.get(sig_type)
        if not kabel:
            continue
        
        metraz = math.ceil(n * srednia_trasa_m * NADDATEK_MONTAZOWY)
        
        sel.items.append(CableItem(
            typ_kabla=kabel["typ"],
            opis=kabel["opis"],
            typ_sygnalu=sig_type,
            ilosc_urzadzen=n,
            srednia_trasa_m=srednia_trasa_m,
            metraz_m=metraz,
        ))

    return sel


def format_cables(sel: CableSelection) -> str:
    lines = [f"Zestawienie kablowe (śr. trasa {sel.srednia_trasa}m, naddatek {sel.naddatek_pct}%):"]
    lines.append(f"  {'Typ syg.':<10} {'Urządzeń':>8} {'Typ kabla':<35} {'Metraż [m]':>10}")
    lines.append("  " + "-" * 68)
    for it in sel.items:
        lines.append(f"  {it.typ_sygnalu:<10} {it.ilosc_urzadzen:>8} {it.typ_kabla:<35} {it.metraz_m:>10.0f}")
    lines.append("  " + "-" * 68)
    lines.append(f"  RAZEM metraż: {sel.total_metraz:.0f} m")
    return "\n".join(lines)
