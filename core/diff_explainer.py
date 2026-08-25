"""
core/diff_explainer.py
========================
Kontrakt z AI dla TRZECIEGO, opcjonalnego wywołania: wyjaśnienie PRZYCZYNY
różnic już wykrytych deterministycznie przez core/extraction_diff.py.

TO NIE JEST EKSTRAKCJA. Model na tym etapie NIE widzi surowego Excela ani
PDF-a - dostaje wyłącznie ZWIĘZŁĄ, już ustrukturyzowaną listę różnic
(core/extraction_diff.py::ExtractionDiff, zserializowaną do zwięzłego
tekstu). Zadanie modelu jest wąskie i zamknięte: dla KAŻDEJ różnicy
wybrać JEDNĄ kategorię przyczyny z zamkniętego zbioru (nie wolno wymyślać
własnych kategorii) i napisać jedno zdanie uzasadnienia.

DLACZEGO TO JEST BEZPIECZNE (zgodne z Zero-Halucynacją):
- Model nie zwraca żadnej liczby - tylko kategorię (enum) + krótki tekst.
- Wynik NIGDY nie zmienia bilansu I/O, doboru sprzętu ani kosztorysu -
  trafia wyłącznie do warstwy wyświetlanej inżynierowi jako pomoc
  diagnostyczna, obok (nie zamiast) surowej listy różnic z extraction_diff.
- Jeśli wywołanie się nie powiedzie (błąd API, timeout, zły JSON) -
  aplikacja MUSI dalej pokazać surowe różnice bez wyjaśnień, nigdy nie
  blokować użytkownika brakiem tego kroku. Ten moduł jest czystym
  dodatkiem, nie zależnością krytyczną.
"""

from __future__ import annotations

import json
import re

from .extraction_diff import ExtractionDiff

# Zamknięty zbiór kategorii przyczyn - model MUSI wybrać jedną z tych,
# nie wolno mu wymyślać własnej. To ogranicza ryzyko halucynacji przyczyny
# do zbioru, który inżynier rozumie i może samodzielnie zweryfikować.
KATEGORIE_PRZYCZYN = [
    "OBECNE_TYLKO_W_PDF",       # urządzenie widoczne wyłącznie w załączonym PDF -
                                 # offline strukturalnie nie mogła go zobaczyć
    "MOZLIWA_DEDUPLIKACJA",     # obie strony mają wiersz, ale różnie policzony -
                                 # możliwy efekt reguły łączenia pozycji zbiorczych
    "NARUSZENIE_KONTRAKTU_AI",  # model prawdopodobnie pominął/połączył/uzupełnił
                                 # dane wbrew zasadzie wierności 1:1 (ai_contract.py)
    "NIEJEDNOZNACZNE_ZRODLO",   # sam dokument źródłowy jest niejasny/sprzeczny -
                                 # obie interpretacje mogą być broniące się
    "NIEUSTALONE",              # brak wystarczających danych by wskazać przyczynę
]


def build_diff_explanation_prompt(diff: ExtractionDiff, pdf_byl_uzyty: bool) -> str:
    """
    Buduje zwięzły prompt z już wykrytymi różnicami - NIE z surowych danych
    źródłowych. pdf_byl_uzyty informuje model, czy w ogóle możliwe jest
    wyjaśnienie "obecne tylko w PDF" (jeśli PDF nie był użyty, ta kategoria
    jest z góry wykluczona - patrz uwaga w extraction_diff.py).
    """
    kontekst_pdf = (
        "Do ekstrakcji AI użyto Excela ORAZ załączonego PDF-a. Ścieżka OFFLINE "
        "czyta WYŁĄCZNIE Excel - nie ma strukturalnej możliwości zobaczenia "
        "niczego, co jest tylko w PDF-ie."
        if pdf_byl_uzyty else
        "Do ekstrakcji AI użyto WYŁĄCZNIE Excela - to samo źródło co ścieżka "
        "OFFLINE. Kategoria OBECNE_TYLKO_W_PDF jest więc niemożliwa w tym "
        "przypadku (nie było żadnego PDF-a) - NIE używaj jej."
    )

    tylko_offline = "\n".join(
        f"  - {e.oznaczenie}: {e.opis} (x{e.ilosc})" for e in diff.tylko_w_offline
    ) or "  (brak)"
    tylko_ai = "\n".join(
        f"  - {e.oznaczenie}: {e.opis} (x{e.ilosc})" for e in diff.tylko_w_ai
    ) or "  (brak)"
    bilans = "\n".join(
        f"  {t}: {v:+d}" for t, v in diff.balans_delta.items() if v != 0
    ) or "  (brak różnicy w bilansie I/O)"

    return f"""Porównano dwie niezależne ekstrakcje tej samej dokumentacji projektowej
AKPiA: ścieżkę OFFLINE (deterministyczny parser Excela) i ścieżkę AI (model
językowy). Wykryto różnice - patrz dane niżej. Twoim JEDYNYM zadaniem jest
wskazanie NAJBARDZIEJ PRAWDOPODOBNEJ przyczyny dla każdej różnicy.

KONTEKST: {kontekst_pdf}

RÓŻNICA W BILANSIE I/O (delta AI minus offline):
{bilans}

URZĄDZENIA TYLKO W OFFLINE (offline je widzi, AI - nie):
{tylko_offline}

URZĄDZENIA TYLKO W AI (AI je widzi, offline - nie):
{tylko_ai}

DLA KAŻDEJ pozycji z obu list wybierz DOKŁADNIE JEDNĄ kategorię z zamkniętego
zbioru (nie wolno wymyślać innych):
- OBECNE_TYLKO_W_PDF: urządzenie jest tylko w PDF, offline nie mogła go zobaczyć
- MOZLIWA_DEDUPLIKACJA: prawdopodobnie efekt reguły łączenia pozycji zbiorczych
- NARUSZENIE_KONTRAKTU_AI: model prawdopodobnie pominął/połączył/dopisał dane
- NIEJEDNOZNACZNE_ZRODLO: sam dokument źródłowy jest niejasny
- NIEUSTALONE: nie da się wskazać przyczyny z dostępnych danych

Nie masz dostępu do oryginalnego pliku - oceniasz WYŁĄCZNIE na podstawie nazw,
oznaczeń i kontekstu podanego wyżej. Jeśli nie masz podstaw do pewnej oceny,
wybierz NIEUSTALONE zamiast zgadywać.

Zwróć WYŁĄCZNIE poprawny JSON, bez komentarzy i markdown:
{{
  "wyjasnienia": [
    {{"oznaczenie": "...", "opis": "...", "kategoria": "JEDNA_Z_KATEGORII_WYZEJ",
      "uzasadnienie": "jedno krótkie zdanie"}}
  ],
  "podsumowanie": "1-2 zdania ogólnego podsumowania dla inżyniera"
}}
"""


def build_diff_explanation_schema() -> dict:
    """Schemat wymuszający wybór kategorii wyłącznie z zamkniętego zbioru."""
    return {
        "type": "object",
        "properties": {
            "wyjasnienia": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "oznaczenie": {"type": "string"},
                        "opis": {"type": "string"},
                        "kategoria": {"type": "string", "enum": KATEGORIE_PRZYCZYN},
                        "uzasadnienie": {"type": "string"},
                    },
                    "required": ["oznaczenie", "opis", "kategoria", "uzasadnienie"],
                },
            },
            "podsumowanie": {"type": "string"},
        },
        "required": ["wyjasnienia", "podsumowanie"],
    }


def parse_diff_explanation(text: str) -> dict:
    """
    Parsuje odpowiedź modelu. Rzuca ValueError przy złym formacie - wywołujący
    (app.py) MUSI to złapać i pokazać surowe różnice bez wyjaśnień zamiast
    wywalać całą stronę (patrz zasada w docstringu modułu).
    """
    if not text or not text.strip():
        raise ValueError("Pusta odpowiedź modelu przy wyjaśnianiu różnic.")

    raw = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1).strip()
    if not raw.startswith("{"):
        first, last = raw.find("{"), raw.rfind("}")
        if first != -1 and last != -1 and last > first:
            raw = raw[first:last + 1]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Nie udało się sparsować JSON wyjaśnienia różnic: {exc}") from exc

    if "wyjasnienia" not in data or not isinstance(data["wyjasnienia"], list):
        raise ValueError("Odpowiedź nie zawiera listy 'wyjasnienia'.")

    # Walidacja: każda kategoria MUSI być z zamkniętego zbioru - jeśli model
    # (mimo response_schema z enum) coś wymyślił, odrzucamy tę pozycję
    # zamiast pokazywać inżynierowi kategorię spoza ustalonego słownika.
    wyjasnienia_ok = [
        w for w in data["wyjasnienia"]
        if w.get("kategoria") in KATEGORIE_PRZYCZYN
    ]
    data["wyjasnienia"] = wyjasnienia_ok
    data.setdefault("podsumowanie", "")
    return data
