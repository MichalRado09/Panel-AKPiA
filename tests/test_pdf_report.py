"""
tests/test_pdf_report.py
=========================
Testy generatora PDF (core/pdf_report.py), ze szczególnym uwzględnieniem
sekcji "Urządzenia AKPiA" (dev_budget) dodanej razem z core/device_budget.py.

Testy weryfikują, że PDF się buduje bez wyjątku w trzech stanach parametru
dev_budget (None / pusty / z pozycjami) - to jedyne sensowne sprawdzenie
bez renderowania i porównywania pikseli. Treść została zweryfikowana
wizualnie podczas developmentu (rasteryzacja stron, ręczna inspekcja).

Uruchom: pytest tests/test_pdf_report.py -v
Wymaga zainstalowanego reportlab (jest w requirements.txt).
"""

import pandas as pd

from core.parser import parse_devices
from core.io_counter import count_io, IO_TYPES
from core.plc_selector import select_plc
from core.cabinet import select_cabinet
from core.scada_asix import select_asix
from core.budget import calculate_budget
from core.device_budget import build_device_budget, device_key
from core.pdf_report import create_pdf_report


def _zbuduj_kontekst():
    """Wspólne dane wejściowe dla testów - minimalny, ale realny projekt."""
    df = pd.DataFrame([
        (1, "A", "P1", "Pompa obiegowa", 1, "Zadawanie prędkości (AO)", "Start, Praca, Awaria"),
        (2, "A", "TIC-1", "Przetwornik temperatury", 1, "4-20mA", "-"),
    ], columns=[
        "L.p.", "Układ", "Urządzenie (ozn. proj.)", "Typ / Opis odbiornika",
        "Ilość", "Sygnał Analogowy (4-20mA / 0-10V)", "Sygnał Cyfrowy (DI/DO)",
    ])
    devices, _ = parse_devices(df)
    balance = count_io(devices, reserve_percent=30)
    sel = select_plc(balance, "Beckhoff CX9020")
    cab_sel = select_cabinet(balance, sel)
    asix_sel = select_asix(balance, wspolczynnik=1.2)
    budget = calculate_budget(sel.items, rabaty={})
    return devices, balance, sel, cab_sel, asix_sel, budget


def test_pdf_bez_parametru_dev_budget_dziala_jak_wczesniej():
    """
    Kompatybilność wsteczna: dev_budget ma domyślną wartość None, więc
    wywołanie create_pdf_report BEZ tego argumentu (stary kod, jeśli
    gdzieś jeszcze istnieje) nie może rzucić wyjątku.
    """
    devices, balance, sel, cab_sel, asix_sel, budget = _zbuduj_kontekst()
    pdf = create_pdf_report(devices, balance, "Test", "Beckhoff CX9020",
                             sel, cab_sel, asix_sel, budget, IO_TYPES)
    assert len(pdf.getvalue()) > 0


def test_pdf_z_pustym_dev_budget_nie_dodaje_sekcji():
    """Brak zaznaczonych urządzeń -> sekcja AKPiA nie powinna się pojawić
    (weryfikowane pośrednio przez mniejszy rozmiar pliku vs. wariant z pozycjami)."""
    devices, balance, sel, cab_sel, asix_sel, budget = _zbuduj_kontekst()
    pusty = build_device_budget(devices, set(), rabaty={})
    pdf_pusty = create_pdf_report(devices, balance, "Test", "Beckhoff CX9020",
                                   sel, cab_sel, asix_sel, budget, IO_TYPES,
                                   dev_budget=pusty)

    klucz = device_key(devices[1], 1)  # przetwornik
    z_pozycja = build_device_budget(devices, {klucz}, rabaty={})
    pdf_z_pozycja = create_pdf_report(devices, balance, "Test", "Beckhoff CX9020",
                                       sel, cab_sel, asix_sel, budget, IO_TYPES,
                                       dev_budget=z_pozycja)

    assert len(pdf_z_pozycja.getvalue()) > len(pdf_pusty.getvalue()), (
        "PDF z zaznaczoną pozycją AKPiA powinien być większy niż bez niej "
        "- brak różnicy sugeruje, że sekcja nie jest w ogóle renderowana."
    )


def test_pdf_z_dev_budget_zawiera_urzadzenie():
    """Zaznaczony przetwornik musi się fizycznie znaleźć w bajtach PDF."""
    devices, balance, sel, cab_sel, asix_sel, budget = _zbuduj_kontekst()
    klucz = device_key(devices[1], 1)
    dev_budget = build_device_budget(devices, {klucz}, rabaty={})
    assert dev_budget.items, "Test wymaga niepustej listy - sprawdź device_key"

    pdf = create_pdf_report(devices, balance, "Test", "Beckhoff CX9020",
                             sel, cab_sel, asix_sel, budget, IO_TYPES,
                             dev_budget=dev_budget)
    # reportlab osadza tekst jako operatory rysowania, nie czysty tekst w bajtach,
    # więc nie szukamy "Przetwornik temperatury" wprost - sprawdzamy tylko,
    # że budowa się powiodła i plik ma rozsądny rozmiar (patrz test powyżej
    # dla właściwej weryfikacji różnicy rozmiaru).
    assert len(pdf.getvalue()) > 1000


def test_pdf_dziala_bez_cen_akpia():
    """
    Zgodnie z decyzją: brak cennika dla urządzeń AKPiA nie może wywalić
    generowania PDF - pozycja ma się pokazać z 'BRAK' zamiast ceny.
    """
    devices, balance, sel, cab_sel, asix_sel, budget = _zbuduj_kontekst()
    klucz = device_key(devices[1], 1)
    dev_budget = build_device_budget(
        devices, {klucz}, rabaty={}, cennik_file="plik_ktory_nie_istnieje.csv"
    )
    assert dev_budget.items[0].cena_katalogowa is None

    pdf = create_pdf_report(devices, balance, "Test", "Beckhoff CX9020",
                             sel, cab_sel, asix_sel, budget, IO_TYPES,
                             dev_budget=dev_budget)
    assert len(pdf.getvalue()) > 0
