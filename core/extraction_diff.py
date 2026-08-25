"""
core/extraction_diff.py
========================
Deterministyczne porównanie dwóch niezależnych ekstrakcji tej samej
dokumentacji: ścieżki OFFLINE (parse_devices na Excelu wprost) i ścieżki
AI (parse_ai_devices na JSON z Gemini).

CEL: dać inżynierowi możliwość uruchomienia obu metod naraz i zobaczyć,
CZY i GDZIE się rozjeżdżają - bez zgadywania, czy ekstrakcja AI "wygląda
dobrze". To jest kontrola jakości, nie wybór lepszej metody z góry.

ZASADA ZERO-HALUCYNACJI STOSUJE SIĘ TU WPROST:
Ten moduł NIE wybiera, która wersja jest "poprawna" - tylko WYKRYWA
i NAZYWA różnice. Wybór, którą wersję zatwierdzić do dalszej pracy,
zawsze należy do inżyniera (patrz UI w app.py). Żadna liczba tutaj nie
trafia do bilansu I/O ani kosztorysu - to wyłącznie warstwa diagnostyczna.

Ten moduł jest CAŁKOWICIE deterministyczny (brak AI) - to fundament, na
którym dopiero (opcjonalnie, patrz core/diff_explainer.py) może pracować
trzecie wywołanie AI wyjaśniające PRZYCZYNĘ wykrytych tu różnic. AI nigdy
nie widzi surowych danych źródłowych na tym etapie - tylko już wykryte,
ustrukturyzowane różnice z tego modułu.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .io_counter import IO_TYPES


@dataclass
class DeviceDiffEntry:
    """Jedna różnica na poziomie pojedynczego urządzenia."""
    strona: str          # "tylko_offline" | "tylko_ai"
    oznaczenie: str
    opis: str
    ilosc: int


@dataclass
class ExtractionDiff:
    """Wynik porównania dwóch ekstrakcji tego samego źródła."""
    # Różnica bilansu I/O per typ: dodatnia = AI naliczyło więcej niż offline
    balans_delta: dict[str, int] = field(default_factory=lambda: {t: 0 for t in IO_TYPES})
    liczba_urzadzen_offline: int = 0
    liczba_urzadzen_ai: int = 0

    # Urządzenia obecne tylko w jednej ze ścieżek (dopasowanie po lp+oznaczenie+opis)
    tylko_w_offline: list[DeviceDiffEntry] = field(default_factory=list)
    tylko_w_ai: list[DeviceDiffEntry] = field(default_factory=list)

    @property
    def identyczne(self) -> bool:
        """True, jeśli obie ścieżki dają identyczny bilans I/O i te same urządzenia."""
        return (
            all(v == 0 for v in self.balans_delta.values())
            and not self.tylko_w_offline
            and not self.tylko_w_ai
        )

    @property
    def ma_roznice(self) -> bool:
        return not self.identyczne


def _normalize_lp(lp: str) -> str:
    """
    Normalizuje L.p. do porównania między ścieżkami. Excel bywa wczytywany
    przez pandas jako float64 (gdy kolumna ma choćkolwiek jeden pusty wiersz),
    więc parser.py::_clean() na ścieżce offline daje "1.0", podczas gdy AI,
    zgodnie z kontraktem, zwraca czysty tekst "1" - identyczna wartość
    logiczna, różny zapis. Bez tej normalizacji KAŻDE porównanie zgłaszałoby
    fałszywe różnice na 100% urządzeń, nawet przy w pełni zgodnej ekstrakcji.
    Zmierzone bezpośrednio przy budowie tego modułu - patrz testy.
    """
    s = lp.strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _device_match_key(dev) -> tuple[str, str, str]:
    """
    Klucz dopasowania urządzenia między dwiema ścieżkami. Celowo NIE używamy
    tu device_key() z device_budget.py (ten ma indeks pozycji jako
    tie-breaker) - tutaj porównujemy DWIE RÓŻNE listy, gdzie kolejność
    i długość mogą się różnić, więc dopasowanie musi bazować wyłącznie
    na treści wiersza, nie na jego pozycji w żadnej z list.
    """
    return (_normalize_lp(dev.lp), dev.oznaczenie, dev.opis)


def compare_extractions(devices_offline: list, devices_ai: list) -> ExtractionDiff:
    """
    Porównuje dwie niezależnie zbudowane listy Device (offline vs AI) dla
    TEGO SAMEGO źródła danych.

    devices_offline: wynik parse_devices(excel_df)
    devices_ai: wynik parse_ai_devices(records) na ekstrakcji z tego samego
                pliku (Excel, ewentualnie + PDF - patrz uwaga niżej)

    UWAGA: jeśli devices_ai powstało z Excela + PDF, a devices_offline tylko
    z Excela, to każda pozycja "tylko_w_ai" MOŻE oznaczać zarówno błąd
    ekstrakcji, JAK I urządzenie widoczne wyłącznie w PDF (czego offline
    strukturalnie nie może zobaczyć - parse_devices nie czyta PDF).
    Ten moduł tego nie rozróżnia - to zadanie warstwy wyjaśniającej
    (core/diff_explainer.py), której trzeba przekazać informację, czy
    PDF był użyty, żeby nie stawiała fałszywej alarmowej diagnozy.
    """
    diff = ExtractionDiff()
    diff.liczba_urzadzen_offline = len(devices_offline)
    diff.liczba_urzadzen_ai = len(devices_ai)

    # Bilans I/O per typ - liczymy tu bezpośrednio z sygnałów, NIE przez
    # count_io(), żeby nie mieszać efektu rezerwy do porównania ekstrakcji.
    # Rezerwa to osobny, późniejszy krok - różnica ekstrakcji musi być
    # widoczna na poziomie bazowym, przed jakimkolwiek mnożeniem.
    def _bilans_bazowy(devices) -> dict[str, int]:
        bal = {t: 0 for t in IO_TYPES}
        for dev in devices:
            qty = getattr(dev, "ilosc", 1) or 1
            for sig in getattr(dev, "sygnaly", []):
                if sig.get("typ") in IO_TYPES:
                    bal[sig["typ"]] += qty
        return bal

    bal_off = _bilans_bazowy(devices_offline)
    bal_ai = _bilans_bazowy(devices_ai)
    diff.balans_delta = {t: bal_ai[t] - bal_off[t] for t in IO_TYPES}

    # Dopasowanie urządzeń po kluczu treściowym
    off_by_key = {_device_match_key(d): d for d in devices_offline}
    ai_by_key = {_device_match_key(d): d for d in devices_ai}

    for key, dev in off_by_key.items():
        if key not in ai_by_key:
            diff.tylko_w_offline.append(DeviceDiffEntry(
                strona="tylko_offline", oznaczenie=dev.oznaczenie or "-",
                opis=dev.opis, ilosc=dev.ilosc,
            ))
    for key, dev in ai_by_key.items():
        if key not in off_by_key:
            diff.tylko_w_ai.append(DeviceDiffEntry(
                strona="tylko_ai", oznaczenie=dev.oznaczenie or "-",
                opis=dev.opis, ilosc=dev.ilosc,
            ))

    return diff


def format_extraction_diff(diff: ExtractionDiff) -> str:
    """Tekstowe podsumowanie różnic - do logów / trybu CLI / testów."""
    if diff.identyczne:
        return "✓ Ścieżki OFFLINE i AI dają identyczny wynik — brak różnic."

    lines = ["⚠ Wykryto różnice między ścieżką OFFLINE a AI:"]
    lines.append(
        f"  Urządzeń: offline={diff.liczba_urzadzen_offline}, ai={diff.liczba_urzadzen_ai}"
    )
    lines.append("  Bilans I/O (delta AI - offline):")
    for t in IO_TYPES:
        d = diff.balans_delta[t]
        if d != 0:
            lines.append(f"    {t}: {d:+d}")
    if diff.tylko_w_offline:
        lines.append(f"  Tylko w OFFLINE ({len(diff.tylko_w_offline)}):")
        for e in diff.tylko_w_offline[:10]:
            lines.append(f"    - {e.oznaczenie}: {e.opis} (x{e.ilosc})")
    if diff.tylko_w_ai:
        lines.append(f"  Tylko w AI ({len(diff.tylko_w_ai)}):")
        for e in diff.tylko_w_ai[:10]:
            lines.append(f"    - {e.oznaczenie}: {e.opis} (x{e.ilosc})")
    return "\n".join(lines)
