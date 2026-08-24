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
    # Czy ŹRÓDŁO jawnie podało ilość, czy parser przyjął domyślną 1.
    # Krytyczne dla deduplikacji: wiersz bez L.p. I bez jawnej Ilości to
    # wiersz szczegółowy, nawet jeśli ma wypełnione oznaczenie projektowe.
    ilosc_podana: bool = False
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


def _has_quantity(raw) -> bool:
    """
    Czy źródło JAWNIE podało ilość? Odróżnia pustą komórkę (False) od jawnej
    jedynki (True). Ta różnica decyduje, czy wiersz jest pozycją samodzielną,
    czy wierszem szczegółowym rozpisującym pozycję zbiorczą.
    """
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return False
    return str(raw).strip() not in {"", "?", "-"}


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

        raw_qty = row.get(col_map.get("ilosc", ""), None)
        qty, flag, qty_warn = _parse_quantity(raw_qty)
        dev.ilosc = qty
        dev.ilosc_flaga = flag
        dev.ilosc_podana = _has_quantity(raw_qty)
        dev.warnings.extend(qty_warn)

        moc, moc_warn = _parse_power(row.get(col_map.get("moc", ""), None))
        dev.moc_kw = moc
        dev.warnings.extend(moc_warn)

        # Sygnały: jednolita logika (kolumny + fallback typu urządzenia)
        analog_raw = _clean(row.get(col_map.get("analog", ""), ""))
        cyfrowy_raw = _clean(row.get(col_map.get("cyfrowy", ""), ""))
        _attach_signals(dev, analog_raw, cyfrowy_raw)

        devices.append(dev)

    devices, dedup_warnings = _deduplicate_hierarchical_aggregates(devices)
    global_warnings.extend(dedup_warnings)

    return devices, global_warnings


def _normalize_opis_key(opis: str) -> str:
    """Uproszczony klucz porównawczy opisu - do wykrywania powtarzających się typów."""
    n = _normalize_pl(opis).strip()
    # Odetnij końcowe oznaczenia typu "D1", "D2", numer w nawiasie itp. - zostaw rdzeń
    n = re.sub(r"\s*\(?\b[a-z]?\d+\)?\s*$", "", n).strip()
    return n


def _normalize_pl(text: str) -> str:
    pl_map = str.maketrans({
        "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n",
        "ó": "o", "ś": "s", "ź": "z", "ż": "z",
    })
    return str(text).lower().translate(pl_map)


def _keywords(opis_key: str) -> set[str]:
    """Wyciąga istotne słowa kluczowe (długość >=5 znaków) do porównania podobieństwa."""
    stopwords = {"ukladzie", "rozne", "roznych", "razem", "obiekcie"}
    return {w for w in opis_key.split() if len(w) >= 5 and w not in stopwords}


# Wzorzec kodu obszaru instalacji, np. "01pcb10", "01pcb40" - format: cyfry+litery+cyfry.
# Takie kody są SILNIEJSZYM sygnałem przynależności niż ogólne słowa opisowe
# (np. "siłownik", "napędem"), bo różne obszary instalacji (01PCB10 vs 01PCB40)
# to RÓŻNE, niezależne urządzenia, nawet jeśli mają identyczny numer tagu (np.
# oba "AA401") i te same słowa opisowe. Wykryte realnie: "Siłownik z napędem
# 01PCB10 AA401" (grupa D1-D4) błędnie dopasowany do "Siłownik z napędem
# 01PCB40 AA401" (inny, niezależny obszar) przez wspólne "siłownik"+"napędem"+
# przypadkowo powtórzony numer tagu "aa401".
_AREA_CODE_RE = re.compile(r"^\d{2}[a-z]{2,5}\d{1,3}$")

# Zbyt ogólne słowa techniczne, które same w sobie NIE powinny wystarczać do
# uznania dwóch urządzeń za tę samą grupę (występują w wielu, różnych typach
# urządzeń) - wykluczone z dopasowania, chyba że nie ma nic bardziej specyficznego.
_GENERIC_TECH_WORDS = {"silownik", "napedem", "naped", "urzadzenie", "element"}


def _area_codes(opis_key: str) -> set[str]:
    """Wyciąga kody obszaru instalacji (np. '01pcb10') z klucza opisu."""
    return {w for w in opis_key.split() if _AREA_CODE_RE.match(w)}


def _keywords_specific(opis_key: str) -> set[str]:
    """Słowa kluczowe BEZ zbyt ogólnych terminów technicznych - do dopasowania precyzyjnego."""
    return _keywords(opis_key) - _GENERIC_TECH_WORDS


# Próg: minimalna liczba tematycznie podobnych "bezimiennych" wierszy, żeby uznać
# to za wzorzec zbiorczego licznika, a nie przypadkowe podobieństwo dwóch urządzeń.
_DEDUP_MIN_MATCHES = 2


def _deduplicate_hierarchical_aggregates(
    devices: list[Device],
) -> tuple[list[Device], list[str]]:
    """
    Wykrywa i USUWA z liczenia wiersze "bezimienne" (bez L.p./oznaczenia, domyślna
    ilość=1), które są prawdopodobnie WYPISANYMI Z NAZWY EGZEMPLARZAMI zbiorczej
    pozycji (wiersz z L.p. i jawną Ilość > 1 o tematycznie zbliżonym opisie).

    UZASADNIENIE (zweryfikowane matematycznie na realnym pliku): poprawne
    "rozbicie" zbiorczej pozycji na N osobnych wierszy z ilością 1 daje IDENTYCZNY
    bilans I/O co pozostawienie jej jako 1 wiersza z ilością N - to tylko dwa
    zapisy tych samych fizycznych urządzeń. Problem pojawia się, gdy źródło ma
    OBA zapisy naraz (zbiorczy licznik + częściowa/pełna lista nazwanych
    egzemplarzy) - wtedy naiwne liczenie każdego wiersza osobno dolicza te same
    urządzenia dwukrotnie. Ta funkcja usuwa nadmiarowe wiersze "bezimienne",
    zostawiając jeden, autorytatywny zbiorczy licznik.

    AUDYT: nic nie znika po cichu. Usunięcie jest zawsze:
    - zapisane jako ostrzeżenie globalne z pełną listą usuniętych opisów,
    - dopisane do pola "uwagi" pozycji zbiorczej, która pozostaje w wyniku.
    Jeśli interpretacja jest błędna dla konkretnego pliku (bezimienne wiersze
    faktycznie były dodatkowymi, niezależnymi urządzeniami) - inżynier zobaczy
    to w ostrzeżeniu i może zgłosić korektę reguły, ale NIE zostanie to
    przeoczone w milczeniu.
    """
    warnings: list[str] = []

    aggregates: list[Device] = []
    unnamed: list[Device] = []
    other: list[Device] = []

    for dev in devices:
        kws = _keywords(_normalize_opis_key(dev.opis))
        if not kws:
            other.append(dev)
            continue
        # Wiersz szczegółowy = brak jawnie podanej Ilości.
        # Wypełnione 'oznaczenie' NIE dyskwalifikuje (rozpisane sztuki mają tag).
        # Pomijamy sprawdzanie L.p., bo projektanci odruchowo przeciągają numerację
        # w Excelu w dół dla każdego wiersza, co blokowało deduplikację.
        wiersz_szczegolowy = not dev.ilosc_podana
        if wiersz_szczegolowy and dev.ilosc == 1 and not dev.ilosc_flaga:
            unnamed.append(dev)
        elif dev.lp and dev.ilosc > 1:
            aggregates.append(dev)
        else:
            other.append(dev)

    to_remove: set[int] = set()  # id() obiektów Device do usunięcia
    for agg in aggregates:
        agg_key = _normalize_opis_key(agg.opis)
        agg_areas = _area_codes(agg_key)
        agg_kws = _keywords_specific(agg_key)

        matches = []
        for u in unnamed:
            u_key = _normalize_opis_key(u.opis)
            u_areas = _area_codes(u_key)
            if agg_areas or u_areas:
                # Oba mają kod obszaru -> muszą się zgadzać DOKŁADNIE (silny sygnał).
                # Jeden ma kod, drugi nie -> traktujemy jako niedopasowanie (bezpieczniej
                # nie łączyć, niż fałszywie połączyć różne obszary instalacji).
                if agg_areas and u_areas and (agg_areas & u_areas):
                    matches.append(u)
                continue
            # Żadne nie ma kodu obszaru -> dopasowanie po specyficznych słowach kluczowych
            u_kws = _keywords_specific(u_key)
            if u_kws & agg_kws:
                matches.append(u)

        if len(matches) >= _DEDUP_MIN_MATCHES:
            removed_opisy = [m.opis for m in matches]
            # Wiersze z wypełnionym tagiem projektowym wymagają uważniejszej
            # weryfikacji - tag sugeruje, że projektant traktował je jako
            # konkretne, zidentyfikowane urządzenia.
            z_tagiem = [m.oznaczenie for m in matches if m.oznaczenie]
            for m in matches:
                to_remove.add(id(m))
            komunikat = (
                f"ℹ AUTOMATYCZNA DEDUPLIKACJA: pozycja „{agg.opis}” (Ilość={agg.ilosc}) "
                f"reprezentuje {len(matches)} wypisanych z nazwy wierszy poniżej "
                f"({', '.join(removed_opisy[:5])}{'...' if len(removed_opisy) > 5 else ''}) "
                f"- wykluczono je z osobnego liczenia, żeby nie dublować sygnałów I/O. "
                f"Jeśli to były jednak NIEZALEŻNE, dodatkowe urządzenia (nie egzemplarze "
                f"tego licznika) - zgłoś korektę, bilans byłby wtedy zaniżony."
            )
            if z_tagiem:
                komunikat += (
                    f" ⚠ UWAGA: {len(z_tagiem)} z usuniętych wierszy miało wypełniony "
                    f"tag projektowy ({', '.join(z_tagiem[:5])}"
                    f"{'...' if len(z_tagiem) > 5 else ''}) - to mocniejsza przesłanka, "
                    f"że mogą być osobnymi urządzeniami. ZWERYFIKUJ ten przypadek "
                    f"w dokumentacji źródłowej."
                )
            warnings.append(komunikat)
            agg.uwagi = (agg.uwagi + " | " if agg.uwagi else "") + (
                f"Zawiera {len(matches)} wypisanych z nazwy egzemplarzy: "
                f"{', '.join(removed_opisy[:5])}{'...' if len(removed_opisy) > 5 else ''}"
            )

    result = [d for d in devices if id(d) not in to_remove]
    return result, warnings


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

    raw_qty = rec.get("ilosc", None)
    qty, flag, qty_warn = _parse_quantity(raw_qty)
    dev.ilosc = qty
    dev.ilosc_flaga = flag
    # AI zwraca null, gdy komórka źródłowa była pusta (patrz ai_contract).
    # Ta sama semantyka co _has_quantity() w ścieżce Excel - to gwarantuje,
    # że deduplikacja zadziała identycznie niezależnie od źródła danych.
    dev.ilosc_podana = _has_quantity(raw_qty)
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

        devices, dedup_warnings = _deduplicate_hierarchical_aggregates(devices)
        global_warnings.extend(dedup_warnings)

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
