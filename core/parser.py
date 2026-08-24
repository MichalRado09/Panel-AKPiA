"""
core/parser.py
==============
Deterministyczny parser zestawienia aparatury w PROSTYM formacie.

Mapuje kolumny PO NAZWIE (nie po pozycji) - odporność na przesunięcia
i drobne różnice w nagłówkach między plikami. Obsługuje realne "brudy"
danych ze zestawień:
  - ilości słowne: "min. 10", "np. 4"  -> wyciąga liczbę, oznacza flagą
  - moce: "np. 15,0", "~40,0"          -> parsuje liczbę z przecinkiem
  - znaki "?" i puste                  -> BRAK DANYCH (nie zgadujemy)
  - wiersze scalone (podpozycje bez L.p./Ilości) -> dziedziczą po nadrzędnym

Parser NIE dobiera sprzętu i NIE liczy I/O. Zwraca listę urządzeń w
jednolitej, ustrukturyzowanej formie. Zliczaniem zajmuje się io_counter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

import pandas as pd

from .signal_rules import (
    classify_digital_phrase,
    classify_analog_phrase,
    NO_DATA,
)
from .device_rules import infer_signals_from_type

# --- Mapa nagłówków: kanoniczna nazwa -> możliwe warianty w pliku -------------
# Klucz to nazwa, której używamy w kodzie. Wartości to fragmenty (lowercase),
# których szukamy w nagłówku kolumny. Pierwszy pasujący wygrywa.
_COLUMN_ALIASES = {
    "lp":        ["l.p.", "lp", "l.p", "nr"],
    "uklad":     ["układ", "uklad", "obszar", "system"],
    "oznaczenie":["urządzenie", "urzadzenie", "ozn. proj", "oznaczenie", "tag"],
    "opis":      ["typ / opis", "typ/opis", "opis odbiornika", "typ / opis odbiornika", "nazwa"],
    "ilosc":     ["ilość", "ilosc", "szt"],
    "moc":       ["moc jedn", "moc [kw]", "moc"],
    "napiecie":  ["napięcie zasil", "napiecie zasil", "napięcie"],
    "zasilanie": ["zasilanie 24v", "24v dc", "zasilanie"],
    "analog":    ["sygnał analog", "sygnal analog", "analog", "4-20ma"],
    "cyfrowy":   ["sygnał cyfrow", "sygnal cyfrow", "cyfrowy", "di/do"],
    "komunikacja":["komunikacja", "modbus", "profinet", "sieć", "siec"],
    "uwagi":     ["uwagi", "komentarz", "notatki"],
}


@dataclass
class Device:
    """Jedno urządzenie po sparsowaniu - forma neutralna, gotowa do zliczania."""
    lp: str = ""
    uklad: str = ""
    oznaczenie: str = ""
    opis: str = ""
    ilosc: int = 1
    ilosc_flaga: str = ""          # np. "min." gdy w pliku było "min. 10"
    moc_kw: float | None = None
    napiecie: str = ""
    komunikacja: str = ""
    uwagi: str = ""
    # Sygnały rozbite na osobne pozycje z typem DI/DO/AI/AO/BRAK DANYCH
    sygnaly: list[dict] = field(default_factory=list)
    # Ostrzeżenia parsera dla tego wiersza (trafiają do uwag inżyniera)
    warnings: list[str] = field(default_factory=list)


# --- Narzędzia parsujące "brudne" wartości -----------------------------------

def _parse_quantity(raw) -> tuple[int, str, list[str]]:
    """
    "1" -> (1, "", [])
    "min. 10" -> (10, "min.", ["Ilość podana jako 'min. 10' - przyjęto 10"])
    "np. 4" -> (4, "np.", [...])
    "" / "?" -> (1, "", ["Brak ilości - przyjęto 1"])  # bezpieczne minimum
    """
    warnings: list[str] = []
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return 1, "", ["Brak ilości - przyjęto domyślnie 1"]

    s = str(raw).strip()
    if s in {"", "?", "-"}:
        return 1, "", ["Brak ilości - przyjęto domyślnie 1"]

    # Flaga słowna przed liczbą
    flag = ""
    m_flag = re.match(r"^(min\.?|np\.?|ok\.?|~|>=|≥)\s*", s, re.IGNORECASE)
    if m_flag:
        flag = m_flag.group(1)
        warnings.append(f"Ilość podana jako '{s}' - przyjęto liczbę, oznaczono flagą '{flag}'")

    m_num = re.search(r"(\d+)", s)
    if not m_num:
        return 1, flag, [f"Nie rozpoznano liczby w ilości '{s}' - przyjęto 1"]

    return int(m_num.group(1)), flag, warnings


def _parse_power(raw) -> tuple[float | None, list[str]]:
    """ "np. 15,0" -> 15.0 ; "~40,0" -> 40.0 ; "-"/"" -> None """
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None, []
    s = str(raw).strip()
    if s in {"", "-", "?"}:
        return None, []
    s_num = s.replace("~", "").replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)", s_num)
    if not m:
        return None, [f"Nie rozpoznano mocy w '{s}'"]
    return float(m.group(1)), []


def _clean(raw) -> str:
    """Zamienia NaN/None na pusty string, przycina."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return ""
    return str(raw).strip()


# --- Mapowanie kolumn --------------------------------------------------------

def _build_column_map(columns: list[str]) -> dict[str, str]:
    """
    Zwraca mapę: nazwa_kanoniczna -> rzeczywista_nazwa_kolumny_w_pliku.
    Dopasowanie po fragmencie nagłówka (lowercase). Brakujące kolumny pomijane.
    """
    col_map: dict[str, str] = {}
    lowered = {str(c).strip().lower(): c for c in columns}

    for canon, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            match = next((orig for low, orig in lowered.items() if alias in low), None)
            if match is not None:
                col_map[canon] = match
                break
    return col_map


def _is_empty_row(row: pd.Series, col_map: dict) -> bool:
    """Wiersz pusty = brak opisu i oznaczenia (nic sensownego do sparsowania)."""
    opis = _clean(row.get(col_map.get("opis", ""), ""))
    ozn = _clean(row.get(col_map.get("oznaczenie", ""), ""))
    return not opis and not ozn


def _is_infrastructure(opis: str) -> bool:
    """
    Pozycje, które z definicji nie generują I/O sterownika (nie ostrzegamy o nich):
    szafy, zawory odcinające/zwrotne bez napędu, filtry, elementy montażowe.
    """
    n = opis.lower()
    markers = [
        "szafa", "zawór zwrotny", "zawor zwrotny", "zawór odcinający",
        "zawor odcinajacy", "zawór odcinajacy", "filtr", "kompensator",
        "przyłącz", "przylacz", "rura", "kołnierz", "kolnierz",
    ]
    return any(m in n for m in markers)


# --- Główna funkcja parsująca ------------------------------------------------

def parse_devices(df: pd.DataFrame) -> tuple[list[Device], list[str]]:
    """
    Parsuje DataFrame zestawienia -> (lista Device, globalne ostrzeżenia).

    Obsługa wierszy scalonych: jeśli wiersz nie ma L.p. ani ilości, ale ma opis,
    traktujemy go jako podpozycję nadrzędnego urządzenia (dziedziczy układ).
    """
    global_warnings: list[str] = []
    col_map = _build_column_map(list(df.columns))

    # Sprawdzenie kolumn krytycznych
    if "opis" not in col_map:
        global_warnings.append(
            "UWAGA: nie znaleziono kolumny opisu urządzenia - sprawdź nagłówki pliku."
        )
    for needed in ("analog", "cyfrowy"):
        if needed not in col_map:
            global_warnings.append(
                f"UWAGA: brak kolumny sygnałów '{needed}' - te sygnały nie zostaną zliczone."
            )

    devices: list[Device] = []
    last_uklad = ""

    for idx, row in df.iterrows():
        if _is_empty_row(row, col_map):
            continue

        dev = Device()

        dev.lp = _clean(row.get(col_map.get("lp", ""), ""))
        dev.uklad = _clean(row.get(col_map.get("uklad", ""), "")) or last_uklad
        if dev.uklad:
            last_uklad = dev.uklad  # zapamiętujemy do dziedziczenia w podpozycjach

        dev.oznaczenie = _clean(row.get(col_map.get("oznaczenie", ""), ""))
        dev.opis = _clean(row.get(col_map.get("opis", ""), ""))
        dev.napiecie = _clean(row.get(col_map.get("napiecie", ""), ""))
        dev.komunikacja = _clean(row.get(col_map.get("komunikacja", ""), ""))
        dev.uwagi = _clean(row.get(col_map.get("uwagi", ""), ""))

        qty, flag, qty_warn = _parse_quantity(row.get(col_map.get("ilosc", ""), None))
        dev.ilosc = qty
        dev.ilosc_flaga = flag
        dev.warnings.extend(qty_warn)

        moc, moc_warn = _parse_power(row.get(col_map.get("moc", ""), None))
        dev.moc_kw = moc
        dev.warnings.extend(moc_warn)

        # Sygnały: jednolita logika (kolumny + fallback typu urządzenia)
        analog_raw = _clean(row.get(col_map.get("analog", ""), ""))
        cyfrowy_raw = _clean(row.get(col_map.get("cyfrowy", ""), ""))
        _attach_signals(dev, analog_raw, cyfrowy_raw)

        devices.append(dev)

    return devices, global_warnings


def _attach_signals(dev: Device, analog_raw: str, cyfrowy_raw: str) -> None:
    """
    Przypisuje sygnały do urządzenia wg jednolitej reguły (wspólnej dla Excela i AI-JSON):
      1) sygnały jawne z kolumn (source="kolumna"),
      2) fallback na typ urządzenia TYLKO gdy kolumny puste (source="typ_urzadzenia"),
      3) ostrzeżenia dla BRAK DANYCH i nierozpoznanych typów.
    """
    jawne = []
    jawne.extend(classify_analog_phrase(analog_raw))
    jawne.extend(classify_digital_phrase(cyfrowy_raw))
    for s in jawne:
        s.setdefault("source", "kolumna")
    dev.sygnaly.extend(jawne)

    if not jawne:  # kolumny puste -> reguła typu urządzenia (decyzja "B")
        inferred, _pattern = infer_signals_from_type(dev.opis)
        if inferred:
            dev.sygnaly.extend(inferred)
            typy = ", ".join(s["typ"] for s in inferred)
            dev.warnings.append(
                f"Sygnały wywnioskowane z typu urządzenia ({typy}) "
                f"- kolumny sygnałów były puste. Do weryfikacji."
            )
        elif dev.opis and not _is_infrastructure(dev.opis):
            dev.warnings.append(
                "Brak sygnałów w kolumnach i nierozpoznany typ urządzenia "
                "- sprawdź, czy pozycja generuje I/O."
            )

    undecided = [s["nazwa"] for s in dev.sygnaly if s["typ"] == NO_DATA]
    if undecided:
        dev.warnings.append(
            f"Nie sklasyfikowano sygnału (DI/DO/AI/AO): {', '.join(undecided)} "
            f"- wymaga decyzji inżyniera."
        )


def devices_to_records(devices: list[Device]) -> list[dict]:
    """Konwersja do listy dict (np. do podglądu w tabeli / serializacji JSON)."""
    return [asdict(d) for d in devices]


def _device_from_record(rec: dict) -> Device:
    """
    Buduje Device z pojedynczego rekordu dict (np. z JSON od AI).
    Stosuje tę samą logikę sygnałów i fallback typu urządzenia co parse_devices,
    więc źródło danych (Excel vs AI-JSON) nie zmienia reguł doboru.
    """
    dev = Device()
    dev.lp = _clean(rec.get("lp", ""))
    dev.uklad = _clean(rec.get("uklad", ""))
    dev.oznaczenie = _clean(rec.get("oznaczenie", ""))
    dev.opis = _clean(rec.get("opis", ""))
    dev.napiecie = _clean(rec.get("napiecie", ""))
    dev.komunikacja = _clean(rec.get("komunikacja", ""))
    dev.uwagi = _clean(rec.get("uwagi", ""))

    qty, flag, qty_warn = _parse_quantity(rec.get("ilosc", None))
    dev.ilosc = qty
    dev.ilosc_flaga = flag
    dev.warnings.extend(qty_warn)

    # moc_kw może już przyjść jako liczba z JSON
    moc_raw = rec.get("moc_kw", None)
    if isinstance(moc_raw, (int, float)):
        dev.moc_kw = float(moc_raw)
    else:
        moc, moc_warn = _parse_power(moc_raw)
        dev.moc_kw = moc
        dev.warnings.extend(moc_warn)

    _attach_signals(dev, _clean(rec.get("analog", "")), _clean(rec.get("cyfrowy", "")))
    return dev


def parse_ai_devices(records: list[dict]) -> tuple[list[Device], list[str]]:
    """
    Buduje listę Device z listy rekordów JSON (od AI).
    Zawiera walidację wyniku — wykrywa podejrzane wzorce sugerujące,
    że AI dostało zły typ dokumentu.
    """
    global_warnings: list[str] = []
    if not records:
        global_warnings.append(
            "AI nie zwróciło żadnych urządzeń. Możliwe przyczyny: "
            "dokument nie zawiera zestawienia urządzeń obiektowych "
            "(np. to lista materiałów szafy lub lista kablowa)."
        )
        return [], global_warnings

    devices = [_device_from_record(r) for r in records if r]

    # Walidacja: wykrywanie podejrzanych wzorców
    if devices:
        from collections import Counter
        all_types = Counter()
        for d in devices:
            for s in d.sygnaly:
                if s.get("typ") in ("DI", "DO", "AI", "AO"):
                    all_types[s["typ"]] += d.ilosc

        total_signals = sum(all_types.values())

        # Jeśli >90% sygnałów to jeden typ — prawdopodobnie zły dokument
        if total_signals > 0:
            dominant_type, dominant_count = all_types.most_common(1)[0]
            if dominant_count / total_signals > 0.9 and total_signals > 5:
                global_warnings.append(
                    f"⚠ PODEJRZANY WYNIK: {dominant_count}/{total_signals} sygnałów "
                    f"({dominant_count/total_signals:.0%}) to {dominant_type}. "
                    f"Sprawdź, czy wgrany dokument to rzeczywiście zestawienie "
                    f"urządzeń obiektowych, a nie lista materiałów szafy lub BOM."
                )

        # Jeśli nazwy urządzeń wyglądają jak materiały szafowe
        suspect_names = ["złączk", "przekaźnik interfejs", "zabezpieczen",
                         "wyłącznik", "bezpiecznik", "szyna din", "zasilacz"]
        suspect_count = sum(
            1 for d in devices
            if any(s in d.opis.lower() for s in suspect_names)
        )
        if suspect_count > len(devices) * 0.5:
            global_warnings.append(
                f"⚠ PODEJRZANY WYNIK: {suspect_count}/{len(devices)} pozycji "
                f"wygląda na materiały szafowe (złączki, zabezpieczenia), "
                f"nie urządzenia obiektowe. Prawdopodobnie wgrano listę materiałów "
                f"zamiast zestawienia urządzeń."
            )

    return devices, global_warnings


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        print("Użycie: python -m core.parser <plik.xlsx>")
        sys.exit(1)

    df = pd.read_excel(path, sheet_name=0)
    devs, warns = parse_devices(df)

    print(f"Sparsowano urządzeń: {len(devs)}\n")
    for d in devs[:15]:
        sig = ", ".join(f"{s['nazwa']}[{s['typ']}]" for s in d.sygnaly) or "(brak)"
        print(f"  [{d.lp:>3}] {d.uklad:8} {d.oznaczenie:20} x{d.ilosc}  sygnały: {sig}")
        for w in d.warnings:
            print(f"        ! {w}")

    if warns:
        print("\nOSTRZEŻENIA GLOBALNE:")
        for w in warns:
            print(f"  - {w}")
