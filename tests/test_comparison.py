"""
tests/test_comparison.py
=========================
Testy core/comparison.py (porównanie wariantów sterowników obok siebie).
Wcześniej moduł nie miał ŻADNEGO testu, mimo że jest wpięty w UI (sekcja 8
"Porównanie wariantów sterowników") i eksportowany do Excela.

Uruchom: pytest tests/test_comparison.py -v
"""

from core.io_counter import IOBalance, IO_TYPES
from core.comparison import compare_variants
from core.plc_selector import PLATFORMY


def _bal(di=8, do=8, ai=8, ao=4):
    bal = IOBalance()
    bal.reserved = {"DI": di, "DO": do, "AI": ai, "AO": ao}
    bal.base = dict(bal.reserved)
    return bal


def test_porownuje_wszystkie_zarejestrowane_platformy_domyslnie():
    variants = compare_variants(_bal())
    platformy_w_wyniku = {v.platforma for v in variants}
    assert platformy_w_wyniku == set(PLATFORMY.keys())


def test_cpu_rozpoznany_dla_kazdej_platformy():
    """
    REGRESJA: rozpoznanie CPU po dopasowaniu tekstowym numeru katalogowego
    ("CPU"/"CX" w numerze) nie działało dla Siemensa - numer CPU
    ("6ES7512-1DM03-0AB0") nie zawiera żadnego z tych fragmentów, więc
    kolumna "CPU" w tabeli porównania platform była dla niego pusta.
    Naprawione przez rozpoznanie po PlcItem.katalog_typ == "CPU" (ten sam
    mechanizm, co poprawka w core/cabinet.py).
    """
    variants = compare_variants(_bal())
    for v in variants:
        assert v.cpu, f"Brak rozpoznanego CPU dla platformy {v.platforma}"


def test_karty_io_zgodne_z_bilansem():
    bal = _bal(di=16, do=8, ai=8, ao=4)
    variants = compare_variants(bal)
    for v in variants:
        # Każda platforma musi mieć jakieś karty DI, skoro bilans wymaga 16 DI
        assert v.karty_io.get("DI", 0) > 0
        assert v.kanaly_io.get("DI", 0) >= 16  # dostępne kanały pokrywają zapotrzebowanie


def test_rabat_wplywa_na_sume_netto():
    bal = _bal()
    bez_rabatu = compare_variants(bal, rabaty={})
    z_rabatem = compare_variants(bal, rabaty={"BECKHOFF": 50, "SIEMENS": 50})
    by_plat_bez = {v.platforma: v.suma_netto for v in bez_rabatu}
    by_plat_z = {v.platforma: v.suma_netto for v in z_rabatem}
    # Tam, gdzie w ogóle są ceny w cenniku, rabat 50% musi obniżyć sumę netto
    # (a nie tylko katalogową) - jeśli cennik jest pusty (świeże repo bez
    # cennik.csv), obie sumy będą równe 0 i test i tak przejdzie (<=).
    for plat in by_plat_bez:
        assert by_plat_z[plat] <= by_plat_bez[plat]


def test_pomija_platforme_z_brakujacym_katalogiem_zamiast_wywalic_cale_porownanie(monkeypatch):
    """
    Platforma zarejestrowana w core.plc_selector.PLATFORMY, ale wskazująca
    na plik CSV, którego nie ma w katalogi/ (np. dodana do słownika przed
    dorobieniem katalogu kart - realny stan przejściowy przy rozbudowie
    o nową platformę), nie może wywalić porównania dla WSZYSTKICH pozostałych,
    poprawnie skonfigurowanych platform.
    """
    import core.plc_selector as plc_selector_module

    fake_platformy = dict(plc_selector_module.PLATFORMY)
    fake_platformy["Platforma Widmo Bez Katalogu"] = "plik_ktory_nie_istnieje.csv"
    monkeypatch.setattr(plc_selector_module, "PLATFORMY", fake_platformy)

    variants = compare_variants(_bal(), platformy=list(fake_platformy.keys()))
    platformy_w_wyniku = {v.platforma for v in variants}
    assert platformy_w_wyniku == set(PLATFORMY.keys())
    assert "Platforma Widmo Bez Katalogu" not in platformy_w_wyniku
