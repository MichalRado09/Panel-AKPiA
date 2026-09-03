"""
core/validator.py
==================
Walidator kompletności oferty. Sprawdza logiczną spójność MIĘDZY sekcjami
(urządzenia, I/O, PLC, okablowanie, szafa, SCADA, kosztorys) — rzeczy,
których żaden pojedynczy moduł nie widzi, bo każdy liczy tylko swój wycinek.

Cel: złapać oczywiste niespójności, zanim oferta trafi do klienta.
Np. "masz sygnały AI, ale zero metrów kabla ekranowanego" — to sygnał,
że coś w danych wejściowych jest niekompletne albo błędnie sparsowane.

Walidator NIE blokuje generowania — tylko ostrzega. Decyzję zawsze
podejmuje inżynier (HITL), walidator tylko zwraca uwagę.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Severity(Enum):
    INFO = "info"        # do wiedzy, nie wymaga działania
    WARNING = "warning"  # warto sprawdzić przed wysyłką
    ERROR = "error"       # prawdopodobny błąd, wysoka szansa że coś nie gra


@dataclass
class ValidationIssue:
    severity: Severity
    message: str
    category: str = ""  # np. "I/O", "Kable", "Kosztorys", "Zasilanie"


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.WARNING]

    @property
    def infos(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.INFO]

    @property
    def is_clean(self) -> bool:
        return len(self.errors) == 0 and len(self.warnings) == 0


def _add(report: ValidationReport, sev: Severity, msg: str, cat: str = ""):
    report.issues.append(ValidationIssue(sev, msg, cat))


def validate_offer(
    devices,
    balance,
    plc_selection,
    cable_selection,
    cabinet_selection,
    asix_selection,
    budget,
) -> ValidationReport:
    """
    Sprawdza spójność między wszystkimi sekcjami oferty.
    Każda funkcja _check_* sprawdza jeden aspekt — łatwo dodać kolejne.
    """
    report = ValidationReport()

    _check_io_vs_cables(balance, cable_selection, report)
    _check_io_vs_plc_capacity(balance, plc_selection, report)
    _check_power_budget(cabinet_selection, report)
    _check_undecided_signals(balance, report)
    _check_inferred_signals_ratio(balance, report)
    _check_budget_completeness(budget, report)
    _check_empty_device_list(devices, report)
    _check_scada_package_headroom(asix_selection, report)

    return report


def _check_io_vs_cables(balance, cable_sel, report: ValidationReport):
    """Czy są sygnały AI/AO, ale brak kabla ekranowanego (typowy błąd danych)."""
    has_analog = balance.reserved.get("AI", 0) > 0 or balance.reserved.get("AO", 0) > 0
    # FALOWNIK liczy się jako pokrycie sygnału analogowego: core/cables.py
    # świadomie SCALA urządzenie z AO+DO (pompa z falownikiem) w jedną pozycję
    # "FALOWNIK", zamiast wystawiać osobny wiersz AO - a dobrany tam kabel
    # (BiTservo 2XSLCH-J) JEST ekranowany i to on niesie sterowanie AO.
    # Bez tego wyjątku projekt złożony z samych pomp z falownikiem (bardzo
    # typowy w AKPiA) dostawał czerwony BŁĄD "brak kabla ekranowanego",
    # mimo że kabel ekranowany był na liście.
    has_analog_cable = any(
        it.typ_sygnalu in ("AI", "AO", "FALOWNIK")
        for it in (cable_sel.items if cable_sel else [])
    )
    if has_analog and not has_analog_cable:
        _add(report, Severity.ERROR,
             "Bilans I/O zawiera sygnały analogowe (AI/AO), ale zestawienie "
             "kablowe nie ma pozycji kabla ekranowanego. Sprawdź dobór okablowania.",
             "Kable")

    has_digital = balance.reserved.get("DI", 0) > 0 or balance.reserved.get("DO", 0) > 0
    has_digital_cable = any(
        it.typ_sygnalu in ("DI", "DO", "FALOWNIK")
        for it in (cable_sel.items if cable_sel else [])
    )
    if has_digital and not has_digital_cable:
        _add(report, Severity.WARNING,
             "Bilans I/O zawiera sygnały cyfrowe (DI/DO), ale zestawienie "
             "kablowe nie ma odpowiadającej pozycji kabla sterowniczego.",
             "Kable")


def _check_io_vs_plc_capacity(balance, plc_sel, report: ValidationReport):
    """Czy dobrane karty faktycznie pokrywają zapotrzebowanie (zapas ujemny = błąd)."""
    if not plc_sel:
        return
    for sig_type, util in plc_sel.utilization.items():
        zapas = util.get("zapas_kanałów", 0)
        if zapas < 0:
            _add(report, Severity.ERROR,
                 f"Dobrane karty {sig_type} NIE POKRYWAJĄ zapotrzebowania "
                 f"(brakuje {abs(zapas)} kanałów). To błąd doboru — zgłoś.",
                 "PLC")


def _check_power_budget(cabinet_sel, report: ValidationReport):
    """Czy dobrany zasilacz ma sensowny zapas (nie za ciasno, nie absurdalnie za duży)."""
    if not cabinet_sel or cabinet_sel.zasilacz_a <= 0:
        return
    obciazenie = cabinet_sel.prad_z_zapasem_a
    zasilacz = cabinet_sel.zasilacz_a
    wykorzystanie = obciazenie / zasilacz if zasilacz else 0

    if wykorzystanie > 0.95:
        _add(report, Severity.WARNING,
             f"Zasilacz {zasilacz}A jest obciążony w {wykorzystanie:.0%} — "
             "bardzo ciasny dobór, rozważ większy zasilacz na przyszłą rozbudowę.",
             "Zasilanie")
    elif wykorzystanie < 0.15:
        _add(report, Severity.INFO,
             f"Zasilacz {zasilacz}A jest obciążony tylko w {wykorzystanie:.0%} — "
             "być może mniejszy zasilacz wystarczy (do weryfikacji kosztowej).",
             "Zasilanie")


def _check_undecided_signals(balance, report: ValidationReport):
    """Ile sygnałów BRAK DANYCH — wymagają decyzji przed wysyłką oferty."""
    n = len(balance.undecided)
    if n > 0:
        _add(report, Severity.WARNING,
             f"{n} sygnał(ów) ma status BRAK DANYCH (nierozpoznany typ DI/DO/AI/AO). "
             "Nie wchodzą do bilansu I/O — sprawdź, czy to celowe.",
             "I/O")


def _check_inferred_signals_ratio(balance, report: ValidationReport):
    """Jeśli >70% sygnałów pochodzi z reguły typu (nie z jawnych danych) — ostrzeżenie."""
    kolumna = balance.source_counts.get("kolumna", 0)
    typ = balance.source_counts.get("typ_urzadzenia", 0)
    total = kolumna + typ
    if total > 0 and typ / total > 0.7:
        _add(report, Severity.WARNING,
             f"{typ}/{total} sygnałów ({typ/total:.0%}) wywnioskowano z typu "
             "urządzenia, nie z jawnych danych źródłowych. Zestawienie "
             "wejściowe może mieć niewypełnione kolumny sygnałów — zalecana "
             "dokładna weryfikacja przed ofertą.",
             "I/O")


def _check_budget_completeness(budget, report: ValidationReport):
    """Ile pozycji w kosztorysie nie ma ceny — kosztorys jest wtedy niepełny."""
    if not budget or not budget.items:
        return
    n_brak = len(budget.brak_ceny)
    n_total = len(budget.items)
    if n_brak > 0:
        pct = n_brak / n_total
        sev = Severity.ERROR if pct > 0.5 else Severity.WARNING
        _add(report, sev,
             f"{n_brak}/{n_total} pozycji kosztorysu ({pct:.0%}) nie ma ceny "
             "katalogowej. Suma netto jest NIEPEŁNA — uzupełnij cennik przed "
             "wysłaniem oferty do klienta.",
             "Kosztorys")


def _check_empty_device_list(devices, report: ValidationReport):
    """Pusta lista urządzeń — nic nie da się zrobić dalej."""
    if not devices:
        _add(report, Severity.ERROR,
             "Brak zidentyfikowanych urządzeń. Sprawdź plik wejściowy "
             "(format kolumn, czy dane nie są puste).",
             "Urządzenia")


def _check_scada_package_headroom(asix_sel, report: ValidationReport):
    """Czy pakiet ASIX ma rozsądny zapas, czy jest tuż nad progiem (mało miejsca na rozbudowę)."""
    if not asix_sel or asix_sel.prog_licencyjny <= 0:
        return
    wykorzystanie = asix_sel.zmienne_obliczone / asix_sel.prog_licencyjny
    if wykorzystanie > 0.9:
        _add(report, Severity.INFO,
             f"Pakiet ASIX {asix_sel.prog_nazwa} jest wykorzystany w "
             f"{wykorzystanie:.0%} — mały zapas na przyszłą rozbudowę instalacji.",
             "SCADA")


def format_report(report: ValidationReport) -> str:
    if report.is_clean:
        return "✓ Brak zastrzeżeń — oferta wygląda na spójną."
    lines = []
    for sev, label in ((Severity.ERROR, "BŁĄD"), (Severity.WARNING, "OSTRZEŻENIE"),
                       (Severity.INFO, "INFO")):
        items = [i for i in report.issues if i.severity == sev]
        for it in items:
            lines.append(f"[{label}] ({it.category}) {it.message}")
    return "\n".join(lines)
