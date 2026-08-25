"""
tests/test_zgodnosc_sciezek.py
===============================
Testy regresyjne pilnujące, że ścieżka OFFLINE (parse_devices na DataFrame)
i ścieżka AI (parse_ai_devices na rekordach JSON) dają IDENTYCZNY bilans I/O
dla tych samych danych wejściowych.

DLACZEGO TO ISTNIEJE:
Wykryto rozjazd ~30 sygnałów między raportem offline a raportem AI dla tego
samego pliku. Przyczyna: model językowy wykonywał WŁASNĄ deduplikację wierszy
(instrukcja w prompcie) i uzupełniał brakujące oznaczenia projektowe, przez co
do parsera trafiał INNY zestaw danych niż przy czytaniu Excela wprost.

Rdzeń deterministyczny jest poprawny - dla identycznych danych wejściowych
obie ścieżki liczą tak samo. Te testy pilnują, żeby kontrakt z AI (ai_contract)
nigdy więcej nie odebrał parserowi informacji, których potrzebuje do decyzji.

Uruchom: pytest tests/test_zgodnosc_sciezek.py -v
"""

import pandas as pd

from core.parser import parse_devices, parse_ai_devices
from core.io_counter import count_io, IO_TYPES


# --- Narzędzia pomocnicze -----------------------------------------------------

def _df_na_rekordy(df: pd.DataFrame) -> list[dict]:
    """
    Zamienia DataFrame na rekordy JSON DOKŁADNIE tak, jak model MA je zwrócić
    zgodnie z kontraktem w core/ai_contract.py: jeden wiersz = jeden rekord,
    bez pomijania, bez uzupełniania, pusta Ilość -> None.
    """
    def c(x):
        return "" if pd.isna(x) else str(x).strip()

    rekordy = []
    for _, row in df.iterrows():
        ilosc_raw = row.get("Ilość")
        rekordy.append({
            "lp": c(row.get("L.p.")).replace(".0", ""),
            "uklad": c(row.get("Układ")),
            "oznaczenie": c(row.get("Urządzenie (ozn. proj.)")),
            "opis": c(row.get("Typ / Opis odbiornika")),
            "ilosc": None if pd.isna(ilosc_raw) else int(float(ilosc_raw)),
            "analog": c(row.get("Sygnał Analogowy (4-20mA / 0-10V)")),
            "cyfrowy": c(row.get("Sygnał Cyfrowy (DI/DO)")),
            "komunikacja": c(row.get("Komunikacja")),
            "uwagi": c(row.get("Uwagi")),
        })
    return rekordy


def _bilanse(df: pd.DataFrame, rezerwa: int = 0):
    """Zwraca (bilans_offline, bilans_ai) dla tego samego DataFrame."""
    dev_off, _ = parse_devices(df)
    dev_ai, _ = parse_ai_devices(_df_na_rekordy(df))
    return count_io(dev_off, rezerwa), count_io(dev_ai, rezerwa)


# --- Fixture: zestawienie odwzorowujące realny plik --------------------------
# Zawiera wszystkie wzorce, które w praktyce powodowały rozjazd:
#   - pozycje zbiorcze z L.p. i Ilość > 1
#   - wiersze szczegółowe bez L.p. i bez Ilości (część z tagiem, część bez)
#   - kody obszaru instalacji rozróżniające niezależne urządzenia
#   - kolumny sygnałów puste (fallback na regułę typu) i wypełnione

KOLUMNY = [
    "L.p.", "Układ", "Urządzenie (ozn. proj.)", "Typ / Opis odbiornika",
    "Ilość", "Sygnał Analogowy (4-20mA / 0-10V)", "Sygnał Cyfrowy (DI/DO)",
    "Komunikacja", "Uwagi",
]

WIERSZE = [
    ("1", "RTO", "P1", "Pompa obiegowa glikolu - praca", 1,
     "Zadawanie prędkości (AO)", "Start, Praca, Awaria", "", ""),
    ("2", "RTO", "01PCB30 BR010 (M)", "Zawór z siłownikiem przy P1", 1,
     "-", "Otwórz/Zamknij, Krańcówki", "", ""),
    # Pozycja zbiorcza + wiersze szczegółowe BEZ tagu
    ("3", "AKPiA", "TI (różne)", "Czujniki temperatury w układzie", 12,
     "4-20mA lub RTD", "-", "", ""),
    (None, None, None, "Przetwornik temperatury", None, None, None, None, None),
    (None, None, None, "Przetwornik temperatury", None, None, None, None, None),
    # Wiersze szczegółowe Z tagiem projektowym - kiedyś wypadały z deduplikacji
    (None, None, "01PCB20 AT001", "Przetwornik temperatury", None, None, None, None, None),
    (None, None, "01PCB20 AT002", "Przetwornik temperatury", None, None, None, None, None),
    # Pozycja zbiorcza z kodem obszaru + egzemplarze tego samego obszaru
    ("4", "RTO", "D1-D4 (M)", "Siłownik z napędem 01PCB10 AA401", 4,
     "Opcja (AO)", "Opcja (DO)", "", ""),
    (None, None, None, "Siłownik z napędem 01PCB10 AA402", None, None, None, None, None),
    (None, None, None, "Siłownik z napędem 01PCB10 AA403", None, None, None, None, None),
    # INNY obszar instalacji - NIE powinien zostać zdeduplikowany
    (None, None, None, "Siłownik z napędem 01PCB40 AA401", None, None, None, None, None),
]


def _fixture_df() -> pd.DataFrame:
    return pd.DataFrame(WIERSZE, columns=KOLUMNY)


# --- Testy zgodności ----------------------------------------------------------

def test_bilans_identyczny_bez_rezerwy():
    """Rdzeń liczy tak samo niezależnie od źródła danych."""
    off, ai = _bilanse(_fixture_df(), rezerwa=0)
    assert off.base == ai.base, (
        f"Rozjazd bilansu bazowego: offline={off.base}, AI={ai.base}"
    )
    assert off.base_total == ai.base_total


def test_bilans_identyczny_z_rezerwa_30():
    """Rezerwa nie może wprowadzać rozbieżności między ścieżkami."""
    off, ai = _bilanse(_fixture_df(), rezerwa=30)
    assert off.reserved == ai.reserved
    assert off.reserved_total == ai.reserved_total


def test_zrodla_sygnalow_identyczne():
    """Ten sam podział na sygnały jawne vs wywnioskowane z typu urządzenia."""
    off, ai = _bilanse(_fixture_df(), rezerwa=0)
    assert off.source_counts == ai.source_counts


def test_liczba_urzadzen_identyczna():
    """Deduplikacja usuwa te same wiersze w obu ścieżkach."""
    df = _fixture_df()
    dev_off, _ = parse_devices(df)
    dev_ai, _ = parse_ai_devices(_df_na_rekordy(df))
    assert len(dev_off) == len(dev_ai)
    assert sorted(d.opis for d in dev_off) == sorted(d.opis for d in dev_ai)


def test_sygnaly_nierozstrzygniete_identyczne():
    """Pozycje BRAK DANYCH muszą być te same - trafiają do decyzji inżyniera."""
    off, ai = _bilanse(_fixture_df(), rezerwa=0)
    assert len(off.undecided) == len(ai.undecided)


# --- Testy samej reguły deduplikacji -----------------------------------------

def test_wiersz_z_tagiem_ale_bez_lp_jest_deduplikowany():
    """
    REGRESJA: '01PCB20 AT001' bez L.p. i bez Ilości to wiersz szczegółowy
    pozycji zbiorczej, mimo wypełnionego tagu projektowego. Wcześniejsze
    kryterium (not oznaczenie) przepuszczało go do osobnego liczenia,
    zawyżając bilans AI o liczbę takich wierszy.
    """
    dev, warns = parse_devices(_fixture_df())
    opisy_tagowane = [d.oznaczenie for d in dev]
    assert "01PCB20 AT001" not in opisy_tagowane
    assert "01PCB20 AT002" not in opisy_tagowane
    # Usunięcie musi być zaraportowane, nigdy po cichu
    assert any("DEDUPLIKACJA" in w for w in warns)


def test_usuniecie_wiersza_z_tagiem_jest_wyroznione_w_ostrzezeniu():
    """Wiersze z tagiem dostają mocniejsze ostrzeżenie do weryfikacji."""
    _, warns = parse_devices(_fixture_df())
    assert any("tag projektowy" in w for w in warns)


def test_inny_kod_obszaru_nie_jest_deduplikowany():
    """
    '01PCB40 AA401' to inny obszar instalacji niż zbiorcza pozycja
    '01PCB10 AA401' - musi przetrwać jako niezależne urządzenie.
    """
    dev, _ = parse_devices(_fixture_df())
    opisy = [d.opis for d in dev]
    assert "Siłownik z napędem 01PCB40 AA401" in opisy


def test_wiersz_z_lp_ale_bez_ilosci_nie_jest_deduplikowany():
    """
    REGRESJA: L.p. wypełniony, ale Ilość pusta (niedopatrzenie przy
    wypełnianiu arkusza) - to NIE jest wiersz szczegółowy pozycji zbiorczej,
    tylko osobne, samodzielnie ponumerowane urządzenie. Sprawdzanie WYŁĄCZNIE
    Ilości (bez L.p.) błędnie pochłaniało takie wiersze do niepowiązanego
    agregatu o podobnym opisie - zmierzone: 2 niezależne urządzenia zniknęły
    w pozycji "12 czujników" mimo własnej numeracji L.p.=2, L.p.=3.
    """
    df = pd.DataFrame([
        (1, "AKPiA", "TI (różne)", "Czujniki temperatury w układzie", 12,
         "4-20mA", "-", "", ""),
        (2, "RTO", "TI-201", "Przetwornik temperatury (osobny obwód)", None,
         "4-20mA", "-", "", ""),
        (3, "RTO", "TI-202", "Przetwornik temperatury (osobny obwód)", None,
         "4-20mA", "-", "", ""),
    ], columns=KOLUMNY)

    dev, _ = parse_devices(df)
    assert len(dev) == 3, (
        f"Oczekiwano 3 niezależne urządzenia (własny L.p.), dostano {len(dev)} "
        "- wiersze z L.p. zostały błędnie zdeduplikowane mimo braku podstaw."
    )
    bal = count_io(dev, reserve_percent=0)
    assert bal.base["AI"] == 14, (
        f"Oczekiwano AI=14 (12+1+1), dostano AI={bal.base['AI']} "
        "- niezależne urządzenia zostały wchłonięte przez agregat."
    )


def test_pozycja_zbiorcza_zachowuje_ilosc():
    """Deduplikacja usuwa egzemplarze, ale NIE zmienia Ilości pozycji zbiorczej."""
    dev, _ = parse_devices(_fixture_df())
    zbiorcza = next(d for d in dev if d.opis == "Czujniki temperatury w układzie")
    assert zbiorcza.ilosc == 12


# --- Testy kontraktu z AI -----------------------------------------------------

def test_ilosc_null_odrozniana_od_jawnej_jedynki():
    """
    Kontrakt: ilosc=None oznacza 'źródło nie podało', ilosc=1 oznacza
    'źródło podało jedną sztukę'. Rozróżnienie decyduje o deduplikacji.
    """
    bez_ilosci = [
        {"lp": "1", "opis": "Czujniki temperatury w układzie", "ilosc": 12,
         "analog": "4-20mA", "cyfrowy": "-", "oznaczenie": "TI"},
        {"lp": "", "opis": "Przetwornik temperatury", "ilosc": None,
         "analog": "", "cyfrowy": "", "oznaczenie": ""},
        {"lp": "", "opis": "Przetwornik temperatury", "ilosc": None,
         "analog": "", "cyfrowy": "", "oznaczenie": ""},
    ]
    z_jedynka = [dict(r, ilosc=1 if r["ilosc"] is None else r["ilosc"])
                 for r in bez_ilosci]

    dev_null, _ = parse_ai_devices(bez_ilosci)
    dev_jeden, _ = parse_ai_devices(z_jedynka)

    # Z null: wiersze szczegółowe zdeduplikowane -> zostaje sama pozycja zbiorcza
    assert len(dev_null) == 1
    # Z jawną jedynką: traktowane jako samodzielne urządzenia
    assert len(dev_jeden) == 3


def test_prompt_zabrania_deduplikacji_po_stronie_ai():
    """
    Strażnik kontraktu: instrukcja dla modelu NIE może kazać mu pomijać
    wierszy - to była pierwotna przyczyna rozjazdu wyników.
    """
    from core.ai_contract import build_extraction_prompt
    prompt = build_extraction_prompt()
    assert "JEDEN WIERSZ ŹRÓDŁOWY = JEDEN REKORD JSON" in prompt
    assert "NIE POMIJAJ" in prompt
    assert "NIGDY nie nadawaj własnych tagów" in prompt


def test_schemat_dopuszcza_null_w_ilosci():
    """Schemat odpowiedzi musi pozwalać modelowi zwrócić ilosc=null."""
    from core.ai_contract import build_response_schema
    schema = build_response_schema()
    ilosc = schema["properties"]["urzadzenia"]["items"]["properties"]["ilosc"]
    assert ilosc.get("nullable") is True
