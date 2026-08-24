"""
core/signal_rules.py
=====================
Deterministyczny słownik reguł klasyfikacji sygnałów cyfrowych na DI / DO.

ZASADA: to jest JEDYNE miejsce, gdzie decydujemy "Start" -> DO, "Awaria" -> DI.
Reguły są jawne i audytowalne. AI NIE klasyfikuje sygnałów - dostarcza tylko
surowy tekst opisu, a ten moduł go interpretuje.

Słowniki wyprowadzono z realnych opisów w zestawieniach projektowych, np.:
  "Start, Praca, Awaria"        (pompa P1/P2)
  "Otwórz/Zamknij, Krańcówki"   (zawór z siłownikiem)
  "Gotowość, Zezwolenie, Awaria"(wymiana danych z RTO)

Rozbudowa: dopisz frazę do właściwego zbioru. Dopasowanie jest po
znormalizowanym rdzeniu słowa (bez polskich znaków, lowercase), więc
"Awaria", "awarii", "AWARIA" trafią w ten sam wpis.
"""

from __future__ import annotations

import re
import unicodedata

# --- Reguły: fraza (rdzeń) -> typ sygnału -------------------------------------
# DO = sterownik WYSTAWIA sygnał (wyjście): zał/wył, otwórz, zezwól.
# DI = sterownik CZYTA stan (wejście): praca, awaria, krańcówka, gotowość.

_DO_KEYWORDS = {
    "start", "stop", "zalacz", "wylacz", "zal", "wyl",
    "otworz", "zamknij", "otwarcie", "zamkniecie",
    "zezwolenie", "zezwol", "blokada", "reset",
    "sterowanie", "steruj", "rozruch",
}

_DI_KEYWORDS = {
    "praca", "awaria", "gotowosc", "status", "stan",
    "kraniec", "krancowka", "kraniecowka", "kranc",
    "potwierdzenie", "sygnalizacja", "alarm",
    "zadzialanie", "obecnosc", "kontrola",
    "otwarty", "zamkniety",  # krańcówki położenia zaworu = DI
}

# Frazy niejednoznaczne, których świadomie NIE zgadujemy.
# Trafiają do "BRAK DANYCH" i lądują w uwagach dla inżyniera.
_AMBIGUOUS = {
    "sygnal", "sygnaly", "opcja", "praca/rezerwa",
}


_PL_MAP = str.maketrans({
    "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n",
    "ó": "o", "ś": "s", "ź": "z", "ż": "z",
})


def _normalize(text: str) -> str:
    """Zdejmuje polskie znaki, lowercase, przycina. 'Krańcówki' -> 'krancowki'."""
    text = str(text).translate(_PL_MAP)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.strip().lower()


def _match_keyword(token: str, keywords: set[str]) -> bool:
    """
    Dopasowanie po rdzeniu: token zaczyna się od któregoś słowa kluczowego
    albo odwrotnie. Pozwala złapać 'awarii', 'krancowka', 'otwarcie'
    bez wypisywania wszystkich odmian.
    """
    for kw in keywords:
        if token.startswith(kw) or kw.startswith(token):
            if min(len(token), len(kw)) >= 3:  # unikamy przypadkowych 2-literowych
                return True
    return False


# Sygnał BRAK DANYCH - jawny znacznik, spójny w całym systemie.
NO_DATA = "BRAK DANYCH"


def classify_digital_phrase(phrase: str) -> list[dict]:
    """
    Rozbija słowny opis sygnałów cyfrowych na osobne sygnały z typem DI/DO.

    Wejście:  "Start, Praca, Awaria"
    Wyjście:  [
        {"nazwa": "Start",  "typ": "DO"},
        {"nazwa": "Praca",  "typ": "DI"},
        {"nazwa": "Awaria", "typ": "DI"},
    ]

    Sygnał nierozpoznany dostaje typ = NO_DATA (nie zgadujemy).
    Puste / "-" / "brak" zwraca [] (brak sygnałów cyfrowych).
    """
    if not phrase:
        return []

    raw = str(phrase).strip()
    if raw in {"-", "", "brak", "Brak", "BRAK"}:
        return []

    # Rozdzielamy po przecinku, ukośniku, średniku, "i", nowej linii.
    parts = re.split(r"[,/;\n]|\bi\b", raw)
    signals: list[dict] = []

    for part in parts:
        name = part.strip(" .[]")           # zachowujemy nawiasy do wykrycia (DO)/(DI)
        if not name:
            continue

        token = _normalize(name)

        # Jawny znacznik (DO)/(DI) w tekście ma pierwszeństwo nad heurystyką.
        if "(do)" in token:
            signals.append({"nazwa": name.strip("() "), "typ": "DO"})
            continue
        if "(di)" in token:
            signals.append({"nazwa": name.strip("() "), "typ": "DI"})
            continue

        # Po sprawdzeniu znacznika czyścimy resztki nawiasów do dalszej analizy
        name = name.strip(" ().")
        token = _normalize(name)

        # Odrzucamy słowa-wypełniacze, które nie są sygnałem
        if token in {"opcja", "opcje", "np", ""}:
            continue

        if _match_keyword(token, _DO_KEYWORDS):
            sig_type = "DO"
        elif _match_keyword(token, _DI_KEYWORDS):
            sig_type = "DI"
        else:
            sig_type = NO_DATA  # jawnie: nie wiemy, decyzja inżyniera

        signals.append({"nazwa": name, "typ": sig_type})

    return signals


def classify_analog_phrase(phrase: str) -> list[dict]:
    """
    Klasyfikuje opis sygnału analogowego na AI / AO.

    Reguła:
      - "Zadawanie prędkości (AO)", "Sterowanie (AO)" -> AO (wyjście do falownika/zaworu)
      - "4-20mA", "0-10V", "RTD", "Pt100", "Pomiar"    -> AI (wejście pomiarowe)
    Jawny znacznik (AO)/(AI) w tekście ma pierwszeństwo nad heurystyką.
    """
    if not phrase:
        return []

    raw = str(phrase).strip()
    if raw in {"-", "", "brak", "Brak", "BRAK"}:
        return []

    token = _normalize(raw)

    # 1) Jawny znacznik w nawiasie ma pierwszeństwo
    if "(ao)" in token or token.endswith("ao"):
        return [{"nazwa": raw, "typ": "AO"}]
    if "(ai)" in token:
        return [{"nazwa": raw, "typ": "AI"}]

    # 2) Heurystyka po treści
    ao_markers = ("zadawanie", "sterowanie", "nastaw", "predkosc", "regulacyjn")
    ai_markers = ("4-20", "0-10", "rtd", "pt100", "pt-100", "pomiar", "ma", "mv")

    if any(m in token for m in ao_markers):
        return [{"nazwa": raw, "typ": "AO"}]
    if any(m in token for m in ai_markers):
        return [{"nazwa": raw, "typ": "AI"}]

    # 3) Nie wiemy - jawnie BRAK DANYCH (typ do decyzji inżyniera)
    return [{"nazwa": raw, "typ": NO_DATA}]


if __name__ == "__main__":
    # Szybki self-test na realnych frazach z zestawień
    tests_digital = [
        "Start, Praca, Awaria",
        "Otwórz/Zamknij, Krańcówki",
        "Gotowość, Zezwolenie, Awaria",
        "Otwórz/Zamknij",
        "-",
        "Opcja (DO)",
        "Impulsy (Energia)",
    ]
    print("=== SYGNAŁY CYFROWE ===")
    for t in tests_digital:
        print(f"{t!r:35} -> {classify_digital_phrase(t)}")

    tests_analog = [
        "Zadawanie prędkości (AO)",
        "Sterowanie (AO)",
        "4-20mA (Przepływ, Temp)",
        "4-20mA lub RTD",
        "-",
    ]
    print("\n=== SYGNAŁY ANALOGOWE ===")
    for t in tests_analog:
        print(f"{t!r:35} -> {classify_analog_phrase(t)}")
