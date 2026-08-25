"""
tests/test_extraction_diff.py
==============================
Testy core/extraction_diff.py (porównanie ścieżki OFFLINE vs AI — sekcja
"⚖ Policz + zweryfikuj przez AI" w UI). Wcześniej moduł nie miał ŻADNEGO
testu, mimo nietrywialnej logiki normalizacji L.p. i dopasowania urządzeń.

Uruchom: pytest tests/test_extraction_diff.py -v
"""

from core.parser import Device
from core.extraction_diff import compare_extractions, _normalize_lp


def _dev(lp="", oznaczenie="", opis="", ilosc=1, sygnaly=None):
    d = Device()
    d.lp = lp
    d.oznaczenie = oznaczenie
    d.opis = opis
    d.ilosc = ilosc
    d.sygnaly = sygnaly or []
    return d


def test_identyczne_listy_dają_identyczne():
    devs = [_dev(lp="1", oznaczenie="P1", opis="Pompa",
                 sygnaly=[{"typ": "DO", "nazwa": "Start"}])]
    diff = compare_extractions(devs, devs)
    assert diff.identyczne
    assert diff.balans_delta == {"DI": 0, "DO": 0, "AI": 0, "AO": 0}
    assert not diff.tylko_w_offline
    assert not diff.tylko_w_ai


def test_normalize_lp_ujednolica_zapis_pandas_float_vs_ai_string():
    """
    REGRESJA: pandas czasem wczytuje L.p. jako float64 ("1.0"), a AI zgodnie
    z kontraktem zwraca czysty tekst "1" - bez normalizacji KAŻDE porównanie
    zgłaszałoby fałszywą różnicę na 100% urządzeń mimo w pełni zgodnej
    ekstrakcji.
    """
    assert _normalize_lp("1.0") == "1"
    assert _normalize_lp("1") == "1"
    assert _normalize_lp("12.0") == "12"


def test_lp_jako_pandas_float_nie_generuje_falszywej_roznicy():
    off = [_dev(lp="1.0", oznaczenie="P1", opis="Pompa",
                sygnaly=[{"typ": "DO", "nazwa": "Start"}])]
    ai = [_dev(lp="1", oznaczenie="P1", opis="Pompa",
               sygnaly=[{"typ": "DO", "nazwa": "Start"}])]
    diff = compare_extractions(off, ai)
    assert diff.identyczne


def test_urzadzenie_tylko_w_offline():
    off = [_dev(lp="1", oznaczenie="P1", opis="Pompa", ilosc=2,
                sygnaly=[{"typ": "DO", "nazwa": "Start"}])]
    ai = []
    diff = compare_extractions(off, ai)
    assert not diff.identyczne
    assert len(diff.tylko_w_offline) == 1
    assert diff.tylko_w_offline[0].oznaczenie == "P1"
    assert diff.tylko_w_offline[0].ilosc == 2
    assert not diff.tylko_w_ai
    assert diff.balans_delta["DO"] == -2  # 1 sygnał DO x ilosc=2, offline ma go, AI nie


def test_urzadzenie_tylko_w_ai():
    off = []
    ai = [_dev(lp="1", oznaczenie="V1", opis="Zawór",
               sygnaly=[{"typ": "AI", "nazwa": "Pozycja"}])]
    diff = compare_extractions(off, ai)
    assert not diff.identyczne
    assert len(diff.tylko_w_ai) == 1
    assert diff.tylko_w_ai[0].oznaczenie == "V1"
    assert diff.balans_delta["AI"] == 1


def test_balans_delta_ignoruje_rezerwe():
    """
    Porównanie liczy na BAZOWYM bilansie (przed rezerwą) - różnica w
    ekstrakcji musi być widoczna 1:1, bez przemnożenia przez suwak rezerwy
    (to osobny, późniejszy krok, patrz komentarz w extraction_diff.py).
    """
    off = [_dev(lp="1", oznaczenie="P1", opis="Pompa", ilosc=1,
                sygnaly=[{"typ": "DI", "nazwa": "Praca"}])]
    ai = [_dev(lp="1", oznaczenie="P1", opis="Pompa", ilosc=1,
               sygnaly=[{"typ": "DI", "nazwa": "Praca"}, {"typ": "DI", "nazwa": "Awaria"}])]
    diff = compare_extractions(off, ai)
    assert diff.balans_delta["DI"] == 1  # +1 sygnał DI w AI, nie +30% ani nic innego


def test_liczba_urzadzen_liczona_niezaleznie_od_dopasowania():
    off = [_dev(lp="1", opis="A"), _dev(lp="2", opis="B")]
    ai = [_dev(lp="1", opis="A")]
    diff = compare_extractions(off, ai)
    assert diff.liczba_urzadzen_offline == 2
    assert diff.liczba_urzadzen_ai == 1
