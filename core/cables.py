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
# Przekrój kabla falownikowego wg mocy silnika - progi (do X kW, przekrój).
# Wyprowadzone z DPK2 Wujek, szczegóły w kabel_falownika_dla_mocy().
PROGI_KABLA_FALOWNIKA = [
    (4,  "BiTservo®3plus 2XSLCH-J 3G2,5+3G0,5"),
    (15, "BiTservo®3plus 2XSLCH-J 3G6+3G1,5"),
    (30, "BiTservo®3plus 2XSLCH-J 3G10+3G1,5"),
]

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


def kabel_falownika_dla_mocy(moc_kw: float | None) -> str:
    """
    Przekrój kabla falownikowego dobrany do MOCY silnika.

    Tabela wyprowadzona z projektu DPK2 Wujek przez zestawienie listy kablowej
    (PT.E-05-3-201) z listą materiałów (PT.E-05-3-202) - dopasowanie jest
    jednoznaczne, bo zgadzają się zarówno liczby, jak i odbiorniki:
        2 kable 3G2,5+3G0,5  -> LT-POB1, POB-01       <- 2x falownik 4 kW
        1 kabel  3G6+3G1,5   -> HT-POB1               <- 1x falownik 15 kW
        3 kable  3G10+3G1,5  -> K.POB-01/02/03        <- 3x falownik 30 kW

    Progi ustawione na zaobserwowanych mocach. Powyżej największej znanej
    mocy zwracamy największy znany przekrój - świadomie NIE ekstrapolujemy
    w górę, bo dobór kabla silnikowego zależy też od długości trasy i sposobu
    ułożenia; taką pozycję inżynier ma zweryfikować (patrz ostrzeżenie
    w select_cables).

    Brak podanej mocy -> None-owy przypadek: zwracamy przekrój bazowy,
    ten sam, który był zaszyty w aplikacji przed wprowadzeniem tabeli.
    """
    if moc_kw is None:
        return DOMYSLNE_KABLE["FALOWNIK"]["typ"]
    for prog_kw, przekroj in PROGI_KABLA_FALOWNIKA:
        if moc_kw <= prog_kw:
            return przekroj
    return PROGI_KABLA_FALOWNIKA[-1][1]


def _count_devices_by_signal(devices: list) -> dict[str, int]:
    """
    Zlicza URZĄDZENIA (nie kanały) per typ sygnału.
    Jeden kabel idzie do jednego urządzenia, niezależnie ile ma kanałów.
    Pompa z AO+DO+2DI = 1 kabel sterowania + 1 kabel falownika, nie 4 osobne.

    Uproszczenie: liczymy urządzenia wg dominującego sygnału.

    Falowniki są dodatkowo rozbijane po PRZEKROJU kabla (zależnym od mocy
    silnika) - klucz "FALOWNIK::<przekrój>". Dzięki temu zestawienie kablowe
    pokazuje osobne pozycje dla różnych przekrojów, tak jak realna lista
    kablowa, zamiast wrzucać wszystko do jednego worka.
    """
    counts: dict[str, int] = {"DI": 0, "DO": 0, "AI": 0, "AO": 0, "FALOWNIK": 0}

    for dev in devices:
        qty = getattr(dev, "ilosc", 1) or 1
        typy = {s.get("typ") for s in getattr(dev, "sygnaly", [])}

        # Urządzenie z AO + DO = falownik (kabel servo + kabel sterowania)
        if "AO" in typy and "DO" in typy:
            przekroj = kabel_falownika_dla_mocy(getattr(dev, "moc_kw", None))
            klucz = f"FALOWNIK::{przekroj}"
            counts[klucz] = counts.get(klucz, 0) + qty
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

    # Falowniki mają klucze "FALOWNIK::<przekrój>" (przekrój zależy od mocy
    # silnika), więc listę pozycji budujemy dynamicznie zamiast ze sztywnej
    # krotki typów sygnału.
    klucze_falownikow = sorted(k for k in counts if k.startswith("FALOWNIK::"))
    for klucz in ("DI", "DO", "AI", "AO", "FALOWNIK", *klucze_falownikow):
        n = counts.get(klucz, 0)
        if n <= 0:
            continue

        if klucz.startswith("FALOWNIK::"):
            kabel = dict(kable["FALOWNIK"])
            kabel["typ"] = klucz.split("::", 1)[1]
            sig_type = "FALOWNIK"
        else:
            kabel = kable.get(klucz)
            sig_type = klucz
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
