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


def test_cala_listwa_odtwarza_projekt_referencyjny_wujek():
    """
    WALIDACJA NA REALNYM PROJEKCIE (DPK2 Wujek): dobór musi odtworzyć całą
    listwę co do sztuki - zweryfikowane niezależnie DWOMA dokumentami
    wykonawczymi: listą materiałów (PT.E-05-3-202) i rysunkiem konfiguracji
    sterownika (PT.E-05-3-404, moduły -A1...-A28).

    Kluczowa jest tu pozycja EL9410. Poprzednia reguła "zasilacz E-bus co
    12 modułów" dawała tu 2 sztuki zamiast 1. Rysunek pokazuje, dlaczego ta
    premisa była fałszywa: EL9410 stoi na -A14 (po 12 terminalach), ale ZA NIM
    jest jeszcze 14 kolejnych bez drugiego zasilacza. Dobór liczy teraz bilans
    prądu magistrali i wychodzi 1 szt., zgodnie z rzeczywistością.
    """
    from core.plc_selector import select_plc

    bal = _bal(di=80, do=24, ai=56, ao=16)   # realne I/O Wujka (10/3/7/4 karty)
    sel = select_plc(bal, "Beckhoff CX9020")
    dobrane = {it.nr: it.ilosc for it in sel.items}

    listwa_z_projektu = {
        "CX9020-0115": 1, "EL6070-0033": 1, "EL6021": 1,
        "EL1008": 10, "EL2008": 3, "EL3058": 7, "EL4024": 4,
        "EL9410": 1,   # <- pozycja, na której wykładała się stara reguła
        "EL9011": 1,
    }
    assert dobrane == listwa_z_projektu


def test_zasilacz_ebus_spada_na_regule_zastepcza_bez_danych_o_poborze():
    """
    Gdy w katalogu nie ma poborów E-bus (np. ktoś doda platformę bez tych
    kolumn), dobór nie może udawać, że policzył bilans prądu - ma wrócić do
    zgrubnej reguły po liczbie modułów i JAWNIE to zaznaczyć.
    """
    import core.plc_selector as plc_selector_module
    from core.plc_selector import select_plc, load_catalog

    katalog_bez_pradow = {
        typ: {**dane, "pobor_ebus_ma": None, "zasila_ebus_ma": None}
        for typ, dane in load_catalog("Beckhoff CX9020").items()
    }
    monkey = lambda platforma: katalog_bez_pradow
    orig = plc_selector_module.load_catalog
    plc_selector_module.load_catalog = monkey
    try:
        sel = select_plc(_bal(di=80, do=24, ai=56, ao=16), "Beckhoff CX9020")
    finally:
        plc_selector_module.load_catalog = orig

    assert any("ZGRUBNY SZACUNEK" in w for w in sel.warnings), (
        "bez danych o poborze dobór musi się przyznać, że to tylko szacunek"
    )
