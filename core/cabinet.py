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

# Zapas mocy zasilacza (dobieramy z zapasem 30%)
ZAPAS_ZASILACZA = 1.30


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


def select_cabinet(balance, plc_selection=None) -> CabinetSelection:
    """
    Dobiera wyposażenie szafy na podstawie bilansu I/O i (opcjonalnie) doboru PLC.

    balance: IOBalance z io_counter (używamy .reserved).
    plc_selection: PlcSelection z plc_selector — do bilansu prądowego kart.
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
        sel.items.append(CabinetItem(
            f"Zasilacz 24V DC {sel.zasilacz_a}A",
            f"Zasilacz 24V DC {sel.zasilacz_a}A (np. Mean Well NDR-{sel.zasilacz_a*10}-24)",
            1, uwaga=f"bilans {sel.prad_z_zapasem_a}A (z zapasem 30%)"
        ))

    sel.warnings.append(
        "Reguły doboru złączek wyprowadzone z projektu DPK2 Wujek — "
        "traktować jako oszacowanie, wymaga weryfikacji projektanta."
    )

    return sel


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
