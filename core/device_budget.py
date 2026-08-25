"""
core/device_budget.py
======================
Kosztorys URZĄDZEŃ OBIEKTOWYCH (z listy Device, ekstrahowanej z Excela/PDF)
oznaczonych przez inżyniera jako "wchodzące w zakres wyceny AKPiA".

DLACZEGO TO JEST OSOBNY MODUŁ, NIE CZĘŚĆ budget.py:
budget.py liczy koszt SPRZĘTU STEROWNICZEGO (karty PLC, materiały szafowe,
SCADA, HMI) - pozycje, które program SAM dobiera na podstawie bilansu I/O.
Ten moduł liczy koszt URZĄDZEŃ PROCESOWYCH (pompy, zawory, przetworniki) -
pozycje, które program NIGDY nie dobiera automatycznie, bo:
  1) większość z nich (pompy, zawory, siłowniki) fizycznie stoi na hali
     i jest dostarczana/wyceniana przez dział technologiczny, NIE AKPiA;
  2) tylko CZĘŚĆ urządzeń obiektowych (typowo: przetworniki pomiarowe)
     wchodzi w zakres dostawy/wyceny automatyki - i to, które konkretnie,
     zależy od projektu i umowy, nie da się tego wywnioskować z opisu.

Dlatego wybór jest RĘCZNY, per-urządzenie (checkbox w UI) - zgodnie z zasadą
HITL stosowaną już w core/hmi.py. AI ani parser NIC tu nie decydują.

Ceny: na start bez cennika (jak przy PLC bez wypełnionego cennik.csv) -
pozycja trafia do BOM z ilością, cena pokazuje się jako "BRAK CENY" do
uzupełnienia. Struktura cennika jest gotowa na rozszerzenie w przyszłości
(nowa kategoria w cennik.csv, np. "AKPiA-URZADZENIA"), ale to nie jest
wymagane do działania - moduł działa od razu, tylko bez cen.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .budget import load_cennik, _round_netto

# Grupa rabatowa dla urządzeń obiektowych AKPiA - osobna od PLC/APARATURA/ASIX/KABLE,
# żeby dało się jej przypisać własny rabat w panelu bocznym niezależnie od reszty.
GRUPA_RABATOWA = "AKPIA_URZADZENIA"


@dataclass
class DeviceBudgetItem:
    """Jedna pozycja kosztorysu urządzeń obiektowych."""
    oznaczenie: str
    opis: str
    ilosc: int
    jednostka: str = "szt."
    cena_katalogowa: float | None = None
    grupa_rabatowa: str = GRUPA_RABATOWA
    rabat_pct: float = 0.0
    cena_netto_jed: float | None = None
    wartosc_netto: float | None = None


@dataclass
class DeviceBudgetSelection:
    """Wynik: pozycje wybranych urządzeń + podsumowanie."""
    items: list[DeviceBudgetItem] = field(default_factory=list)

    @property
    def suma_katalogowa(self) -> float:
        return sum((it.cena_katalogowa or 0) * it.ilosc for it in self.items)

    @property
    def suma_netto(self) -> float:
        return sum(it.wartosc_netto or 0 for it in self.items)

    @property
    def brak_ceny(self) -> list[DeviceBudgetItem]:
        return [it for it in self.items if it.cena_katalogowa is None]


def device_key(dev, index: int | None = None) -> str:
    """
    Stabilny identyfikator urządzenia do przechowania stanu checkboxa w UI
    między przeliczeniami (st.session_state). Oparty na polach, które parser
    ZAWSZE wypełnia deterministycznie dla tego samego wiersza źródłowego -
    NIE na obiekcie Device samym w sobie (ten jest tworzony na nowo przy
    każdym uruchomieniu analizy).

    index: pozycja urządzenia na LIŚCIE PO DEDUPLIKACJI (nie numer wiersza
    źródłowego). Wymagany jako tie-breaker: dwa NIEZALEŻNE urządzenia, oba
    bez L.p. i bez oznaczenia projektowego, o identycznym opisie (rzadkie,
    ale możliwe - np. dwa osobno stojące "Zawór odcinający ręczny" w różnych
    częściach instalacji, żadne niepowiązane z pozycją zbiorczą) dają bez
    indeksu IDENTYCZNY klucz - zaznaczenie jednego w UI zaznaczałoby oba.
    Lista devices ma w obrębie jednego uruchomienia analizy stabilną
    kolejność (parser jej nie sortuje), więc indeks jest bezpiecznym
    tie-breakerem tak długo, jak jest liczony na tej samej liście przy
    budowaniu UI i przy odczycie zaznaczeń - patrz app.py.
    """
    baza = f"{dev.lp}|{dev.oznaczenie}|{dev.opis}"
    return f"{index}|{baza}" if index is not None else baza


def build_device_budget(
    devices: list,
    selected_keys: set[str],
    rabaty: dict[str, float] | None = None,
    cennik_file: str = "cennik.csv",
) -> DeviceBudgetSelection:
    """
    Buduje kosztorys z urządzeń, których device_key(dev) jest w selected_keys.

    devices: lista Device (ta sama, co wyświetlana w tabeli wyników).
    selected_keys: zbiór kluczy urządzeń zaznaczonych przez inżyniera w UI
                   (np. st.session_state.wycena_osobna_keys).
    rabaty: dict {GRUPA: procent}. Rabat dla GRUPA_RABATOWA, jeśli podany.
    cennik_file: plik cennikowy - szuka pozycji po oznaczeniu/opisie; jeśli
                 nie ma dopasowania, pozycja idzie z cena_katalogowa=None
                 ("BRAK CENY"), identycznie jak niewycenione karty PLC.
    """
    if rabaty is None:
        rabaty = {}
    rabat = rabaty.get(GRUPA_RABATOWA, 0.0)
    cennik = load_cennik(cennik_file)

    sel = DeviceBudgetSelection()
    for i, dev in enumerate(devices):
        if device_key(dev, i) not in selected_keys:
            continue

        # Cennik urządzeń obiektowych może być kluczowany po oznaczeniu
        # projektowym (tag) - jeśli inżynier kiedyś go uzupełni. Na razie
        # w cenniku takich wpisów nie ma, więc to zawsze da None ("BRAK").
        wpis = cennik.get(dev.oznaczenie) or cennik.get(dev.opis)
        cena_kat = wpis.get("cena") if wpis else None

        cena_netto = None
        wartosc = None
        if cena_kat is not None:
            cena_netto = _round_netto(cena_kat, rabat)
            wartosc = round(cena_netto * dev.ilosc, 2)

        sel.items.append(DeviceBudgetItem(
            oznaczenie=dev.oznaczenie or "-",
            opis=dev.opis,
            ilosc=dev.ilosc,
            cena_katalogowa=cena_kat,
            rabat_pct=rabat,
            cena_netto_jed=cena_netto,
            wartosc_netto=wartosc,
        ))

    return sel


def format_device_budget(sel: DeviceBudgetSelection) -> str:
    lines = ["Kosztorys urządzeń AKPiA (wybranych ręcznie):"]
    if not sel.items:
        lines.append("  (brak zaznaczonych pozycji)")
        return "\n".join(lines)
    lines.append(f"  {'Oznaczenie':<22} {'Opis':<32} {'Ilość':>5} {'Kat.':>10} {'Wartość':>12}")
    lines.append("  " + "-" * 85)
    for it in sel.items:
        kat = f"{it.cena_katalogowa:.2f}" if it.cena_katalogowa else "BRAK"
        val = f"{it.wartosc_netto:.2f}" if it.wartosc_netto else "-"
        lines.append(f"  {it.oznaczenie:<22} {it.opis[:32]:<32} {it.ilosc:>5} {kat:>10} {val:>12}")
    lines.append("  " + "-" * 85)
    lines.append(f"  Suma katalogowa: {sel.suma_katalogowa:>12.2f} PLN")
    lines.append(f"  Suma netto:      {sel.suma_netto:>12.2f} PLN")
    if sel.brak_ceny:
        lines.append(f"\n  UWAGA: {len(sel.brak_ceny)} pozycji BEZ CENY (uzupełnij cennik.csv)")
    return "\n".join(lines)
