"""
core/device_rules.py
====================
Deterministyczny słownik typów urządzeń -> domyślne sygnały I/O.

STOSOWANY WYŁĄCZNIE gdy kolumny sygnałów w zestawieniu są PUSTE.
Jeśli inżynier wpisał sygnały jawnie - mają one pierwszeństwo, ten moduł
nie jest wtedy używany.

To NIE jest halucynacja - to reguła inżynierska: zawór regulacyjny
fizycznie zawsze daje sterowanie AO + sprzężenie AI. Każdy sygnał
wywnioskowany z typu urządzenia jest OZNACZANY (source="typ_urzadzenia"),
żeby inżynier widział, co przyjął parser, a co wynika z reguły.

NIEJEDNOZNACZNE TYPY (np. "czujnik/przetwornik temperatury"):
To samo słowo bywa w różnych projektach używane różnie - czasem oznacza
sam element pomiarowy bez własnego sygnału, czasem gotowy przetwornik
z sygnałem 4-20mA (potwierdzone przez inżyniera nadzorującego rozwój tej
aplikacji). Dla takich wzorców reguła świadomie NIE zgaduje - sygnał
dostaje typ NO_DATA, tak samo jak niesklasyfikowany sygnał cyfrowy z
signal_rules.py. Trafia do balance.undecided, inżynier rozstrzyga ręcznie
w tabeli wyników (ta sama, istniejąca ścieżka co dla innych BRAK DANYCH -
zero nowej architektury UI).

Rozbudowa: dopisz wzorzec do _DEVICE_PATTERNS. Kolejność ma znaczenie -
pierwszy pasujący wzorzec wygrywa, więc bardziej szczegółowe dawaj wyżej.
Użyj NO_DATA zamiast konkretnego typu, jeśli reguła nie jest pewna
niezależnie od projektu (patrz przykłady niżej).
"""

from __future__ import annotations

import re
import unicodedata

from .signal_rules import NO_DATA


_PL_MAP = str.maketrans({
    "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n",
    "ó": "o", "ś": "s", "ź": "z", "ż": "z",
})


def _norm(text: str) -> str:
    text = str(text).translate(_PL_MAP)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.strip().lower()


# --- Wzorce typów urządzeń ----------------------------------------------------
# Każdy wpis: (regex na znormalizowanym opisie, lista domyślnych sygnałów).
# Sygnał: {"typ": "AI|AO|DI|DO|BRAK DANYCH", "nazwa": "<opis>"}.
# Kolejność: od szczegółu do ogółu.
_DEVICE_PATTERNS: list[tuple[str, list[dict]]] = [
    # --- Pomiary jednoznaczne (typ pomiaru pewny z samego słowa) ---
    (r"przetwornik cisnien|roznicow.* cisnien",
     [{"typ": "AI", "nazwa": "Pomiar ciśnienia (4-20mA)"}]),

    # Przepływomierz: pomiar AI + zwykle impuls energii/objętości DI
    (r"przeplywomierz|\bfl\b|\bfq\b",
     [{"typ": "AI", "nazwa": "Pomiar przepływu (4-20mA)"},
      {"typ": "DI", "nazwa": "Impuls objętości/energii"}]),

    # Ciepłomierz/przelicznik energii: pomiary + impuls, często Modbus/M-Bus
    (r"cieplomierz|przelicznik energii|\bq\b.*energ",
     [{"typ": "AI", "nazwa": "Pomiar (przepływ/temp)"},
      {"typ": "DI", "nazwa": "Impuls energii"}]),

    (r"analizator|jakosci|przewodnos|\bph\b|\bco2\b|\bo2\b",
     [{"typ": "AI", "nazwa": "Pomiar analityczny (4-20mA)"}]),

    # --- Pomiary NIEJEDNOZNACZNE - to samo słowo bywa różnie w różnych
    # projektach (sam czujnik bez sygnału vs gotowy przetwornik z AI).
    # Świadomie NIE zgadujemy - NO_DATA trafia do listy BRAK DANYCH,
    # inżynier rozstrzyga ręcznie tak samo jak przy sygnałach cyfrowych.
    (r"przetwornik temperatur|czujnik temperatur|\btic?\b|\bti\b|termopar|rezystancyjn|pt100|pt-100",
     [{"typ": NO_DATA, "nazwa": "Pomiar temperatury - czujnik czy przetwornik? Rozstrzygnij ręcznie"}]),

    (r"czujnik cisnien|\bpi\b|\bpdi\b|\bpt\b",
     [{"typ": NO_DATA, "nazwa": "Pomiar ciśnienia - czujnik czy przetwornik? Rozstrzygnij ręcznie"}]),

    (r"poziomu|poziom\b|\blt\b|\bli\b",
     [{"typ": NO_DATA, "nazwa": "Pomiar poziomu - czujnik czy przetwornik? Rozstrzygnij ręcznie"}]),

    # --- Elementy wykonawcze jednoznaczne ---
    # Pompa z falownikiem: AO zadawanie + Start(DO) + Praca/Awaria(DI)
    # (sprawdzane PRZED ogólnym 'napęd', żeby nie złapał go wzorzec siłownika)
    (r"pompa.*inwerter|pompa.*falownik|pompa.*naped",
     [{"typ": "AO", "nazwa": "Zadawanie prędkości"},
      {"typ": "DO", "nazwa": "Start"},
      {"typ": "DI", "nazwa": "Praca"},
      {"typ": "DI", "nazwa": "Awaria"}]),

    # Pompa bez wzmianki o falowniku: DO Start + DI Praca/Awaria. To jest
    # WIĘKSZOŚCIOWY, typowy przypadek (pompa jednobiegowa) - jeśli konkretna
    # pompa ma jednak falownik, inżynier dopisze AO ręcznie po weryfikacji
    # (ostrzeżenie w warnings zawsze to sygnalizuje - patrz parser.py).
    (r"pompa\b",
     [{"typ": "DO", "nazwa": "Start"},
      {"typ": "DI", "nazwa": "Praca"},
      {"typ": "DI", "nazwa": "Awaria"}]),

    # Zawór/siłownik regulacyjny: sterowanie AO + sprzężenie zwrotne AI + 2 krańcówki DI
    # Reguła wg wytycznych przełożonego (mail, korekta poprzedniej wersji):
    # "1xAO + 1xAI + opcjonalnie 2xDI = 2/4 kanały". Poprzednia wersja (tylko
    # AO+2DI) POMIJAŁA sprzężenie zwrotne pozycji - to była luka merytoryczna.
    (r"zawor regulacyjn|silownik.*regulacyjn|zawor trojdrog|regulacyjn.*zawor|trojdrog",
     [{"typ": "AO", "nazwa": "Sterowanie położeniem (4-20mA)"},
      {"typ": "AI", "nazwa": "Sprzężenie zwrotne pozycji (4-20mA)"},
      {"typ": "DI", "nazwa": "Krańcówka otwarcia"},
      {"typ": "DI", "nazwa": "Krańcówka zamknięcia"}]),

    # Zawór odcinający z siłownikiem (on/off): DO otw/zamk + 2 krańcówki DI
    (r"zawor.*silownik|silownik.*zawor|zawor.*\(m\)|zawor z naped|zawor odcinaj.*silownik",
     [{"typ": "DO", "nazwa": "Otwórz/Zamknij"},
      {"typ": "DI", "nazwa": "Krańcówka otwarcia"},
      {"typ": "DI", "nazwa": "Krańcówka zamknięcia"}]),

    # Siłownik/napęd (nagrzewnica, przepustnica): AO sterowanie
    (r"silownik z naped|silownik.*naped|naped\b|przepustnic",
     [{"typ": "AO", "nazwa": "Sterowanie napędem"}]),

    # Zawór odcinający bez napędu / zwrotny / filtr: brak sygnałów I/O
    (r"zawor zwrotny|zawor odcinaj|filtr\b",
     []),
]


def infer_signals_from_type(opis: str) -> tuple[list[dict], str | None]:
    """
    Zwraca (lista_sygnałów, dopasowany_wzorzec) na podstawie opisu urządzenia.

    Każdy sygnał dostaje source="typ_urzadzenia" - znacznik, że wynika z reguły,
    nie z jawnego wpisu w kolumnie. Sygnały typu NO_DATA (patrz moduł wyżej)
    oznaczają: reguła rozpoznała KATEGORIĘ urządzenia, ale nie może pewnie
    określić sygnału bez dodatkowego kontekstu - trafiają do
    balance.undecided jak każdy inny BRAK DANYCH.

    Gdy nic nie pasuje: ([], None) - urządzenie bez rozpoznanego typu,
    parser oznaczy je jako wymagające decyzji inżyniera (inny komunikat
    niż dla NO_DATA - patrz parser.py::_attach_signals).
    """
    if not opis:
        return [], None

    n = _norm(opis)
    for pattern, signals in _DEVICE_PATTERNS:
        if re.search(pattern, n):
            enriched = [
                {**s, "source": "typ_urzadzenia"} for s in signals
            ]
            return enriched, pattern
    return [], None


if __name__ == "__main__":
    tests = [
        "Przetwornik temperatury",
        "Czujnik temperatury",
        "Przetwornik ciśnienia PI",
        "Przepływomierz",
        "Ciepłomierze (Q) - przeliczniki energii",
        "Zawór regulacyjny na bypassie",
        "Zawór z siłownikiem przy P1",
        "Zawór odcinający",
        "Pompa P6a - napęd inwerterowy",
        "Pompa obiegowa glikolu",
        "Siłownik z napędem 01PCB10 AA401",
        "Szafa zasilająco-sterownicza",  # brak dopasowania
    ]
    for t in tests:
        sig, pat = infer_signals_from_type(t)
        types = ", ".join(f"{s['typ']}" for s in sig) or "(brak / nierozpoznany)"
        print(f"{t:45} -> {types}")
