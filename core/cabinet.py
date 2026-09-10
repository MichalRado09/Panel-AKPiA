"""
core/cabinet.py
===============
Dobór wyposażenia szafy sterowniczej AKPiA (+SAKG): złączki, przekaźniki
interfejsowe, bilans prądowy 24V DC, dobór zasilacza.

ŹRÓDŁO REGUŁ — realny BOM szafy DPK2 Wujek (PT_E053202_Lista_materiałów):
  bilans I/O: 80 DI, 24 DO, 56 AI, 16 AO
  BOM:  PT 2,5 = 235 szt, PT 2,5-PE = 72, PT 4-HESI = 64,
        RIF-1 (przekaźnik interfejsowy) = 81

Wyprowadzone współczynniki (weryfikacja na Wujku):
  PT 2,5-PE  : 72 = AI+AO (72)        -> 1 na każdy sygnał analogowy (ekran)  [ZGODNE]
  RIF-1      : 81 ~ DI (80)           -> 1 przekaźnik na wejście cyfrowe      [~1.01]
  PT 4-HESI  : 64 ~ AI (56) + zapas   -> 1 na wejście analogowe (zasilanie)   [~1.14]
  PT 2,5     : 235 ~ DI*2 + (AI+AO)   -> 232, różnica +3                      [~1.01]

UWAGA — REGUŁY PRZYBLIŻONE:
Wyprowadzono z JEDNEGO projektu referencyjnego. Realny projektant dobiera
złączki także pod konkretną topologię (grupy potencjałowe, rezerwy na listwie).
Wyniki traktować jako oszacowanie do weryfikacji przez inżyniera, NIE jako
gotową listę zakupową. Współczynniki są konfigurowalne (patrz WSPOLCZYNNIKI).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


# Współczynniki złączek — wyprowadzone z BOM Wujka, konfigurowalne.
WSPOLCZYNNIKI = {
    "PT_2_5_na_DI": 2,        # 2 złączki standardowe na wejście cyfrowe
    "PT_2_5_na_ANALOG": 1,    # 1 złączka standardowa na sygnał analogowy
    "PT_2_5_PE_na_ANALOG": 1, # 1 złączka PE (ekran) na sygnał analogowy
    "PT_4_HESI_na_AI": 1,     # 1 złączka bezpiecznikowa na wejście analogowe
    "RIF_na_DI": 1,           # 1 przekaźnik interfejsowy na wejście cyfrowe
}

# Pobory prądu 24V DC [mA] — wartości katalogowe/typowe.
# Karty PLC: z kart katalogowych producentów. Przetworniki: typowe 4-20mA.
POBORY_MA = {
    "karta_DI": 100,      # typowy pobór karty wejść cyfrowych z magistrali
    "karta_DO": 100,
    "karta_AI": 150,      # karty analogowe pobierają więcej
    "karta_AO": 200,
    "CPU": 500,           # sterownik (CX9020 / CPU 1512SP)
    "przekaznik_RIF": 20, # cewka przekaźnika interfejsowego
    "przetwornik_AI": 25, # przetwornik 4-20mA (pętla prądowa)
}

# Dostępne zasilacze 24V DC [A] — typowy szereg
ZASILACZE = [5, 10, 20, 40]

# Numery katalogowe zasilaczy. Klucz = prąd [A], wartość = numer katalogowy.
#
# DLACZEGO NUMER, A NIE OPIS: pozycja trafia do kosztorysu i jest szukana
# w cenniku PO NUMERZE KATALOGOWYM. Dobór generował wcześniej opisową nazwę
# ("Zasilacz 24V DC 10A"), której w cenniku nie ma - są tam realne numery
# Mean Well - więc zasilacz NIGDY nie dostawał ceny i po cichu wypadał z sumy.
#
# Numery są wpisane wprost, a nie liczone wzorem. Poprzednia wersja skladała
# je jako "NDR-{prąd*10}-24", co dawało numery nieistniejące albo o złej mocy
# (NDR-50-24 to ok. 2 A, nie 5 A) - dobór ma podawać część, którą da się
# zamówić, albo nie podawać żadnej.
#
# Powyżej 10 A świadomie brak numeru: seria NDR kończy się na 480 W (20 A),
# a przy większych prądach realnie schodzi się na inną serię albo dwa
# zasilacze - to decyzja projektanta, nie wzór. Taka pozycja pokaże
# "BRAK CENY", co jest uczciwsze niż zmyślony numer.
ZASILACZE_KATALOG = {
    5:  "NDR-120-24",
    10: "NDR-240-24",
}

# Zapas mocy zasilacza (dobieramy z zapasem 30%)
ZAPAS_ZASILACZA = 1.30


# =============================================================================
# KOMPLETNA ROZDZIELNICA — obudowa, korytka, szyny, okablowanie, zabezpieczenia
# =============================================================================
# Dotąd dobór kończył się na złączkach, przekaźnikach i zasilaczu, więc
# „wyposażenie szafy" nie było wyceną rozdzielnicy — brakowało w niej samej
# obudowy, czyli zwykle najdroższej pojedynczej pozycji.
#
# STATUS TYCH REGUŁ — INNY NIŻ ZŁĄCZEK. Współczynniki złączek wyżej są
# wyprowadzone z realnego BOM-u (Wujek) i zwalidowane na drugim projekcie
# (Niwka). Reguły poniżej takiej walidacji NIE MAJĄ: opierają się na
# szerokościach aparatów na szynie TH35 i na typowej praktyce montażowej.
# Dają rozsądny punkt wyjścia i pilnują, żeby pozycja nie wypadła z oferty —
# ale rozmiar obudowy i skład zabezpieczeń zatwierdza projektant.
# Wynik zawsze niesie ze sobą ostrzeżenie o tym rozróżnieniu.

# Szerokość zabudowy na szynie TH35 [mm]. Wartości katalogowe/typowe.
SZEROKOSCI_MM = {
    "PT 2,5": 5.2,
    "PT 2,5-PE": 5.2,
    "PT 4-HESI (5X20)": 6.2,
    "RIF": 6.2,
    "wylacznik_1P_N": 36.0,      # nadprądowy 1P+N = 2 moduły po 18 mm
    "rozlacznik_glowny": 54.0,   # rozłącznik główny 3P = 3 moduły
}

# Moduły sterownika — szerokość zależy od platformy, więc bierzemy wartości
# typowe: CPU/Embedded PC jest wyraźnie szerszy od pojedynczego terminala.
SZEROKOSC_CPU_MM = 110.0
SZEROKOSC_MODULU_MM = 20.0

# Zasilacz 24V DC: szerokość rośnie z prądem [A -> mm].
SZEROKOSC_ZASILACZA_MM = {5: 40.0, 10: 65.0, 20: 85.0, 40: 125.0}

# Zapas miejsca na szynie (na rezerwę montażową i rozdzielenie potencjałów).
ZAPAS_SZYNY = 1.20

# Katalog obudów: (nazwa, użyteczna długość szyny w rzędzie [mm], liczba rzędów).
# Pojemność = długość x rzędy. Kolejność od najmniejszej — dobieramy pierwszą,
# która się mieści.
OBUDOWY = [
    ("Obudowa wisząca 600x600x250",    500.0, 3),
    ("Obudowa wisząca 600x800x250",    500.0, 4),
    ("Obudowa wisząca 800x1000x300",   700.0, 5),
    ("Obudowa wisząca 800x1200x300",   700.0, 6),
    ("Obudowa stojąca 800x2000x400",   700.0, 10),
    ("Obudowa stojąca 1000x2000x400",  900.0, 10),
]

# Korytko grzebieniowe: prowadzone nad i pod każdym rzędem aparatury,
# stąd mnożnik 2 względem długości szyn.
KORYTKO_NA_SZYNE = 2.0

# Okablowanie wewnętrzne [m]:
#   - LgY 1,0 — od złączek do modułów, ~0,8 m na złączkę sygnałową,
#   - LgY 2,5 — obwody 230V i zasilacza, ryczałt na szafę.
PRZEWOD_LGY1_NA_ZLACZKE_M = 0.8
PRZEWOD_LGY25_RYCZALT_M = 15.0

# Wentylacja wymuszona od tego poboru wzwyż [A]; poniżej chłodzenie naturalne.
PROG_WENTYLACJI_A = 5.0


@dataclass
class CabinetItem:
    """Pozycja wyposażenia szafy."""
    nr_katalogowy: str
    nazwa: str
    ilosc: int
    jednostka: str = "szt."
    grupa_rabatowa: str = "APARATURA"
    uwaga: str = ""


@dataclass
class CabinetSelection:
    """Wynik doboru wyposażenia szafy."""
    items: list[CabinetItem] = field(default_factory=list)

    # Zabudowa szafy
    dlugosc_szyn_mm: float = 0.0   # suma szerokości aparatów + zapas
    obudowa: str = ""              # dobrana obudowa
    obudowa_rzedow: int = 0        # ile rzędów szyny wykorzystuje

    # Bilans prądowy
    prad_karty_ma: int = 0
    prad_przekazniki_ma: int = 0
    prad_przetworniki_ma: int = 0
    prad_total_ma: int = 0
    prad_z_zapasem_a: float = 0.0
    zasilacz_a: int = 0

    warnings: list[str] = field(default_factory=list)

    @property
    def total_zlaczki(self) -> int:
        return sum(it.ilosc for it in self.items if "PT " in it.nr_katalogowy)


def select_cabinet(balance, plc_selection=None,
                   pelna_rozdzielnica: bool = True) -> CabinetSelection:
    """
    Dobiera wyposażenie szafy na podstawie bilansu I/O i (opcjonalnie) doboru PLC.

    balance: IOBalance z io_counter (używamy .reserved).
    plc_selection: PlcSelection z plc_selector — do bilansu prądowego kart.
    pelna_rozdzielnica: czy dołożyć obudowę, korytka, szynę, okablowanie
        wewnętrzne i zabezpieczenia (domyślnie tak). Wyłączenie zostawia samą
        aparaturę na szynie — przydatne, gdy rozdzielnicę wycenia ktoś inny
        albo szafa jest już na obiekcie i dokładamy tylko aparaturę.
    """
    sel = CabinetSelection()
    r = balance.reserved
    di, do, ai, ao = r.get("DI", 0), r.get("DO", 0), r.get("AI", 0), r.get("AO", 0)
    analog = ai + ao
    W = WSPOLCZYNNIKI

    # --- Złączki szynowe ---
    n_pt25 = di * W["PT_2_5_na_DI"] + analog * W["PT_2_5_na_ANALOG"]
    if n_pt25 > 0:
        sel.items.append(CabinetItem(
            "PT 2,5", "Złączka szynowa 2,5mm2 (Phoenix Contact)", n_pt25,
            uwaga=f"{W['PT_2_5_na_DI']}/DI + {W['PT_2_5_na_ANALOG']}/analog"
        ))

    n_pt25pe = analog * W["PT_2_5_PE_na_ANALOG"]
    if n_pt25pe > 0:
        sel.items.append(CabinetItem(
            "PT 2,5-PE", "Złączka szynowa ochronna PE 2,5mm2 (ekran sygnałów analogowych)",
            n_pt25pe, uwaga="1 na sygnał analogowy (ekran kabla)"
        ))

    n_hesi = ai * W["PT_4_HESI_na_AI"]
    if n_hesi > 0:
        sel.items.append(CabinetItem(
            "PT 4-HESI (5X20)", "Złączka bezpiecznikowa 4mm2 (zasilanie przetworników)",
            n_hesi, uwaga="1 na wejście analogowe"
        ))

    # --- Przekaźniki interfejsowe ---
    n_rif = di * W["RIF_na_DI"]
    if n_rif > 0:
        sel.items.append(CabinetItem(
            "RIF-1-RPT-LDP-24DC/2X21MS",
            "Przekaźnik interfejsowy 24V DC (separacja galwaniczna wejść)",
            n_rif, uwaga="1 na wejście cyfrowe"
        ))

    # --- Bilans prądowy 24V DC ---
    # Karty PLC (jeśli podano dobór)
    if plc_selection is not None:
        # CPU rozpoznawany po katalog_typ (klucz z CSV), NIE po dopasowaniu
        # tekstowym numeru katalogowego — numery CPU różnią się między
        # platformami (np. Siemens "6ES7512-1DM03-0AB0" nie zawiera ani "CX",
        # ani "CPU", ani "1512" — poprzednia heurystyka po prostu go pomijała,
        # więc pobór CPU Siemensa nigdy nie trafiał do bilansu prądowego).
        for it in plc_selection.items:
            if getattr(it, "katalog_typ", "") == "CPU":
                sel.prad_karty_ma += POBORY_MA["CPU"] * it.ilosc
        # Karty I/O — rozpoznaj typ po utilization
        for t, key in (("DI", "karta_DI"), ("DO", "karta_DO"),
                       ("AI", "karta_AI"), ("AO", "karta_AO")):
            u = plc_selection.utilization.get(t, {})
            sel.prad_karty_ma += POBORY_MA[key] * u.get("kart", 0)
    else:
        sel.warnings.append(
            "Brak doboru PLC — bilans prądowy pomija pobór kart sterownika."
        )

    sel.prad_przekazniki_ma = n_rif * POBORY_MA["przekaznik_RIF"]
    sel.prad_przetworniki_ma = ai * POBORY_MA["przetwornik_AI"]

    sel.prad_total_ma = (
        sel.prad_karty_ma + sel.prad_przekazniki_ma + sel.prad_przetworniki_ma
    )
    sel.prad_z_zapasem_a = round(sel.prad_total_ma * ZAPAS_ZASILACZA / 1000.0, 2)

    # Dobór zasilacza
    for z in ZASILACZE:
        if sel.prad_z_zapasem_a <= z:
            sel.zasilacz_a = z
            break
    if sel.zasilacz_a == 0:
        sel.zasilacz_a = ZASILACZE[-1]
        sel.warnings.append(
            f"Zapotrzebowanie {sel.prad_z_zapasem_a}A przekracza największy "
            f"zasilacz {ZASILACZE[-1]}A — rozważ podział na dwa zasilacze."
        )

    if sel.zasilacz_a > 0:
        nr_zasilacza = ZASILACZE_KATALOG.get(sel.zasilacz_a)
        sel.items.append(CabinetItem(
            nr_zasilacza or f"Zasilacz 24V DC {sel.zasilacz_a}A",
            f"Zasilacz 24V DC {sel.zasilacz_a}A"
            + (f" (Mean Well {nr_zasilacza})" if nr_zasilacza else ""),
            1, uwaga=f"bilans {sel.prad_z_zapasem_a}A (z zapasem 30%)"
        ))
        if not nr_zasilacza:
            sel.warnings.append(
                f"Zasilacz {sel.zasilacz_a}A bez numeru katalogowego - przy tym "
                f"prądzie dobór zależy od serii i od tego, czy nie lepiej podzielić "
                f"zasilanie na dwie sztuki. Wybierz model i dopisz go do cennika, "
                f"inaczej pozycja zostanie bez ceny."
            )

    sel.warnings.append(
        "Reguły doboru złączek wyprowadzone z projektu DPK2 Wujek — "
        "traktować jako oszacowanie, wymaga weryfikacji projektanta."
    )

    # Obudowa, korytka, szyna, okablowanie, zabezpieczenia — dopiero z nimi
    # to jest wycena rozdzielnicy, a nie sama lista aparatów.
    # WOŁANE NA KOŃCU, bo dobór obudowy potrzebuje już policzonego zasilacza
    # (jego szerokość wchodzi do zabudowy) i bilansu prądowego (wentylacja).
    if pelna_rozdzielnica:
        _dodaj_rozdzielnice(sel, plc_selection)

    return sel


def _szerokosc_aparatury_mm(sel: CabinetSelection, plc_selection) -> float:
    """
    Suma szerokości wszystkiego, co siedzi na szynie TH35 — złączek,
    przekaźników, zasilacza, modułów sterownika i zabezpieczeń.

    To jest jedyna wielkość, z której da się sensownie wyprowadzić rozmiar
    obudowy i metraż korytek: liczba sygnałów sama w sobie nic nie mówi
    o zabudowie, bo złączka analogowa z bezpiecznikiem zajmuje inaczej niż
    przekaźnik, a Embedded PC inaczej niż terminal wejść.
    """
    szer = 0.0
    for it in sel.items:
        if it.nr_katalogowy in SZEROKOSCI_MM:
            szer += SZEROKOSCI_MM[it.nr_katalogowy] * it.ilosc
        elif it.nr_katalogowy.startswith("RIF-1"):
            szer += SZEROKOSCI_MM["RIF"] * it.ilosc

    if sel.zasilacz_a:
        szer += SZEROKOSC_ZASILACZA_MM.get(sel.zasilacz_a, 90.0)

    if plc_selection is not None:
        for it in plc_selection.items:
            if getattr(it, "katalog_typ", "") == "CPU":
                szer += SZEROKOSC_CPU_MM * it.ilosc
            elif getattr(it, "typ", "") in ("io", "SERIAL", "montaz"):
                szer += SZEROKOSC_MODULU_MM * it.ilosc

    # Zabezpieczenia dokładane niżej — doliczamy je do zabudowy z góry,
    # żeby obudowa była dobrana do KOMPLETU, a nie do stanu sprzed ich dodania.
    szer += SZEROKOSCI_MM["rozlacznik_glowny"] + 2 * SZEROKOSCI_MM["wylacznik_1P_N"]

    return szer


def _dobierz_obudowe(dlugosc_mm: float) -> tuple[str, int, int]:
    """
    Najmniejsza obudowa z katalogu, w której zmieści się zabudowa.
    Zwraca (nazwa, liczba_potrzebnych_rzedow, pojemnosc_mm).

    Przekroczenie największej obudowy nie jest błędem — to sygnał, że
    rozdzielnica idzie na dwa pola. Zwracamy wtedy największą pozycję,
    a select_cabinet dokłada ostrzeżenie.
    """
    for nazwa, dlugosc_rzedu, rzedow in OBUDOWY:
        pojemnosc = dlugosc_rzedu * rzedow
        if dlugosc_mm <= pojemnosc:
            return nazwa, math.ceil(dlugosc_mm / dlugosc_rzedu), int(pojemnosc)
    nazwa, dlugosc_rzedu, rzedow = OBUDOWY[-1]
    return nazwa, rzedow, int(dlugosc_rzedu * rzedow)


def _dodaj_rozdzielnice(sel: CabinetSelection, plc_selection) -> None:
    """
    Dokłada to, co odróżnia listę aparatów od wyceny KOMPLETNEJ rozdzielnicy:
    obudowę, korytka, szynę, okablowanie wewnętrzne, zabezpieczenia
    i wyposażenie obudowy.

    Bez tych pozycji oferta była systematycznie zaniżona — i to nie o drobiazg,
    bo brakowało w niej samej obudowy.
    """
    szer_mm = _szerokosc_aparatury_mm(sel, plc_selection)
    sel.dlugosc_szyn_mm = round(szer_mm * ZAPAS_SZYNY, 1)

    nazwa, rzedow, pojemnosc = _dobierz_obudowe(sel.dlugosc_szyn_mm)
    sel.obudowa = nazwa
    sel.obudowa_rzedow = rzedow
    sel.items.append(CabinetItem(
        nazwa, "Obudowa rozdzielnicy z płytą montażową", 1,
        grupa_rabatowa="OBUDOWY",
        uwaga=f"zabudowa {sel.dlugosc_szyn_mm:.0f} mm (z zapasem "
              f"{int((ZAPAS_SZYNY - 1) * 100)}%) w {rzedow} rzędach",
    ))
    if sel.dlugosc_szyn_mm > pojemnosc:
        sel.warnings.append(
            f"Zabudowa {sel.dlugosc_szyn_mm:.0f} mm przekracza pojemność największej "
            f"obudowy w katalogu ({pojemnosc} mm) — rozdzielnica wymaga drugiego pola. "
            f"Wyceniono jedną obudowę; podziel projekt lub dopisz większą pozycję "
            f"do OBUDOWY w core/cabinet.py."
        )

    dlugosc_m = sel.dlugosc_szyn_mm / 1000.0

    sel.items.append(CabinetItem(
        "Szyna montażowa TH35", "Szyna montażowa TH35 (DIN)",
        math.ceil(dlugosc_m), jednostka="m", grupa_rabatowa="OBUDOWY",
        uwaga="długość zabudowy",
    ))
    sel.items.append(CabinetItem(
        "Korytko grzebieniowe 40x60", "Korytko grzebieniowe (prowadzenie przewodów)",
        math.ceil(dlugosc_m * KORYTKO_NA_SZYNE), jednostka="m", grupa_rabatowa="OBUDOWY",
        uwaga=f"{KORYTKO_NA_SZYNE:g}x długość szyn (korytko nad i pod rzędem)",
    ))

    # --- Okablowanie wewnętrzne ---
    n_zlaczek = sel.total_zlaczki
    if n_zlaczek > 0:
        sel.items.append(CabinetItem(
            "Przewód LgY 1,0 mm2", "Przewód wewnętrzny LgY 1,0 mm2 (obwody sygnałowe)",
            math.ceil(n_zlaczek * PRZEWOD_LGY1_NA_ZLACZKE_M), jednostka="m",
            grupa_rabatowa="KABLE",
            uwaga=f"{PRZEWOD_LGY1_NA_ZLACZKE_M} m na złączkę",
        ))
    sel.items.append(CabinetItem(
        "Przewód LgY 2,5 mm2", "Przewód wewnętrzny LgY 2,5 mm2 (obwody 230V i zasilacza)",
        int(PRZEWOD_LGY25_RYCZALT_M), jednostka="m", grupa_rabatowa="KABLE",
        uwaga="ryczałt na szafę",
    ))

    # --- Zabezpieczenia ---
    sel.items.append(CabinetItem(
        "Rozłącznik główny 3P 25A", "Rozłącznik główny z napędem drzwiowym", 1,
        uwaga="1 na rozdzielnicę",
    ))
    sel.items.append(CabinetItem(
        "Wyłącznik nadprądowy B10 1P+N", "Zabezpieczenie obwodu zasilacza 24V DC", 1,
        uwaga="obwód zasilacza",
    ))
    sel.items.append(CabinetItem(
        "Wyłącznik nadprądowy B10 1P+N (gniazdo/oświetlenie)",
        "Zabezpieczenie gniazda serwisowego i oświetlenia szafy", 1,
        uwaga="obwód pomocniczy",
    ))

    # --- Wyposażenie obudowy ---
    sel.items.append(CabinetItem(
        "Oświetlenie szafy z wyłącznikiem drzwiowym",
        "Oprawa oświetleniowa szafy z wyłącznikiem drzwiowym", 1,
    ))
    sel.items.append(CabinetItem(
        "Gniazdo serwisowe 230V", "Gniazdo serwisowe 230V na szynę TH35", 1,
    ))
    if sel.prad_z_zapasem_a >= PROG_WENTYLACJI_A:
        sel.items.append(CabinetItem(
            "Wentylator z filtrem + termostat",
            "Wentylacja wymuszona z termostatem", 1,
            uwaga=f"pobór {sel.prad_z_zapasem_a} A >= {PROG_WENTYLACJI_A} A",
        ))
    else:
        sel.warnings.append(
            f"Przyjęto chłodzenie naturalne (pobór {sel.prad_z_zapasem_a} A poniżej "
            f"progu {PROG_WENTYLACJI_A} A). Przy szafie w gorącym pomieszczeniu "
            f"dolicz wentylację ręcznie."
        )

    sel.warnings.append(
        "Obudowa, korytka, okablowanie i zabezpieczenia to OSZACOWANIE z sumy "
        "szerokości aparatów na szynie, a nie reguła zwalidowana na projekcie "
        "referencyjnym (w odróżnieniu od liczby złączek i przekaźników). "
        "Rozmiar obudowy i skład zabezpieczeń zatwierdza projektant."
    )


def format_cabinet(sel: CabinetSelection) -> str:
    lines = ["Wyposażenie szafy (+SAKG):"]
    for it in sel.items:
        lines.append(f"  {it.ilosc:>5}x {it.nr_katalogowy:<30} {it.nazwa[:45]}")
    lines.append("")
    lines.append("Bilans prądowy 24V DC:")
    lines.append(f"  Karty PLC:      {sel.prad_karty_ma:>6} mA")
    lines.append(f"  Przekaźniki:    {sel.prad_przekazniki_ma:>6} mA")
    lines.append(f"  Przetworniki:   {sel.prad_przetworniki_ma:>6} mA")
    lines.append(f"  RAZEM:          {sel.prad_total_ma:>6} mA")
    lines.append(f"  Z zapasem 30%:  {sel.prad_z_zapasem_a:>6} A")
    lines.append(f"  -> Zasilacz:    {sel.zasilacz_a} A")
    if sel.warnings:
        lines.append("")
        for w in sel.warnings:
            lines.append(f"  ! {w}")
    return "\n".join(lines)
