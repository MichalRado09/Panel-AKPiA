"""
core/pdf_report.py
==================
Generator raportu PDF budowany bezpośrednio z danych (reportlab) — nie przez
konwersję Word→PDF. Powód: konwersja wymagałaby LibreOffice lub MS Word
zainstalowanego na komputerze użytkownika, czego nie możemy zagwarantować
(Windows w Cursorze). reportlab jest czystą biblioteką Python — działa
identycznie wszędzie, bez zależności zewnętrznych.

Zawartość odpowiada raportowi Word (te same dane), tylko inny silnik render.
"""

from __future__ import annotations

import io
import os
import time

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
)


# --- Rejestracja czcionki z pełnym wsparciem polskich znaków ------------------
# Domyślne czcionki reportlab (Helvetica) NIE mają polskich znaków (ą, ę, ż...)
# i renderują je jako czarne kwadraty. DejaVu Sans (dołączona do projektu w
# assets/fonts/, licencja wolna/Public Domain) ma pełne pokrycie Unicode i
# działa identycznie na każdym systemie, niezależnie od zainstalowanych
# czcionek systemowych (ważne dla przenośności Windows/Cursor).
_FONTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "fonts")
_FONT_REGULAR = "DejaVuSans"
_FONT_BOLD = "DejaVuSans-Bold"

_fonts_registered = False


def _ensure_fonts():
    global _fonts_registered
    if _fonts_registered:
        return
    reg_path = os.path.join(_FONTS_DIR, "DejaVuSans.ttf")
    bold_path = os.path.join(_FONTS_DIR, "DejaVuSans-Bold.ttf")
    if os.path.exists(reg_path) and os.path.exists(bold_path):
        pdfmetrics.registerFont(TTFont(_FONT_REGULAR, reg_path))
        pdfmetrics.registerFont(TTFont(_FONT_BOLD, bold_path))
        _fonts_registered = True
    else:
        # Fallback: brak plików czcionek — użyjemy Helvetica (bez polskich znaków)
        # zamiast wywalać cały raport. Lepiej dostać PDF z paroma krzaczkami niż nic.
        globals()["_FONT_REGULAR"] = "Helvetica"
        globals()["_FONT_BOLD"] = "Helvetica-Bold"
        _fonts_registered = True


def _styles():
    _ensure_fonts()
    styles = getSampleStyleSheet()
    # Nadpisujemy domyślne fontName na DejaVu we wszystkich bazowych stylach
    for name in ("Normal", "Title", "Heading1", "Heading2"):
        styles[name].fontName = _FONT_REGULAR if "Heading" not in name and name != "Title" else _FONT_BOLD
    styles.add(ParagraphStyle(
        name="ReportTitle", parent=styles["Title"], alignment=TA_CENTER, fontSize=18,
        fontName=_FONT_BOLD,
    ))
    styles.add(ParagraphStyle(
        name="Meta", parent=styles["Normal"], alignment=TA_RIGHT, fontSize=8,
        textColor=colors.grey, fontName=_FONT_REGULAR,
    ))
    styles.add(ParagraphStyle(
        name="SectionHeading", parent=styles["Heading1"], fontSize=13,
        spaceBefore=14, spaceAfter=6, textColor=colors.HexColor("#2F5496"),
        fontName=_FONT_BOLD,
    ))
    return styles


def _table(data: list[list[str]], col_widths=None) -> Table:
    """Tabela ze stylem spójnym w całym raporcie."""
    _ensure_fonts()
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F5496")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), _FONT_BOLD),
        ("FONTNAME", (0, 1), (-1, -1), _FONT_REGULAR),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#BBBBBB")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F7FA")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def create_pdf_report(
    devices,
    balance,
    project_label: str,
    platforma: str,
    sel,             # PlcSelection
    cab_sel,          # CabinetSelection
    asix_sel,         # AsixSelection
    budget,           # Budget
    io_types: tuple,
) -> io.BytesIO:
    """
    Buduje kompletny raport PDF — te same dane co create_word_report,
    inny silnik renderowania (reportlab, bez zależności zewnętrznych).
    """
    styles = _styles()
    bio = io.BytesIO()
    doc = SimpleDocTemplate(
        bio, pagesize=A4,
        topMargin=18 * mm, bottomMargin=18 * mm,
        leftMargin=16 * mm, rightMargin=16 * mm,
    )
    story = []

    # --- Nagłówek ---
    story.append(Paragraph(f"Raport AKPiA: {project_label}", styles["ReportTitle"]))
    story.append(Paragraph(
        f"Wygenerowano przez Panel Inżyniera AKPiA. Data: {time.strftime('%Y-%m-%d %H:%M')}",
        styles["Meta"],
    ))
    story.append(Spacer(1, 10))

    # --- Bilans I/O ---
    story.append(Paragraph("Bilans sygnałów I/O", styles["SectionHeading"]))
    io_data = [["Typ", "Baza", f"+Rezerwa {balance.reserve_percent}%"]]
    for t in io_types:
        io_data.append([t, str(balance.base[t]), str(balance.reserved[t])])
    io_data.append(["RAZEM", str(balance.base_total), str(balance.reserved_total)])
    story.append(_table(io_data, col_widths=[40 * mm, 40 * mm, 40 * mm]))

    # --- Dobór sterownika ---
    story.append(Paragraph(f"Dobór sterownika ({platforma})", styles["SectionHeading"]))
    plc_data = [["Ilość", "Nr katalogowy", "Opis"]]
    for it in sel.items:
        plc_data.append([str(it.ilosc), it.nr, it.opis])
    story.append(_table(plc_data, col_widths=[18 * mm, 55 * mm, 95 * mm]))

    # --- Szafa SAKG ---
    story.append(Paragraph("Szafa sterownicza (+SAKG)", styles["SectionHeading"]))
    cab_data = [["Ilość", "Nr katalogowy", "Nazwa"]]
    for it in cab_sel.items:
        cab_data.append([str(it.ilosc), it.nr_katalogowy, it.nazwa[:55]])
    story.append(_table(cab_data, col_widths=[18 * mm, 55 * mm, 95 * mm]))
    story.append(Paragraph(
        f"Bilans prądowy 24V DC: {cab_sel.prad_total_ma} mA, "
        f"z zapasem 30%: {cab_sel.prad_z_zapasem_a} A → zasilacz {cab_sel.zasilacz_a} A",
        styles["Normal"],
    ))

    # --- SCADA ASIX ---
    story.append(Paragraph("SCADA ASIX", styles["SectionHeading"]))
    story.append(Paragraph(
        f"Zmiennych procesowych: {asix_sel.zmienne_obliczone} "
        f"(sygnałów I/O: {asix_sel.zmienne_io} × {asix_sel.wspolczynnik}). "
        f"Pakiet licencyjny: {asix_sel.prog_nazwa}.",
        styles["Normal"],
    ))
    story.append(Paragraph(f"Sugestia architektury: {asix_sel.sugestia_opis}", styles["Normal"]))
    if asix_sel.items:
        asix_data = [["Ilość", "Nr katalogowy", "Nazwa"]]
        for it in asix_sel.items:
            asix_data.append([str(it.ilosc), it.nr_katalogowy, it.nazwa])
        story.append(Spacer(1, 4))
        story.append(_table(asix_data, col_widths=[18 * mm, 55 * mm, 95 * mm]))

    # --- Kosztorys (nowa strona — zwykle długi) ---
    story.append(PageBreak())
    story.append(Paragraph("Kosztorys", styles["SectionHeading"]))
    bud_data = [["Nr kat.", "Nazwa", "Ilość", "Kat. PLN", "Rabat %", "Netto PLN"]]
    for it in budget.items:
        bud_data.append([
            it.nr_katalogowy,
            it.nazwa[:40],
            str(it.ilosc),
            f"{it.cena_katalogowa:.2f}" if it.cena_katalogowa else "BRAK",
            f"{it.rabat_pct:.0f}%",
            f"{it.wartosc_netto:.2f}" if it.wartosc_netto else "-",
        ])
    bud_data.append([
        "SUMA", "", "", f"{budget.suma_katalogowa:.2f}", "", f"{budget.suma_netto:.2f}",
    ])
    story.append(_table(
        bud_data,
        col_widths=[28 * mm, 55 * mm, 14 * mm, 22 * mm, 18 * mm, 25 * mm],
    ))
    if budget.brak_ceny:
        story.append(Spacer(1, 6))
        story.append(Paragraph(
            f"Uwaga: {len(budget.brak_ceny)} pozycji bez ceny katalogowej. "
            "Uzupełnij cennik, aby uzyskać pełny kosztorys.",
            styles["Normal"],
        ))

    # --- Uwagi techniczne ---
    story.append(Paragraph("Uwagi techniczne", styles["SectionHeading"]))
    if balance.undecided:
        story.append(Paragraph(
            "Sygnały wymagające decyzji inżyniera (BRAK DANYCH):", styles["Normal"],
        ))
        for u in balance.undecided:
            story.append(Paragraph(
                f"• {u['urzadzenie']}: {u['sygnal']} (x{u['ilosc']})", styles["Normal"],
            ))
    inferred = balance.source_counts.get("typ_urzadzenia", 0)
    if inferred:
        story.append(Paragraph(
            f"Uwaga: {inferred} sygnał(ów) wywnioskowano z typu urządzenia "
            "(kolumny sygnałów były puste). Wymaga weryfikacji projektanta.",
            styles["Normal"],
        ))
    for w in (cab_sel.warnings or []):
        story.append(Paragraph(f"• {w}", styles["Normal"]))

    doc.build(story)
    bio.seek(0)
    return bio
