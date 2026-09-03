"""
core/hmi.py
===========
HMI (panel operatorski lokalny) jako OSOBNA pozycja od konfiguracji SCADA
ASIX — zgodnie z wytyczną przełożonego (mail):
  "HMI osobno, ASIX osobno – SCADA/ASIX traktujemy jako osobną stację
  operatorską, HMI - sterowanie lokalne (...) ale standardowo traktujemy
  to oddzielnie."

DLACZEGO MANUALNY WYBÓR, NIE AUTOMATYCZNY DOBÓR:
W przeciwnym razie zgadywalibyśmy model panelu i regułę doboru bez
potwierdzenia w realnym projekcie. Jedyny znaleziony przykład HMI
(Eco Malbork) był dostarczany przez producenta kotła wraz z rozdzielnicą —
nie ma tam samodzielnej konfiguracji firmy do naśladowania (podobna sytuacja
jak z S7-1200). Zamiast zgadywać model/rozmiar panelu, dajemy inżynierowi
prosty wybór: nazwa/model panelu (wolny tekst albo z krótkiej listy typowych
wielkości) + ilość. To zgodne z zasadą HITL — człowiek decyduje, narzędzie
tylko zestawia pozycję do kosztorysu.

Rozszerzenie w przyszłości: jeśli pojawi się realny projekt z HMI
konfigurowanym samodzielnie przez firmę, można dodać właściwy katalog
(jak dla PLC) z regułą doboru wg np. liczby ekranów procesowych.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Typowe wielkości paneli HMI (do podpowiedzi w interfejsie — NIE reguła
# automatycznego doboru, tylko lista ułatwiająca wybór inżynierowi).
TYPOWE_PANELE = [
    # Weintek 15.6" jako pierwszy, bo to JEDYNY panel potwierdzony w realnym
    # projekcie firmy (DPK2 Wujek, lista materiałów PT.E-05-3-202, poz. 1.55).
    # Reszta to typowe wielkości do wyboru, nie potwierdzone wdrożeniem.
    "Weintek 15.6\" (jak DPK2 Wujek)",
    "Siemens SIMATIC KTP400 (4\")",
    "Siemens SIMATIC KTP700 (7\")",
    "Siemens SIMATIC KTP900 (9\")",
    "Siemens SIMATIC KTP1200 (12\")",
    "Beckhoff CP2xxx Panel PC",
    "Inny / wpisz ręcznie",
]


@dataclass
class HmiItem:
    """Pozycja HMI wybrana ręcznie przez inżyniera."""
    nazwa: str
    ilosc: int = 1
    lokalizacja: str = ""     # np. "szafa sterownicza", "przy kotle"
    grupa_rabatowa: str = "APARATURA"


@dataclass
class HmiSelection:
    items: list[HmiItem] = field(default_factory=list)

    @property
    def total_ilosc(self) -> int:
        return sum(it.ilosc for it in self.items)


def build_hmi_selection(entries: list[dict]) -> HmiSelection:
    """
    Buduje wybór HMI z listy wpisów podanych przez inżyniera w interfejsie.
    entries: [{"nazwa": str, "ilosc": int, "lokalizacja": str}, ...]
    Pusta lista = brak HMI w tym projekcie (poprawny, częsty przypadek).
    """
    sel = HmiSelection()
    for e in entries:
        nazwa = (e.get("nazwa") or "").strip()
        if not nazwa:
            continue
        ilosc = int(e.get("ilosc") or 1)
        sel.items.append(HmiItem(
            nazwa=nazwa, ilosc=max(1, ilosc),
            lokalizacja=(e.get("lokalizacja") or "").strip(),
        ))
    return sel
