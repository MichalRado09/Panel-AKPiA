"""
core/scada_asix.py
==================
Dobór pakietu licencyjnego SCADA ASIX oraz sugestia architektury.

Źródła reguł:
  - Cennik ASIX z dokumentacji handlowej 03/2026 (scalony do cennik.csv)
  - Reguła liczenia zmiennych procesowych z dokumentacji technicznej ASIX

Progi licencyjne (realne, z cennika — NIE MA pakietu 2048!):
  128, 256, 512, 1024, 4096, 8192, bez limitu

Metoda liczenia zmiennych procesowych:
  Każdy sygnał fizyczny (DI/DO/AI/AO) = 1 zmienna.
  Zmienne wirtualne niearchiwizowane NIE liczą się do limitu.
  Zmienne dwustanowe = 1 zmienna (nie 1/32).

Reguła uproszczona (do weryfikacji przez opiekuna):
  zmienne = suma sygnałów I/O (po rezerwie) × współczynnik
  Współczynnik 1.2 uwzględnia zmienne pomocnicze (alarmy, statusy, nastawy).
  Inżynier może go zmienić w panelu.

Sugestia architektury (na podstawie skali projektu):
  - do 256 zmiennych: stacja operatorska (1 stanowisko)
  - 257-1024 zmiennych: serwer + 1 terminal operatorski
  - 1025-4096 zmiennych: serwer + 2 terminale
  - >4096 zmiennych: serwer redundantny + terminale

ARCHITEKTURA JEST DECYZJĄ INŻYNIERA, NIE APLIKACJI.
Powyższe to wyłącznie punkt startowy liczony ze skali projektu. Realnie
architekturę narzuca KLIENT w wymaganiach (redundancja serwera, liczba
stanowisk, dostęp zdalny), a nie liczba zmiennych — dwa węzły o tej samej
liczbie sygnałów potrafią mieć zupełnie inne wymagania.

Dlatego select_asix() przyjmuje jawne parametry (architektura, redundancja,
n_terminale, dostęp zdalny), a sugestia służy tylko do wstępnego ustawienia
pól w panelu. Wcześniej ten docstring obiecywał, że „inżynier ZAWSZE może
nadpisać sugestię" — a funkcja nie miała ani jednego parametru, którym dałoby
się to zrobić: dobór był w 100% automatyczny i nie do ruszenia z interfejsu.

Licencje dostępu zdalnego (RDS / WWW) składamy z pozycji cennika:
  RDS: 1x terminal operatorski + (n-1)x AsRDSCAL
  WWW: 1x As4www+1CAL + (n-1)x klient pełny (As4www1CAL) lub Lite
Zasady łączenia tych pozycji w pakiety bywają u producenta bardziej złożone
(patrz ostrzeżenie dokładane do wyniku) — traktować jako punkt wyjścia do
zapytania ofertowego u ASKOM, nie jako gotowe zamówienie.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .budget import load_cennik

# Realne progi licencyjne ASIX (z cennika — sprawdzone, NIE MA 2048!)
PROGI = [128, 256, 512, 1024, 4096, 8192]
PROG_BEZ_LIMITU = "BEZ_LIMITU"

# Architektury do wyboru w panelu. "auto" = zdaj się na sugestię ze skali.
ARCHITEKTURY = ("auto", "stacja", "serwer")

# Warianty dostępu zdalnego (klient czasem narzuca je wprost w wymaganiach).
DOSTEP_ZDALNY = ("brak", "RDS", "WWW")

# Numery katalogowe pozycji dostępu zdalnego — z cennika ASIX 03/2026.
NR_TERMINAL = "ASIX-WANLO + 1R PM"
NR_RDS_CAL = "AsRDSCAL"
NR_WWW_BAZA = "As4www+1CAL"
NR_WWW_CAL_PELNY = "As4www1CAL"
NR_WWW_CAL_LITE = "As4www1CAL-Lite"


@dataclass
class AsixItem:
    """Pozycja SCADA w kosztorysie."""
    nr_katalogowy: str
    nazwa: str
    ilosc: int = 1
    cena_katalogowa: float | None = None
    grupa_rabatowa: str = "ASIX"


@dataclass
class AsixSelection:
    """Wynik doboru SCADA ASIX."""
    # Obliczone zmienne
    zmienne_io: int = 0           # surowa suma I/O
    wspolczynnik: float = 1.2
    zmienne_obliczone: int = 0    # po współczynniku
    prog_licencyjny: int = 0      # dobrany próg
    prog_nazwa: str = ""

    # Sugestia architektury ze skali projektu (punkt startowy dla panelu)
    typ_licencji: str = ""        # "stacja" lub "serwer" — FAKTYCZNIE użyty
    sugestia_terminale: int = 0   # sugerowana liczba terminali
    sugestia_opis: str = ""       # tekstowy opis sugestii
    sugestia_typ: str = ""        # "stacja"/"serwer" wg samej skali

    # Architektura FAKTYCZNIE przyjęta do kosztorysu. Równa sugestii tylko
    # wtedy, gdy inżynier zostawił "auto" i niczego nie nadpisał.
    architektura_zrodlo: str = "auto"   # "auto" albo "inżynier"
    redundancja: bool = False
    n_terminale: int = 0
    dostep_zdalny: str = "brak"         # "brak" / "RDS" / "WWW"
    n_klientow_zdalnych: int = 0
    klient_www_lite: bool = False

    # Pozycje do kosztorysu
    items: list[AsixItem] = field(default_factory=list)

    warnings: list[str] = field(default_factory=list)


def _load_asix_prices() -> dict[str, dict]:
    """
    Ceny ASIX to te same wiersze cennika co reszta aplikacji - deleguje do
    budget.load_cennik() (ma już fallback na szablon bez cen i cache po
    mtime pliku), zamiast duplikować identyczną logikę wczytywania CSV.
    """
    return load_cennik()


def _find_prog(zmienne: int) -> tuple[int, str]:
    """Dobiera najbliższy wyższy próg licencyjny."""
    for p in PROGI:
        if zmienne <= p:
            return p, f"{p} zmiennych"
    return 0, "Bez limitu"


def _suggest_architecture(zmienne: int) -> tuple[str, int, str]:
    """
    Sugeruje architekturę SCADA na podstawie liczby zmiennych.
    Zwraca (typ_licencji, liczba_terminali, opis).
    """
    if zmienne <= 256:
        return "stacja", 0, (
            "Stacja operatorska (1 stanowisko). "
            "Wystarczająca dla małych węzłów do 256 zmiennych."
        )
    elif zmienne <= 1024:
        return "serwer", 1, (
            "Serwer operatorski + 1 terminal. "
            "Zalecane dla średnich instalacji (257-1024 zmiennych). "
            "Terminal umożliwia obsługę z dodatkowego stanowiska."
        )
    elif zmienne <= 4096:
        return "serwer", 2, (
            "Serwer operatorski + 2 terminale. "
            "Dla dużych instalacji (1025-4096 zmiennych). "
            "Umożliwia obsługę z wielu stanowisk jednocześnie."
        )
    else:
        return "serwer", 3, (
            "Serwer redundantny + 3 terminale. "
            "Dla bardzo dużych/krytycznych instalacji (>4096 zmiennych). "
            "Redundancja serwerów zapewnia ciągłość pracy SCADA."
        )


def _pozycja(prices: dict, nr: str, nazwa_zapasowa: str, ilosc: int = 1) -> AsixItem:
    """Pozycja kosztorysu z ceną z cennika (albo bez ceny, jeśli jej tam nie ma)."""
    p = prices.get(nr, {})
    return AsixItem(
        nr_katalogowy=nr,
        nazwa=p.get("nazwa", nazwa_zapasowa),
        ilosc=ilosc,
        cena_katalogowa=p.get("cena"),
    )


def _dodaj_dostep_zdalny(sel: AsixSelection, prices: dict) -> None:
    """
    Licencje dostępu zdalnego składane z pozycji cennika ASIX.

    RDS: 1x terminal operatorski + (n-1)x rozszerzenie o kolejnego klienta.
    WWW: 1x terminal WWW z pierwszym klientem + (n-1)x kolejny klient
         (pełny albo Lite - Lite jest wyraźnie tańszy i wystarcza tam, gdzie
         zdalny użytkownik ma tylko podglądać).

    Świadomie NIE udajemy, że znamy pełne zasady pakietowania u producenta -
    do wyniku trafia ostrzeżenie, żeby skład licencji potwierdzić u ASKOM.
    """
    n = sel.n_klientow_zdalnych
    if sel.dostep_zdalny == "brak" or n <= 0:
        return

    if sel.dostep_zdalny == "RDS":
        sel.items.append(_pozycja(prices, NR_TERMINAL, "Terminal operatorski (RDS)"))
        if n > 1:
            sel.items.append(_pozycja(
                prices, NR_RDS_CAL, "Rozszerzenie terminala RDS o kolejnego Klienta", n - 1
            ))
    elif sel.dostep_zdalny == "WWW":
        sel.items.append(_pozycja(prices, NR_WWW_BAZA, "Terminal WWW z 1 Klientem pełnym"))
        if n > 1:
            nr_cal = NR_WWW_CAL_LITE if sel.klient_www_lite else NR_WWW_CAL_PELNY
            opis = "Klient Lite" if sel.klient_www_lite else "Klient pełny"
            sel.items.append(_pozycja(prices, nr_cal, f"Terminal WWW +1 {opis}", n - 1))

    sel.warnings.append(
        f"Dostęp zdalny ({sel.dostep_zdalny}, {n} klient(ów)) złożony z pojedynczych "
        f"pozycji cennika. Zasady pakietowania licencji zdalnych bywają u producenta "
        f"bardziej złożone - potwierdź skład w zapytaniu do ASKOM przed wysłaniem oferty."
    )


def sugeruj_architekture(balance, wspolczynnik: float = 1.2) -> tuple[str, int, str]:
    """
    Sama sugestia ze skali projektu, BEZ doboru pozycji cennikowych.

    Potrzebna panelowi, żeby wypełnić pole „liczba terminali" sugerowaną
    wartością ZANIM powstanie widget. Bez tego pole startowałoby od zera
    i architektura serwerowa cicho gubiłaby terminale, które poprzednia
    (w pełni automatyczna) wersja doliczała.

    Zwraca (typ_licencji, liczba_terminali, opis) - to samo, co
    _suggest_architecture, tylko liczone wprost z bilansu.
    """
    zmienne = math.ceil(
        sum(balance.reserved.get(t, 0) for t in ("DI", "DO", "AI", "AO")) * wspolczynnik
    )
    return _suggest_architecture(zmienne)


def select_asix(
    balance,
    wspolczynnik: float = 1.2,
    architektura: str = "auto",
    redundancja: bool = False,
    n_terminale: int | None = None,
    dostep_zdalny: str = "brak",
    n_klientow_zdalnych: int = 0,
    klient_www_lite: bool = False,
) -> AsixSelection:
    """
    Dobiera pakiet SCADA ASIX na podstawie bilansu I/O ORAZ decyzji inżyniera
    co do architektury.

    balance: IOBalance z io_counter.
    wspolczynnik: mnożnik I/O → zmienne (1.2 = +20% na zmienne pomocnicze).

    Parametry architektury - wszystkie opcjonalne, domyślnie zachowują
    dotychczasowe zachowanie (pełne "auto" ze skali projektu):
      architektura:  "auto" (ze skali) | "stacja" | "serwer".
      redundancja:   serwer redundantny = 2 licencje serwera. Wymaga serwera;
                     przy architekturze "stacja" jest ignorowana z ostrzeżeniem,
                     bo redundancja stacji operatorskiej nie ma sensu.
      n_terminale:   liczba terminali operatorskich. None = użyj sugestii.
      dostep_zdalny: "brak" | "RDS" | "WWW" - stacje zdalne.
      n_klientow_zdalnych: liczba zdalnych klientów.
      klient_www_lite: dla WWW - klienci Lite (podgląd) zamiast pełnych.

    Skala projektu NIGDY nie wymusza architektury - liczy tylko PRÓG
    licencyjny (limit zmiennych), który zależy od liczby sygnałów i tego
    klient nie negocjuje.
    """
    if architektura not in ARCHITEKTURY:
        raise ValueError(f"Nieznana architektura: {architektura}. Dostępne: {ARCHITEKTURY}")
    if dostep_zdalny not in DOSTEP_ZDALNY:
        raise ValueError(f"Nieznany dostęp zdalny: {dostep_zdalny}. Dostępne: {DOSTEP_ZDALNY}")

    sel = AsixSelection(wspolczynnik=wspolczynnik)
    prices = _load_asix_prices()

    # 1. Oblicz zmienne
    sel.zmienne_io = sum(balance.reserved.get(t, 0) for t in ("DI", "DO", "AI", "AO"))
    sel.zmienne_obliczone = math.ceil(sel.zmienne_io * wspolczynnik)

    # 2. Dobierz próg licencyjny (to wynika z sygnałów, nie z decyzji klienta)
    sel.prog_licencyjny, sel.prog_nazwa = _find_prog(sel.zmienne_obliczone)

    # 3. Sugestia architektury ze skali - zawsze liczona, żeby panel miał
    #    czym wypełnić pola i żeby inżynier widział, od czego odszedł.
    sel.sugestia_typ, sel.sugestia_terminale, sel.sugestia_opis = (
        _suggest_architecture(sel.zmienne_obliczone)
    )

    # 4. Architektura FAKTYCZNIE przyjęta: wybór inżyniera ma pierwszeństwo.
    sel.typ_licencji = sel.sugestia_typ if architektura == "auto" else architektura
    sel.n_terminale = sel.sugestia_terminale if n_terminale is None else max(0, n_terminale)
    sel.redundancja = bool(redundancja)
    sel.dostep_zdalny = dostep_zdalny
    sel.n_klientow_zdalnych = max(0, n_klientow_zdalnych)
    sel.klient_www_lite = bool(klient_www_lite)

    reczne = (
        architektura != "auto"
        or redundancja
        or (n_terminale is not None and n_terminale != sel.sugestia_terminale)
        or dostep_zdalny != "brak"
    )
    sel.architektura_zrodlo = "inżynier" if reczne else "auto"

    # 5. Pozycje do kosztorysu
    if sel.typ_licencji == "stacja":
        if sel.redundancja:
            sel.redundancja = False
            sel.warnings.append(
                "Redundancja pominięta: dotyczy serwera, nie stacji operatorskiej. "
                "Wybierz architekturę „serwer”, jeśli klient wymaga redundancji."
            )
        nr = (f"ASIX-WA{sel.prog_licencyjny}W+1R PM" if sel.prog_licencyjny > 0
              else "ASIX-WANLW+1R PM")
        sel.items.append(_pozycja(
            prices, nr, f"Stacja operatorska, limit {sel.prog_nazwa}"
        ))
        if sel.n_terminale > 0:
            sel.warnings.append(
                f"Wybrano stację operatorską, a terminale ({sel.n_terminale}) obsługuje "
                f"serwer - terminale pominięto w kosztorysie."
            )
            sel.n_terminale = 0
    else:
        nr = (f"ASIX-WA{sel.prog_licencyjny}S+1R PM" if sel.prog_licencyjny > 0
              else "ASIX-WANLS+1R PM")
        # Redundancja = druga, taka sama licencja serwera.
        n_serwerow = 2 if sel.redundancja else 1
        sel.items.append(_pozycja(
            prices, nr, f"Serwer operatorski, limit {sel.prog_nazwa}", n_serwerow
        ))
        if sel.redundancja:
            sel.warnings.append(
                "Redundancja wyceniona jako 2 licencje serwera. Producent może mieć "
                "dedykowany pakiet redundancyjny w innej cenie - potwierdź u ASKOM."
            )

        if sel.n_terminale > 0:
            sel.items.append(_pozycja(
                prices, NR_TERMINAL, "Terminal operatorski", sel.n_terminale
            ))

    # 6. Stacje zdalne (niezależne od tego, czy baza to stacja czy serwer)
    _dodaj_dostep_zdalny(sel, prices)

    # Ostrzeżenia
    if sel.zmienne_obliczone > 8192:
        sel.warnings.append(
            "Liczba zmiennych przekracza 8192 — rozważ pakiet bez limitu "
            "lub podział na segmenty."
        )

    return sel


def opis_architektury(sel: AsixSelection) -> str:
    """
    Jednolinijkowy opis FAKTYCZNIE przyjętej architektury - używany zarówno
    w raporcie tekstowym, jak i w panelu, żeby oba mówiły to samo.
    """
    baza = "Stacja operatorska" if sel.typ_licencji == "stacja" else "Serwer operatorski"
    czesci = [baza + (" (redundantny, 2 licencje)" if sel.redundancja else "")]
    if sel.n_terminale > 0:
        czesci.append(f"{sel.n_terminale}x terminal operatorski")
    if sel.dostep_zdalny != "brak" and sel.n_klientow_zdalnych > 0:
        klient = " Lite" if (sel.dostep_zdalny == "WWW" and sel.klient_www_lite) else ""
        czesci.append(
            f"dostęp zdalny {sel.dostep_zdalny}: {sel.n_klientow_zdalnych}x klient{klient}"
        )
    zrodlo = "wybór inżyniera" if sel.architektura_zrodlo == "inżynier" else "auto ze skali"
    return " + ".join(czesci) + f"  [{zrodlo}]"


def format_asix(sel: AsixSelection) -> str:
    lines = [
        "Dobór SCADA ASIX:",
        f"  Sygnałów I/O (po rezerwie): {sel.zmienne_io}",
        f"  Współczynnik zmiennych: ×{sel.wspolczynnik}",
        f"  Zmiennych procesowych: {sel.zmienne_obliczone}",
        f"  Pakiet licencyjny: {sel.prog_nazwa}",
        f"",
        f"  Architektura: {opis_architektury(sel)}",
        f"  Sugestia ze skali projektu: {sel.sugestia_opis}",
        f"",
        f"  Pozycje:"
    ]
    for it in sel.items:
        cena = f"{it.cena_katalogowa:.2f} PLN" if it.cena_katalogowa else "BRAK CENY"
        lines.append(f"    {it.ilosc}x {it.nr_katalogowy} — {it.nazwa} ({cena})")
    if sel.warnings:
        lines.append("")
        for w in sel.warnings:
            lines.append(f"  ! {w}")
    return "\n".join(lines)
