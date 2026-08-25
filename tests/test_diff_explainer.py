"""
tests/test_diff_explainer.py
==============================
Testy core/diff_explainer.py - kontrakt trzeciego, opcjonalnego wywołania
AI wyjaśniającego przyczynę różnic wykrytych przez extraction_diff.py.

Te testy NIE wywołują prawdziwego API Gemini (brak kosztu, brak sieci
wymaganej) - testują wyłącznie budowę promptu, schemat i parsowanie
odpowiedzi, tak jak testy ai_contract.py testują parse_ai_json bez
prawdziwego wywołania modelu.

Uruchom: pytest tests/test_diff_explainer.py -v
"""

from core.extraction_diff import ExtractionDiff, DeviceDiffEntry
from core.diff_explainer import (
    build_diff_explanation_prompt, build_diff_explanation_schema,
    parse_diff_explanation, KATEGORIE_PRZYCZYN,
)


def _przykladowy_diff() -> ExtractionDiff:
    return ExtractionDiff(
        balans_delta={"DI": -1, "DO": 0, "AI": 0, "AO": -1},
        liczba_urzadzen_offline=14,
        liczba_urzadzen_ai=13,
        tylko_w_offline=[
            DeviceDiffEntry("tylko_offline", "01PCB40 AA401", "Zawór trójdrogowy", 1),
        ],
    )


def test_prompt_nie_zawiera_zadnej_liczby_do_wpisania_wprost():
    """
    Kontrakt: prompt komunikuje modelowi, że jego zadaniem jest KATEGORIA
    i uzasadnienie, nie liczby. Sprawdzamy, że instrukcja explicite tego
    zakazuje (analogicznie do 'CZEGO NIE ROBISZ' w ai_contract.py).
    """
    prompt = build_diff_explanation_prompt(_przykladowy_diff(), pdf_byl_uzyty=False)
    assert "JEDYNYM zadaniem" in prompt
    assert "wskazanie" in prompt.lower()


def test_prompt_wyklucza_kategorie_pdf_gdy_pdf_nieuzyty():
    prompt = build_diff_explanation_prompt(_przykladowy_diff(), pdf_byl_uzyty=False)
    assert "NIE używaj jej" in prompt
    assert "nie było żadnego PDF-a" in prompt


def test_prompt_dopuszcza_kategorie_pdf_gdy_pdf_uzyty():
    prompt = build_diff_explanation_prompt(_przykladowy_diff(), pdf_byl_uzyty=True)
    assert "NIE używaj jej" not in prompt
    assert "PDF" in prompt


def test_prompt_zawiera_wszystkie_roznice_z_diff():
    diff = _przykladowy_diff()
    prompt = build_diff_explanation_prompt(diff, pdf_byl_uzyty=False)
    assert "01PCB40 AA401" in prompt
    assert "Zawór trójdrogowy" in prompt


def test_schema_wymusza_zamkniety_zbior_kategorii():
    schema = build_diff_explanation_schema()
    enum = schema["properties"]["wyjasnienia"]["items"]["properties"]["kategoria"]["enum"]
    assert enum == KATEGORIE_PRZYCZYN
    assert "NIEUSTALONE" in enum  # musi być "bezpieczna" opcja przy braku pewności


def test_parse_odrzuca_kategorie_spoza_zbioru():
    """
    Nawet jeśli response_schema z enum ma zawieść (np. starsza wersja API,
    inny model), parser MUSI odfiltrować kategorię spoza zamkniętego zbioru
    - to jest druga linia obrony, nie tylko poleganie na response_schema.
    """
    json_ze_zla_kategoria = (
        '{"wyjasnienia":[{"oznaczenie":"X","opis":"Y",'
        '"kategoria":"COS_INNEGO_WYMYSLONEGO","uzasadnienie":"z"}],'
        '"podsumowanie":"test"}'
    )
    wynik = parse_diff_explanation(json_ze_zla_kategoria)
    assert wynik["wyjasnienia"] == []


def test_parse_akceptuje_kategorie_ze_zbioru():
    json_ok = (
        '{"wyjasnienia":[{"oznaczenie":"X","opis":"Y",'
        '"kategoria":"NIEUSTALONE","uzasadnienie":"brak podstaw"}],'
        '"podsumowanie":"test"}'
    )
    wynik = parse_diff_explanation(json_ok)
    assert len(wynik["wyjasnienia"]) == 1
    assert wynik["wyjasnienia"][0]["kategoria"] == "NIEUSTALONE"


def test_parse_pusta_odpowiedz_rzuca_blad():
    """
    Pusta odpowiedź modelu musi jawnie rzucić błąd - app.py łapie go
    (try/except przy wywołaniu run_diff_explanation) i pokazuje surowe
    różnice bez wyjaśnienia, zamiast cichego None udającego "brak przyczyn".
    """
    try:
        parse_diff_explanation("")
        assert False, "Powinno rzucić ValueError na pustą odpowiedź"
    except ValueError:
        pass


def test_parse_zly_json_rzuca_blad():
    try:
        parse_diff_explanation("to nie jest json { zepsuty")
        assert False, "Powinno rzucić ValueError na niepoprawny JSON"
    except ValueError:
        pass


def test_parse_markdown_fence_jest_usuwany():
    fenced = '```json\n{"wyjasnienia":[],"podsumowanie":"ok"}\n```'
    wynik = parse_diff_explanation(fenced)
    assert wynik["podsumowanie"] == "ok"


def test_parse_brak_klucza_wyjasnienia_rzuca_blad():
    try:
        parse_diff_explanation('{"podsumowanie": "brak listy"}')
        assert False, "Powinno rzucić ValueError przy braku klucza 'wyjasnienia'"
    except ValueError:
        pass
