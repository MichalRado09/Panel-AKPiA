"""
tests/test_device_budget.py
============================
Testy modułu kosztorysu urządzeń obiektowych wybranych ręcznie przez
inżyniera (sekcja 1a w aplikacji). Patrz core/device_budget.py.

Uruchom: pytest tests/test_device_budget.py -v
"""

import pandas as pd

from core.parser import parse_devices, Device
from core.device_budget import (
    build_device_budget, device_key, format_device_budget, GRUPA_RABATOWA,
)
from core.budget import GRUPY_RABATOWE


KOLUMNY = [
    "L.p.", "Układ", "Urządzenie (ozn. proj.)", "Typ / Opis odbiornika",
    "Ilość", "Sygnał Analogowy (4-20mA / 0-10V)", "Sygnał Cyfrowy (DI/DO)",
]


def test_grupa_rabatowa_jest_zarejestrowana():
    """Nowa grupa musi być w GRUPY_RABATOWE, inaczej suwak rabatu się nie pojawi w UI."""
    assert GRUPA_RABATOWA in GRUPY_RABATOWE


def test_pusta_lista_urzadzen():
    sel = build_device_budget([], set(), rabaty={})
    assert sel.items == []
    assert sel.suma_katalogowa == 0
    assert sel.suma_netto == 0


def test_nic_niezaznaczone_daje_pusty_kosztorys():
    df = pd.DataFrame([
        (1, "A", "P1", "Pompa", 1, "-", "Start, Praca, Awaria"),
    ], columns=KOLUMNY)
    dev, _ = parse_devices(df)
    sel = build_device_budget(dev, set(), rabaty={})
    assert sel.items == []


def test_zaznaczone_urzadzenie_trafia_do_kosztorysu():
    df = pd.DataFrame([
        (1, "A", "TIC-1", "Przetwornik temperatury", 1, "4-20mA", "-"),
        (2, "A", "P1", "Pompa", 1, "-", "Start, Praca, Awaria"),
    ], columns=KOLUMNY)
    dev, _ = parse_devices(df)
    klucz_przetwornika = device_key(dev[0], 0)

    sel = build_device_budget(dev, {klucz_przetwornika}, rabaty={})
    assert len(sel.items) == 1
    assert sel.items[0].opis == "Przetwornik temperatury"
    assert sel.items[0].oznaczenie == "TIC-1"


def test_bez_cennika_pozycja_ma_brak_ceny():
    """Zgodnie z decyzją: na start bez cennika, pozycja pokazuje 'BRAK CENY'."""
    df = pd.DataFrame([
        (1, "A", "TIC-1", "Przetwornik temperatury", 1, "4-20mA", "-"),
    ], columns=KOLUMNY)
    dev, _ = parse_devices(df)
    klucz = device_key(dev[0], 0)

    sel = build_device_budget(dev, {klucz}, rabaty={}, cennik_file="plik_ktory_nie_istnieje.csv")
    assert sel.items[0].cena_katalogowa is None
    assert sel.items[0].wartosc_netto is None
    assert len(sel.brak_ceny) == 1


def test_pozycja_zbiorcza_zachowuje_pelna_ilosc():
    """
    Zbiorcza pozycja '12 czujników' zaznaczona do wyceny musi mieć ilosc=12,
    NIE 1 - inaczej wycena zaniża liczbę fizycznych urządzeń, mimo że sam
    bilans I/O liczy je poprawnie (to jest inna droga w kodzie).
    """
    df = pd.DataFrame([
        (1, "A", "TI (różne)", "Czujniki temperatury w układzie", 12, "4-20mA", "-"),
        (None, "A", None, "Przetwornik temperatury", None, None, None),
        (None, "A", None, "Przetwornik temperatury", None, None, None),
    ], columns=KOLUMNY)
    dev, _ = parse_devices(df)
    assert len(dev) == 1  # zdeduplikowane do jednej pozycji zbiorczej

    klucz = device_key(dev[0], 0)
    sel = build_device_budget(dev, {klucz}, rabaty={})
    assert sel.items[0].ilosc == 12


def test_device_key_unikalny_mimo_identycznego_opisu():
    """
    REGRESJA: dwa niezależne urządzenia, oba bez L.p./oznaczenia, o
    identycznym opisie (rzadkie, ale możliwe) muszą dostać RÓŻNE klucze -
    inaczej zaznaczenie jednego w UI zaznaczałoby oba.
    """
    df = pd.DataFrame([
        (None, "A", None, "Zawór odcinający ręczny", None, "-", "-"),
        (None, "B", None, "Zawór odcinający ręczny", None, "-", "-"),
    ], columns=KOLUMNY)
    dev, _ = parse_devices(df)
    assert len(dev) == 2  # oba przetrwały (brak pozycji zbiorczej do dopasowania)

    klucze = [device_key(d, i) for i, d in enumerate(dev)]
    assert len(klucze) == len(set(klucze)), "Kolizja kluczy dla identycznych opisów"

    # Zaznaczenie TYLKO pierwszego nie może objąć drugiego
    sel = build_device_budget(dev, {klucze[0]}, rabaty={})
    assert len(sel.items) == 1


def test_device_key_stabilny_miedzy_wywolaniami():
    """Ten sam plik parsowany dwa razy musi dać identyczne klucze - inaczej
    zaznaczenia w UI 'gubią się' przy ponownym przeliczeniu w tej samej sesji."""
    df = pd.DataFrame([
        (1, "A", "TIC-1", "Przetwornik temperatury", 1, "4-20mA", "-"),
        (2, "A", "P1", "Pompa", 1, "-", "Start, Praca, Awaria"),
    ], columns=KOLUMNY)
    dev1, _ = parse_devices(df)
    dev2, _ = parse_devices(df)

    klucze1 = [device_key(d, i) for i, d in enumerate(dev1)]
    klucze2 = [device_key(d, i) for i, d in enumerate(dev2)]
    assert klucze1 == klucze2


def test_format_device_budget_pusty():
    from core.device_budget import DeviceBudgetSelection
    tekst = format_device_budget(DeviceBudgetSelection())
    assert "brak zaznaczonych pozycji" in tekst.lower()


def test_rabat_stosowany_do_grupy_akpia():
    df = pd.DataFrame([
        (1, "A", "TIC-1", "Przetwornik temperatury", 2, "4-20mA", "-"),
    ], columns=KOLUMNY)
    dev, _ = parse_devices(df)
    klucz = device_key(dev[0], 0)

    # Bez cennika nie ma ceny do przemnożenia, ale rabat musi się zapisać
    # w pozycji (do wyświetlenia), nawet gdy cena_katalogowa=None.
    sel = build_device_budget(dev, {klucz}, rabaty={GRUPA_RABATOWA: 15},
                               cennik_file="plik_ktory_nie_istnieje.csv")
    assert sel.items[0].rabat_pct == 15
    assert sel.items[0].cena_netto_jed is None  # brak ceny bazowej -> nic do przemnożenia


def test_price_override_ma_pierwszenstwo_przed_cennikiem():
    """
    Ręcznie wpisana cena (price_overrides) ma pierwszeństwo przed cennikiem —
    to jawna decyzja inżyniera dla konkretnego urządzenia w bieżącej sesji,
    używana gdy cennik.csv jeszcze nie ma wpisu dla urządzeń obiektowych
    (typowy stan - patrz komentarz w core/device_budget.py).
    """
    df = pd.DataFrame([
        (1, "A", "TIC-1", "Przetwornik temperatury", 2, "4-20mA", "-"),
    ], columns=KOLUMNY)
    dev, _ = parse_devices(df)
    klucz = device_key(dev[0], 0)

    sel = build_device_budget(
        dev, {klucz}, rabaty={}, cennik_file="plik_ktory_nie_istnieje.csv",
        price_overrides={klucz: 250.0},
    )
    assert sel.items[0].cena_katalogowa == 250.0
    assert sel.items[0].wartosc_netto == 500.0  # 250 * ilosc(2), bez rabatu
    assert len(sel.brak_ceny) == 0
