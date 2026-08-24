"""
core/ai_contract.py
===================
Nowy kontrakt z AI: model EKSTRAHUJE surową listę urządzeń i zwraca JSON.
NIE dobiera sprzętu, NIE liczy I/O, NIE generuje BOM - to robi Python w core/.

To jest cała rola LLM w nowym przepływie: zamienić nieuporządkowaną
dokumentację (PDF/Excel) na ustrukturyzowaną listę urządzeń, którą dalej
deterministycznie przetwarza rdzeń.

Prompt wymusza prosty format zgodny z parserem: te same pola, których
używa core.parser.Device.
"""

from __future__ import annotations

import json
import re


# Schemat JSON, którego oczekujemy od modelu. Pola zgodne z Device.
JSON_SCHEMA_HINT = """
{
  "urzadzenia": [
    {
      "lp": "1",
      "uklad": "RTO",
      "oznaczenie": "P1",
      "opis": "Pompa obiegowa glikolu - praca",
      "ilosc": 1,
      "moc_kw": 15.0,
      "napiecie": "400V 50Hz",
      "analog": "Zadawanie prędkości (AO)",
      "cyfrowy": "Start, Praca, Awaria",
      "komunikacja": "",
      "uwagi": ""
    }
  ]
}
""".strip()


def build_extraction_prompt() -> str:
    """
    Instrukcja systemowa dla AI w nowym trybie ekstrakcji.
    Zwraca WYŁĄCZNIE JSON zgodny ze schematem - bez doboru, bez BOM, bez rezerwy.
    """
    return f"""Jesteś ekspertem automatyki przemysłowej. Twoim JEDYNYM zadaniem jest
EKSTRAKCJA urządzeń OBIEKTOWYCH (procesowych) z dostarczonej dokumentacji
(arkusz Excel jako CSV i/lub PDF) i zwrócenie ich jako ustrukturyzowanej listy JSON.

ROZPOZNAWANIE TYPU DOKUMENTU:
Zanim zaczniesz ekstrakcję, oceń co to za dokument:
- "Zestawienie urządzeń / aparatury" -> ekstrahujesz urządzenia OBIEKTOWE (pompy, zawory, czujniki, falowniki).
- "Lista materiałów / BOM szafy" -> to NIE jest zestawienie urządzeń! Zawiera komponenty szafowe
  (złączki szynowe, przekaźniki interfejsowe, zabezpieczenia, obudowy Rittal, szyny DIN).
  W takim przypadku zwróć PUSTY JSON: {{"urzadzenia": []}}.
- "Schemat technologiczny / P&ID" -> ekstrahujesz urządzenia widoczne na schemacie.
- "Lista kablowa" -> to NIE jest zestawienie urządzeń! Zwróć pusty JSON.

CO JEST URZĄDZENIEM OBIEKTOWYM (ekstrahujesz):
Pompy, wentylatory, dmuchawy, zawory (regulacyjne, odcinające z siłownikiem),
przetworniki/czujniki (temperatury, ciśnienia, przepływu, poziomu, analityczne),
falowniki, nagrzewnice, chłodnice, wymienniki z napędami, przepustnice z napędem,
ciepłomierze, analizatory, panele HMI.

CO NIE JEST URZĄDZENIEM OBIEKTOWYM (NIE ekstrahujesz, nawet jeśli jest w dokumencie):
Złączki szynowe (PT 2,5, PT 4, PT 10, UTTB, UTN itp.), przekaźniki interfejsowe (RIF-1),
zabezpieczenia (wyłączniki, bezpieczniki, wkładki topikowe), zasilacze 24V DC,
obudowy szaf (Rittal, SE, AX, VX25), szyny DIN, kanały kablowe grzebieniowe,
dławnice, mostki, ścianki boczne, oznaczniki, końcówki kablowe, listwy zaciskowe,
karty sterownika PLC (EL1008, EL3058 itp.), moduły I/O, CPU, kable.
To są MATERIAŁY MONTAŻOWE, nie urządzenia obiektowe.

CZEGO NIE ROBISZ (to policzy program, nie Ty):
- NIE dobierasz sterownika PLC ani kart I/O.
- NIE zliczasz sygnałów, NIE dodajesz rezerwy.
- NIE dobierasz licencji SCADA.
- NIE liczysz kabli.
- NIE generujesz BOM ani kosztorysu.

ZASADA NADRZĘDNA (ZERO HALUCYNACJI):
Opieraj się WYŁĄCZNIE na danych ze źródeł. Jeśli jakiegoś pola nie ma w dokumentacji,
wstaw pusty string "" (lub null dla moc_kw). NIGDY nie wymyślaj wartości.
Przepisz sygnały DOKŁADNIE tak, jak są w dokumentacji (np. "Start, Praca, Awaria",
"Zadawanie prędkości (AO)", "4-20mA") - klasyfikacją DI/DO/AI/AO zajmie się program.

Szczególna uwaga na czujniki i przetworniki: jeśli w dokumencie jest urządzenie
pomiarowe (czujnik temperatury, ciśnienia, przepływu), ale BEZ jawnie podanego
sygnału - zostaw pola analog/cyfrowy puste. Program sam przypisze AI na podstawie
typu urządzenia. NIE wymyślaj sygnałów, których nie ma w źródle.

KRYTYCZNA ZASADA - NIE POMIJAJ WIERSZY BEZ NUMERU L.P.:
W realnych zestawieniach WIELE wierszy nie ma wypełnionego numeru L.p. ani
oznaczenia projektowego - to są kolejne, odrębne urządzenia tego samego typu
(np. druga, trzecia, czwarta "Przetwornik temperatury" pod rząd), a NIE
kontynuacja/duplikat wiersza powyżej. KAŻDY wiersz z wypełnionym polem
"Typ / Opis odbiornika" to OSOBNE urządzenie do wyekstrahowania, niezależnie
od tego, czy ma numer L.p., układ czy oznaczenie. Puste L.p. NIE oznacza
"pomiń ten wiersz" - oznacza tylko, że w tym konkretnym wierszu tych pól nie
podano. Licz i ekstrahuj WSZYSTKIE wiersze z opisem urządzenia, nawet jeśli
wygląda na powtórzenie poprzedniego typu.

CO EKSTRAHUJESZ - dla każdego urządzenia OBIEKTOWEGO wypełnij pola:
- lp: numer porządkowy (jeśli jest)
- uklad: nazwa układu/obszaru (np. "RTO", "AKPiA")
- oznaczenie: oznaczenie projektowe / TAG (np. "P1", "01PCB20 AT001")
- opis: typ / opis odbiornika (np. "Pompa obiegowa glikolu")
- ilosc: liczba sztuk (liczba całkowita; jeśli "min. 10" -> wpisz 10)
- moc_kw: moc w kW jako liczba (jeśli "np. 15,0" -> 15.0; brak -> null)
- napiecie: napięcie zasilające (np. "400V 50Hz")
- analog: OPIS sygnału analogowego DOSŁOWNIE ze źródła (np. "Zadawanie prędkości (AO)")
- cyfrowy: OPIS sygnału cyfrowego DOSŁOWNIE ze źródła (np. "Start, Praca, Awaria")
- komunikacja: magistrala jeśli podana (np. "Modbus", "Profinet", "M-Bus")
- uwagi: uwagi ze źródła

JEŚLI OTRZYMASZ DWA ŹRÓDŁA (Excel + PDF): połącz je, NIE licz podwójnie urządzeń
oczywiście występujących w obu. Sprzeczności zapisz w polu "uwagi" danej pozycji.

FORMAT ODPOWIEDZI:
Zwróć WYŁĄCZNIE poprawny JSON zgodny z poniższym schematem. Bez komentarzy,
bez tekstu przed ani po, bez znaczników markdown. Tylko obiekt JSON:

{JSON_SCHEMA_HINT}
"""


def build_response_schema() -> dict:
    """
    Schemat odpowiedzi (OpenAPI 3.0 subset) wymuszający strukturę JSON.
    Przekazywany do API jako response_schema razem z response_mime_type=application/json.
    Gwarantuje poprawny składniowo JSON zgodny ze strukturą (wg dokumentacji Gemini).
    """
    return {
        "type": "object",
        "properties": {
            "urzadzenia": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "lp": {"type": "string"},
                        "uklad": {"type": "string"},
                        "oznaczenie": {"type": "string"},
                        "opis": {"type": "string"},
                        "ilosc": {"type": "integer"},
                        "moc_kw": {"type": "number", "nullable": True},
                        "napiecie": {"type": "string"},
                        "analog": {"type": "string"},
                        "cyfrowy": {"type": "string"},
                        "komunikacja": {"type": "string"},
                        "uwagi": {"type": "string"},
                    },
                    "required": ["opis", "ilosc", "analog", "cyfrowy"],
                    "propertyOrdering": [
                        "lp", "uklad", "oznaczenie", "opis", "ilosc", "moc_kw",
                        "napiecie", "analog", "cyfrowy", "komunikacja", "uwagi",
                    ],
                },
            }
        },
        "required": ["urzadzenia"],
    }


def parse_ai_json(text: str) -> list[dict]:
    """
    Wyciąga i parsuje JSON z odpowiedzi modelu.
    Odporny na: znaczniki ```json ... ```, tekst wokół JSON, drobne śmieci.

    Zwraca listę słowników urządzeń (pole "urzadzenia").
    Rzuca ValueError, jeśli nie da się odczytać poprawnego JSON.
    """
    if not text or not text.strip():
        raise ValueError("Pusta odpowiedź modelu - brak JSON do sparsowania.")

    raw = text.strip()

    # 1) Zdejmij ewentualne ogrodzenie markdown ```json ... ```
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1).strip()

    # 2) Jeśli wokół JSON jest tekst, wytnij od pierwszego { do ostatniego }
    if not raw.startswith("{"):
        first = raw.find("{")
        last = raw.rfind("}")
        if first != -1 and last != -1 and last > first:
            raw = raw[first:last + 1]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Nie udało się sparsować JSON z odpowiedzi modelu: {exc}") from exc

    devices = data.get("urzadzenia")
    if devices is None:
        raise ValueError("JSON nie zawiera klucza 'urzadzenia'.")
    if not isinstance(devices, list):
        raise ValueError("Pole 'urzadzenia' nie jest listą.")

    return devices


if __name__ == "__main__":
    # Test parsowania odpowiedzi z typowymi zanieczyszczeniami
    samples = [
        '{"urzadzenia":[{"opis":"Pompa","ilosc":1}]}',
        '```json\n{"urzadzenia":[{"opis":"Zawór","ilosc":2}]}\n```',
        'Oto wynik:\n{"urzadzenia":[{"opis":"TI","ilosc":1}]}\nGotowe.',
    ]
    for s in samples:
        print(parse_ai_json(s))
