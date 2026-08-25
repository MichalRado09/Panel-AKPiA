import os
import tempfile
import time
import io
import json

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from google import genai
from google.genai import types
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH

# --- RDZEŃ DETERMINISTYCZNY (core/) ---
# Cały dobór i zliczanie dzieje się tutaj, NIE w LLM.
from core.ai_contract import build_extraction_prompt, parse_ai_json, build_response_schema
from core.parser import parse_devices, parse_ai_devices, devices_to_records
from core.io_counter import count_io, format_balance, IO_TYPES
from core.plc_selector import select_plc, format_selection, PLATFORMY
from core.budget import calculate_budget, format_budget, GRUPY_RABATOWE
from core.cables import select_cables
from core.comparison import compare_variants
from core.scada_asix import select_asix
from core.cabinet import select_cabinet
from core.device_budget import build_device_budget, device_key, GRUPA_RABATOWA
from core.extraction_diff import compare_extractions, ExtractionDiff
from core.pdf_report import create_pdf_report
from core.validator import validate_offer, Severity
from core.hmi import build_hmi_selection, TYPOWE_PANELE

# --- 1. KONFIGURACJA BAZOWA ---
load_dotenv()

HISTORY_DIR = "historia_projektow"
OUTPUT_DIR = "outputs"
os.makedirs(HISTORY_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

GEMINI_MODEL = "gemini-3.5-flash"
MAX_INLINE_PDF_BYTES = 15 * 1024 * 1024


# --- 2. ZABEZPIECZENIE APLIKACJI (LOGOWANIE) ---
def check_password():
    """Zwraca True, jeśli użytkownik wprowadził poprawne hasło."""
    correct_password = os.getenv("APP_PASSWORD")
    if not correct_password and "APP_PASSWORD" in st.secrets:
        correct_password = st.secrets["APP_PASSWORD"]

    if not correct_password:
        st.error("🚨 Krytyczny błąd: Brak skonfigurowanego hasła systemu! Ustaw APP_PASSWORD w .env lub secrets.toml.")
        st.stop()

    def password_entered():
        if st.session_state["password"] == correct_password:
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.markdown("### 🔒 Panel Inżyniera AKPiA - Dostęp ograniczony")
        st.text_input("Wprowadź hasło dostępu:", type="password", on_change=password_entered, key="password")
        return False
    elif not st.session_state["password_correct"]:
        st.markdown("### 🔒 Panel Inżyniera AKPiA - Dostęp ograniczony")
        st.text_input("Wprowadź hasło dostępu:", type="password", on_change=password_entered, key="password")
        st.error("😕 Niepoprawne hasło. Spróbuj ponownie.")
        return False

    return True


# --- 3. LOGIKA API GEMINI (tryb ekstrakcji JSON) ---
def normalize_model_name(model_name: str) -> str:
    model_name = model_name.strip()
    return model_name.removeprefix("models/") if model_name.startswith("models/") else model_name


def extract_response_text(response) -> str:
    if getattr(response, "text", None):
        return response.text
    if getattr(response, "candidates", None):
        candidate = response.candidates[0]
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) if content else None
        if parts:
            text_parts = [part.text for part in parts if getattr(part, "text", None)]
            if text_parts:
                return "\n".join(text_parts)
    raise ValueError("Nie udało się odczytać treści odpowiedzi z API.")


def get_api_key() -> str:
    override = st.session_state.get("api_key_override", "").strip()
    if override:
        return override
    if "GEMINI_API_KEY" in st.secrets:
        return st.secrets["GEMINI_API_KEY"]
    return os.getenv("GEMINI_API_KEY", "").strip()


def wait_for_file_active(client: genai.Client, file_name: str, timeout: int = 120) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        file_info = client.files.get(name=file_name)
        state_name = getattr(getattr(file_info, "state", None), "name", getattr(file_info, "state", None))
        if state_name == "ACTIVE":
            return
        if state_name == "FAILED":
            raise ValueError("Przetwarzanie pliku PDF w API zakończyło się błędem.")
        time.sleep(2)
    raise TimeoutError("Przekroczono czas oczekiwania na aktywację pliku w Gemini.")


def prepare_pdf_for_gemini(client: genai.Client, pdf_bytes: bytes):
    if len(pdf_bytes) <= MAX_INLINE_PDF_BYTES:
        pdf_part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
        return [pdf_part], None

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name
    try:
        uploaded_file = client.files.upload(file=tmp_path)
        wait_for_file_active(client, uploaded_file.name)
        return [uploaded_file], uploaded_file
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def call_gemini_with_retry(client, model_id, contents, system_instruction, response_schema=None, max_retries=3):
    for attempt in range(max_retries):
        try:
            config_kwargs = {"system_instruction": system_instruction}
            # Wymuszony tryb JSON: schema + mime_type = gwarancja poprawnego JSON
            if response_schema is not None:
                config_kwargs["response_mime_type"] = "application/json"
                config_kwargs["response_schema"] = response_schema
            response = client.models.generate_content(
                model=model_id,
                contents=contents,
                config=types.GenerateContentConfig(**config_kwargs),
            )
            return extract_response_text(response)
        except Exception as e:
            err_str = str(e)
            if ("503" in err_str or "429" in err_str) and attempt < max_retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            raise e


def run_extraction(api_key, excel_df, pdf_bytes, excel_filename, pdf_filename) -> list[dict]:
    """
    NOWY PRZEPŁYW: AI zwraca WYŁĄCZNIE listę urządzeń (JSON).
    Żadnego doboru ani BOM - to policzy rdzeń w core/.
    """
    client = genai.Client(api_key=api_key)
    model_id = normalize_model_name(GEMINI_MODEL)
    sys_inst = build_extraction_prompt()

    user_prompt = "ZAŁĄCZONE ŹRÓDŁA DANYCH:\n"
    zrodla = []
    if excel_df is not None:
        zrodla.append(f"- Tabela urządzeń (Excel): {excel_filename}")
    if pdf_bytes is not None:
        zrodla.append(f"- Dokumentacja (PDF): {pdf_filename}")
    user_prompt += "\n".join(zrodla) + "\n\n"

    if excel_df is not None:
        user_prompt += (
            f"PODGLĄD DANYCH Z ARKUSZA EXCEL ({excel_filename}), format CSV:\n\n"
            f"{excel_df.to_csv(index=False)}\n\n"
        )

    contents = []
    uploaded_file = None
    if pdf_bytes is not None:
        pdf_parts, uploaded_file = prepare_pdf_for_gemini(client, pdf_bytes)
        contents.extend(pdf_parts)
        user_prompt += (
            f"Załączony PDF '{pdf_filename}' - wyodrębnij z niego urządzenia "
            "i połącz z danymi z Excela (bez podwójnego liczenia).\n\n"
        )

    user_prompt += "Zwróć listę urządzeń jako JSON zgodny ze schematem z instrukcji."
    contents.append(user_prompt)

    try:
        raw = call_gemini_with_retry(
            client, model_id, contents, sys_inst,
            response_schema=build_response_schema(),
        )
        return parse_ai_json(raw)
    finally:
        if uploaded_file:
            try:
                client.files.delete(name=uploaded_file.name)
            except Exception:
                pass


# --- 4. GENEROWANIE PLIKÓW (na podstawie zatwierdzonych danych rdzenia) ---
def build_io_dataframe(devices) -> pd.DataFrame:
    """Tabela urządzeń z sygnałami - do podglądu i weryfikacji przez inżyniera."""
    rows = []
    for d in devices:
        sygnaly = "; ".join(f"{s['nazwa']} [{s['typ']}]" for s in d.sygnaly) or "-"
        zrodla = {s.get("source", "kolumna") for s in d.sygnaly}
        rows.append({
            "L.p.": d.lp,
            "Układ": d.uklad,
            "Oznaczenie": d.oznaczenie,
            "Opis": d.opis,
            "Ilość": d.ilosc,
            "Sygnały": sygnaly,
            "Źródło": ", ".join(sorted(zrodla)) if zrodla else "-",
            "Uwagi parsera": " | ".join(d.warnings) if d.warnings else "",
        })
    return pd.DataFrame(rows)


def create_word_report(devices, balance, project_label: str, platforma: str, rabaty: dict = None, hmi_entries: list = None, wycena_akpia_keys: set = None) -> io.BytesIO:
    """Raport inżynierski z zatwierdzonym bilansem I/O (na razie: I/O; dobór w kolejnych modułach)."""
    doc = Document()
    title = doc.add_heading(f"Raport AKPiA: {project_label}", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    p = doc.add_paragraph()
    run = p.add_run(f"Wygenerowano przez Panel Inżyniera AKPiA. Data: {time.strftime('%Y-%m-%d %H:%M')}")
    run.font.size = Pt(9)
    run.italic = True
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    doc.add_heading("Bilans sygnałów I/O", level=1)
    table = doc.add_table(rows=1, cols=3)
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0].cells
    hdr[0].text, hdr[1].text, hdr[2].text = "Typ", "Baza", f"+Rezerwa {balance.reserve_percent}%"
    for t in IO_TYPES:
        cells = table.add_row().cells
        cells[0].text = t
        cells[1].text = str(balance.base[t])
        cells[2].text = str(balance.reserved[t])
    total = table.add_row().cells
    total[0].text, total[1].text, total[2].text = "RAZEM", str(balance.base_total), str(balance.reserved_total)

    doc.add_heading(f"Dobór sterownika ({platforma})", level=1)
    sel = select_plc(balance, platforma)
    tbl = doc.add_table(rows=1, cols=3)
    tbl.style = "Light Grid Accent 1"
    h = tbl.rows[0].cells
    h[0].text, h[1].text, h[2].text = "Ilość", "Nr katalogowy", "Opis"
    for it in sel.items:
        c = tbl.add_row().cells
        c[0].text, c[1].text, c[2].text = str(it.ilosc), it.nr, it.opis

    hmi_sel_doc = build_hmi_selection(hmi_entries or [])
    if hmi_sel_doc.items:
        doc.add_heading("HMI — panele operatorskie lokalne", level=1)
        htbl = doc.add_table(rows=1, cols=3)
        htbl.style = "Light Grid Accent 1"
        hh = htbl.rows[0].cells
        hh[0].text, hh[1].text, hh[2].text = "Ilość", "Model", "Lokalizacja"
        for it in hmi_sel_doc.items:
            hc = htbl.add_row().cells
            hc[0].text, hc[1].text, hc[2].text = str(it.ilosc), it.nazwa, it.lokalizacja

    doc.add_heading("Kosztorys", level=1)
    budget = calculate_budget(sel.items, rabaty=rabaty or {})
    bt = doc.add_table(rows=1, cols=6)
    bt.style = "Light Grid Accent 1"
    bh = bt.rows[0].cells
    for i, h in enumerate(["Nr kat.", "Nazwa", "Ilość", "Kat. PLN", "Rabat %", "Netto PLN"]):
        bh[i].text = h
    for it in budget.items:
        bc = bt.add_row().cells
        bc[0].text = it.nr_katalogowy
        bc[1].text = it.nazwa
        bc[2].text = str(it.ilosc)
        bc[3].text = f"{it.cena_katalogowa:.2f}" if it.cena_katalogowa else "BRAK"
        bc[4].text = f"{it.rabat_pct:.0f}%"
        bc[5].text = f"{it.wartosc_netto:.2f}" if it.wartosc_netto else "-"
    sumr = bt.add_row().cells
    sumr[0].text = "SUMA"
    sumr[3].text = f"{budget.suma_katalogowa:.2f}"
    sumr[5].text = f"{budget.suma_netto:.2f}"
    if budget.brak_ceny:
        doc.add_paragraph(
            f"Uwaga: {len(budget.brak_ceny)} pozycji bez ceny katalogowej. "
            "Uzupełnij cennik, aby uzyskać pełny kosztorys."
        )

    dev_budget_doc = build_device_budget(devices, wycena_akpia_keys or set(), rabaty=rabaty or {})
    if dev_budget_doc.items:
        doc.add_heading("Kosztorys urządzeń AKPiA (wybór ręczny)", level=1)
        doc.add_paragraph(
            "Urządzenia obiektowe zaznaczone przez inżyniera jako wchodzące "
            "w zakres dostawy/wyceny AKPiA — osobno od sprzętu sterowniczego."
        )
        dt = doc.add_table(rows=1, cols=5)
        dt.style = "Light Grid Accent 1"
        dh = dt.rows[0].cells
        for i, h in enumerate(["Oznaczenie", "Opis", "Ilość", "Kat. PLN", "Netto PLN"]):
            dh[i].text = h
        for it in dev_budget_doc.items:
            dc = dt.add_row().cells
            dc[0].text = it.oznaczenie
            dc[1].text = it.opis
            dc[2].text = str(it.ilosc)
            dc[3].text = f"{it.cena_katalogowa:.2f}" if it.cena_katalogowa else "BRAK"
            dc[4].text = f"{it.wartosc_netto:.2f}" if it.wartosc_netto else "-"
        dsumr = dt.add_row().cells
        dsumr[0].text = "SUMA"
        dsumr[3].text = f"{dev_budget_doc.suma_katalogowa:.2f}"
        dsumr[4].text = f"{dev_budget_doc.suma_netto:.2f}"
        if dev_budget_doc.brak_ceny:
            doc.add_paragraph(
                f"Uwaga: {len(dev_budget_doc.brak_ceny)} pozycji bez ceny "
                "katalogowej (cennik nie zawiera jeszcze urządzeń obiektowych)."
            )

    doc.add_heading("Uwagi techniczne", level=1)
    if balance.undecided:
        doc.add_paragraph("Sygnały wymagające decyzji inżyniera (BRAK DANYCH):")
        for u in balance.undecided:
            doc.add_paragraph(f"• {u['urzadzenie']}: {u['sygnal']} (x{u['ilosc']})", style="List Bullet")
    inferred = balance.source_counts.get("typ_urzadzenia", 0)
    if inferred:
        doc.add_paragraph(
            f"Uwaga: {inferred} sygnał(ów) wywnioskowano z typu urządzenia "
            "(kolumny sygnałów były puste). Wymaga weryfikacji projektanta."
        )

    bio = io.BytesIO()
    doc.save(bio)
    bio.seek(0)
    return bio


def create_devices_excel(devices, balance, platforma: str, rabaty: dict = None, cable_length: float = 25, asix_factor: float = 1.2, hmi_entries: list = None, wycena_akpia_keys: set = None) -> io.BytesIO:
    """Excel: 9 zakładek — urządzenia, bilans, PLC, okablowanie, SCADA, porównanie, kosztorys, urządzenia AKPiA."""
    df_dev = build_io_dataframe(devices)
    df_io = pd.DataFrame(
        [{"Typ": t, "Baza": balance.base[t], f"Rezerwa {balance.reserve_percent}%": balance.reserved[t]}
         for t in IO_TYPES]
        + [{"Typ": "RAZEM", "Baza": balance.base_total, f"Rezerwa {balance.reserve_percent}%": balance.reserved_total}]
    )
    sel = select_plc(balance, platforma)
    df_plc = pd.DataFrame(
        [{"Ilość": it.ilosc, "Nr katalogowy": it.nr, "Opis": it.opis,
          "Grupa rabatowa": it.grupa_rabatowa}
         for it in sel.items]
    )
    cab = select_cables(devices, srednia_trasa_m=cable_length)
    df_cab = pd.DataFrame([
        {"Typ sygnału": it.typ_sygnalu, "Typ kabla": it.typ_kabla,
         "Urządzeń": it.ilosc_urzadzen, "Śr. trasa [m]": it.srednia_trasa_m,
         "Metraż [m]": it.metraz_m}
        for it in cab.items
    ] + [{"Typ sygnału": "RAZEM", "Metraż [m]": cab.total_metraz}])
    cab_sel = select_cabinet(balance, sel)
    df_cabinet = pd.DataFrame([
        {"Ilość": it.ilosc, "Nr katalogowy": it.nr_katalogowy, "Nazwa": it.nazwa,
         "Jednostka": it.jednostka, "Reguła doboru": it.uwaga,
         "Grupa rabatowa": it.grupa_rabatowa}
        for it in cab_sel.items
    ] + [
        {"Ilość": "", "Nr katalogowy": "BILANS PRĄDOWY 24V DC", "Nazwa": "", "Jednostka": "", "Reguła doboru": "", "Grupa rabatowa": ""},
        {"Ilość": cab_sel.prad_karty_ma, "Nr katalogowy": "Karty PLC [mA]", "Nazwa": "", "Jednostka": "mA", "Reguła doboru": "", "Grupa rabatowa": ""},
        {"Ilość": cab_sel.prad_przekazniki_ma, "Nr katalogowy": "Przekaźniki [mA]", "Nazwa": "", "Jednostka": "mA", "Reguła doboru": "", "Grupa rabatowa": ""},
        {"Ilość": cab_sel.prad_przetworniki_ma, "Nr katalogowy": "Przetworniki [mA]", "Nazwa": "", "Jednostka": "mA", "Reguła doboru": "", "Grupa rabatowa": ""},
        {"Ilość": cab_sel.prad_z_zapasem_a, "Nr katalogowy": "RAZEM z zapasem 30% [A]", "Nazwa": "", "Jednostka": "A", "Reguła doboru": "", "Grupa rabatowa": ""},
    ])
    asix = select_asix(balance, wspolczynnik=asix_factor)
    df_scada = pd.DataFrame([
        {"Parametr": "Sygnałów I/O (po rezerwie)", "Wartość": asix.zmienne_io},
        {"Parametr": f"Współczynnik zmiennych", "Wartość": asix.wspolczynnik},
        {"Parametr": "Zmiennych procesowych", "Wartość": asix.zmienne_obliczone},
        {"Parametr": "Pakiet licencyjny", "Wartość": asix.prog_nazwa},
        {"Parametr": "Sugestia architektury", "Wartość": asix.sugestia_opis},
    ] + [
        {"Parametr": f"Pozycja: {it.nr_katalogowy}", "Wartość": f"{it.ilosc}x {it.nazwa} ({it.cena_katalogowa} PLN)" if it.cena_katalogowa else f"{it.ilosc}x {it.nazwa}"}
        for it in asix.items
    ])
    variants = compare_variants(balance, rabaty=rabaty or {})
    df_cmp = pd.DataFrame([
        {"Platforma": v.platforma, "CPU": v.cpu,
         "Karty DI": v.karty_io.get("DI", 0), "Karty DO": v.karty_io.get("DO", 0),
         "Karty AI": v.karty_io.get("AI", 0), "Karty AO": v.karty_io.get("AO", 0),
         "Moduły łącznie": v.total_modules,
         "Suma netto [PLN]": v.suma_netto if v.suma_netto > 0 else None}
        for v in variants
    ])
    # Kosztorys: PLC + ASIX razem
    all_cost_items = list(sel.items)
    budget = calculate_budget(all_cost_items, rabaty=rabaty or {})
    # Dodaj pozycje ASIX i HMI do kosztorysu
    from core.plc_selector import PlcItem as _PI
    asix_plc_items = [_PI(nr=it.nr_katalogowy, opis=it.nazwa, ilosc=it.ilosc, grupa_rabatowa="ASIX") for it in asix.items]
    cab_plc_items = [_PI(nr=it.nr_katalogowy, opis=it.nazwa, ilosc=it.ilosc, grupa_rabatowa="APARATURA") for it in cab_sel.items]
    hmi_sel_xl = build_hmi_selection(hmi_entries or [])
    hmi_plc_items = [_PI(nr=f"HMI-{i}", opis=f"{it.nazwa} ({it.lokalizacja})" if it.lokalizacja else it.nazwa,
                         ilosc=it.ilosc, grupa_rabatowa="APARATURA")
                     for i, it in enumerate(hmi_sel_xl.items, 1)]
    df_hmi = pd.DataFrame([
        {"Ilość": it.ilosc, "Model": it.nazwa, "Lokalizacja": it.lokalizacja}
        for it in hmi_sel_xl.items
    ]) if hmi_sel_xl.items else pd.DataFrame([{"Ilość": "", "Model": "Brak dodanych paneli HMI", "Lokalizacja": ""}])
    budget_cab = calculate_budget(cab_plc_items, rabaty=rabaty or {})
    budget_asix = calculate_budget(asix_plc_items, rabaty=rabaty or {})
    budget_hmi = calculate_budget(hmi_plc_items, rabaty=rabaty or {})
    all_budget_items = budget.items + budget_asix.items + budget_cab.items + budget_hmi.items
    df_budget = pd.DataFrame([
        {"Nr katalogowy": it.nr_katalogowy, "Nazwa": it.nazwa, "Ilość": it.ilosc,
         "Jednostka": it.jednostka,
         "Cena katalogowa [PLN]": it.cena_katalogowa,
         "Rabat [%]": it.rabat_pct,
         "Cena netto/szt [PLN]": it.cena_netto_jed,
         "Wartość netto [PLN]": it.wartosc_netto}
        for it in all_budget_items
    ])
    dev_budget_xl = build_device_budget(devices, wycena_akpia_keys or set(), rabaty=rabaty or {})
    df_akpia_urz = pd.DataFrame([
        {"Oznaczenie": it.oznaczenie, "Opis": it.opis, "Ilość": it.ilosc,
         "Cena katalogowa [PLN]": it.cena_katalogowa, "Rabat [%]": it.rabat_pct,
         "Cena netto/szt [PLN]": it.cena_netto_jed, "Wartość netto [PLN]": it.wartosc_netto}
        for it in dev_budget_xl.items
    ] + [{"Oznaczenie": "SUMA", "Cena katalogowa [PLN]": dev_budget_xl.suma_katalogowa,
          "Wartość netto [PLN]": dev_budget_xl.suma_netto}]
    ) if dev_budget_xl.items else pd.DataFrame(
        [{"Oznaczenie": "", "Opis": "Brak zaznaczonych urządzeń (sekcja 1a w aplikacji)"}]
    )

    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df_dev.to_excel(writer, index=False, sheet_name="Urządzenia i sygnały")
        df_io.to_excel(writer, index=False, sheet_name="Bilans I-O")
        df_plc.to_excel(writer, index=False, sheet_name="Sterownik PLC")
        df_cab.to_excel(writer, index=False, sheet_name="Okablowanie")
        df_cabinet.to_excel(writer, index=False, sheet_name="Szafa SAKG")
        df_hmi.to_excel(writer, index=False, sheet_name="HMI")
        df_scada.to_excel(writer, index=False, sheet_name="SCADA ASIX")
        df_cmp.to_excel(writer, index=False, sheet_name="Porównanie wariantów")
        df_budget.to_excel(writer, index=False, sheet_name="Kosztorys")
        df_akpia_urz.to_excel(writer, index=False, sheet_name="Urządzenia AKPiA")
    bio.seek(0)
    return bio


def save_outputs_to_disk(project_label, devices, balance, word_bio, excel_bio):
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    safe = project_label.replace("/", "_").replace("\\", "_")

    # Snapshot danych rdzenia (JSON) do historii - audytowalne, odtwarzalne
    snapshot = {
        "project": project_label,
        "reserve_percent": balance.reserve_percent,
        "balance_base": balance.base,
        "balance_reserved": balance.reserved,
        "devices": devices_to_records(devices),
    }
    with open(os.path.join(HISTORY_DIR, f"{timestamp}_{safe}.json"), "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    with open(os.path.join(OUTPUT_DIR, f"{timestamp}_{safe}.docx"), "wb") as f:
        f.write(word_bio.getvalue())
    with open(os.path.join(OUTPUT_DIR, f"{timestamp}_{safe}.xlsx"), "wb") as f:
        f.write(excel_bio.getvalue())


# --- 5. INTERFEJS (STREAMLIT) ---
def list_excel_sheets(uploaded_file) -> list[str]:
    """Zwraca listę nazw arkuszy w pliku, bez wczytywania danych."""
    uploaded_file.seek(0)
    try:
        xl = pd.ExcelFile(uploaded_file)
        return xl.sheet_names
    except Exception:
        return []


def read_excel_file(uploaded_file, sheet_name=0) -> pd.DataFrame:
    """sheet_name: int (pozycja, domyślnie 0 = pierwszy) lub str (nazwa arkusza)."""
    try:
        uploaded_file.seek(0)
        return pd.read_excel(uploaded_file, sheet_name=sheet_name)
    except ImportError as exc:
        raise ValueError("Brak biblioteki do odczytu Excela (openpyxl/xlrd).") from exc
    except Exception as exc:
        raise ValueError(f"Nie udało się odczytać pliku Excel: {exc}") from exc


def build_project_label(excel_name, pdf_name, sheet_name=None) -> str:
    parts = [os.path.splitext(n)[0] for n in (excel_name, pdf_name) if n]
    label = "_".join(parts) if parts else "Projekt"
    # Dopisujemy nazwę arkusza, jeśli plik ma wiele arkuszy - zapobiega
    # sytuacji, w której dwa raporty z tego samego pliku (różne zakładki)
    # mają identyczną nazwę i łatwo je pomylić.
    if isinstance(sheet_name, str):
        safe_sheet = sheet_name.replace("/", "-").replace("\\", "-")
        label = f"{label}_{safe_sheet}"
    return label


def render_sidebar():
    st.sidebar.title("🛠 Panel Inżyniera AKPiA")
    st.sidebar.markdown("---")
    page = st.sidebar.radio("Tryb działania",
                            ["Analiza Projektu", "Historia Projektów", "Ustawienia API"])
    st.sidebar.markdown("---")
    st.sidebar.markdown("**Parametry Techniczne**")
    platforma = st.sidebar.selectbox("Platforma PLC", list(PLATFORMY.keys()))
    reserve_percent = st.sidebar.slider("Rezerwa sprzętowa [%]", 0, 100, 30, 5)
    cable_length = st.sidebar.slider("Średnia trasa kablowa [m]", 5, 200, 25, 5)
    asix_factor = st.sidebar.slider("Współczynnik zmiennych ASIX", 1.0, 2.0, 1.2, 0.1)

    st.sidebar.markdown("---")
    st.sidebar.markdown("**Rabaty firmowe [%]**")
    rabaty = {}
    for grupa, default in GRUPY_RABATOWE.items():
        rabaty[grupa] = st.sidebar.number_input(
            f"{grupa}", min_value=0, max_value=100, value=default, step=1, key=f"rabat_{grupa}"
        )

    st.sidebar.markdown("---")
    if st.sidebar.button("Wyloguj", use_container_width=True):
        st.session_state.clear()
        st.rerun()
    return page, {"reserve_percent": reserve_percent, "platforma": platforma, "rabaty": rabaty, "cable_length": cable_length, "asix_factor": asix_factor}


def render_extraction_diff_panel() -> None:
    """
    Panel porównania ścieżki OFFLINE i AI - widoczny po użyciu przycisku
    '⚖ Policz + zweryfikuj przez AI'. Pokazuje deterministyczne różnice
    (core.extraction_diff) jako pomoc diagnostyczną, NIGDY jako podstawę
    do automatycznej zmiany liczb. Wybór, którą wersję zatwierdzić do
    dalszej pracy, zawsze należy do inżyniera (przyciski niżej).
    """
    diff: ExtractionDiff = st.session_state.extraction_diff

    st.subheader("⚖ Porównanie: OFFLINE vs AI")

    if diff.identyczne:
        st.success(
            f"✓ Obie ścieżki dają identyczny wynik: {diff.liczba_urzadzen_offline} "
            "urządzeń, identyczny bilans I/O. Ekstrakcja AI zgodna z parserem offline."
        )
        return

    st.warning(
        f"⚠ Wykryto różnice — offline: {diff.liczba_urzadzen_offline} urządzeń, "
        f"AI: {diff.liczba_urzadzen_ai} urządzeń."
    )

    delta_cols = st.columns(4)
    for i, t in enumerate(IO_TYPES):
        d = diff.balans_delta[t]
        delta_cols[i].metric(t, f"{d:+d}" if d != 0 else "0",
                             help="Delta AI minus offline")

    def _render_lista(tytul: str, wpisy: list) -> None:
        if not wpisy:
            return
        st.markdown(f"**{tytul} ({len(wpisy)}):**")
        for e in wpisy:
            st.markdown(f"- **{e.oznaczenie}**: {e.opis} (x{e.ilosc})")

    _render_lista("Tylko w OFFLINE", diff.tylko_w_offline)
    _render_lista("Tylko w AI", diff.tylko_w_ai)

    st.caption(
        "Decyzję, którą wersję zatwierdzić do dalszej pracy, podejmuje inżynier. "
        "Domyślnie do sekcji poniżej trafia wynik OFFLINE (deterministyczny)."
    )
    if st.session_state.get("devices_ai_alternative") is not None:
        if st.button("🔄 Użyj zamiast tego wyniku AI", use_container_width=False):
            devices_ai_wybrane = st.session_state.devices_ai_alternative
            st.session_state.devices = devices_ai_wybrane
            st.rerun()


def render_device_budget_selector(devices, rabaty: dict) -> None:
    """
    Checkbox-lista urządzeń obiektowych do RĘCZNEGO oznaczenia, które wchodzą
    w zakres wyceny AKPiA (typowo: przetworniki pomiarowe). Stan trzymany w
    st.session_state, kluczowany przez device_key() - przetrwa przeliczenia
    w obrębie tej samej sesji, dopóki lista urządzeń się nie zmieni.
    """
    if "wycena_akpia_keys" not in st.session_state:
        st.session_state.wycena_akpia_keys = set()

    rows = []
    for i, d in enumerate(devices):
        key = device_key(d, i)
        rows.append({
            "Wycena AKPiA": key in st.session_state.wycena_akpia_keys,
            "Oznaczenie": d.oznaczenie or "-",
            "Opis": d.opis,
            "Ilość": d.ilosc,
            "_key": key,  # ukryta kolumna pomocnicza, nie do edycji
        })
    df_sel = pd.DataFrame(rows)

    edited = st.data_editor(
        df_sel,
        column_config={
            "Wycena AKPiA": st.column_config.CheckboxColumn(
                "Wycena AKPiA", help="Zaznacz, jeśli to urządzenie ma trafić do kosztorysu AKPiA"
            ),
            "_key": None,  # ukrywa kolumnę techniczną w UI
        },
        disabled=["Oznaczenie", "Opis", "Ilość"],
        hide_index=True,
        use_container_width=True,
        key="device_budget_editor",
    )

    # Synchronizacja stanu na podstawie tego, co inżynier zaznaczył w tabeli
    st.session_state.wycena_akpia_keys = set(
        edited.loc[edited["Wycena AKPiA"], "_key"]
    )

    dev_budget = build_device_budget(devices, st.session_state.wycena_akpia_keys, rabaty=rabaty)
    if dev_budget.items:
        st.caption(f"Zaznaczono {len(dev_budget.items)} pozycji do kosztorysu AKPiA.")
    else:
        st.caption("Brak zaznaczonych pozycji — żadne urządzenie obiektowe nie trafi do kosztorysu.")


def render_results(devices, balance, project_label, platforma, rabaty, cable_length=25, asix_factor=1.2):
    """Sekcja HITL: urządzenia + bilans + PLC + okablowanie + SCADA + porównanie + kosztorys + pliki."""
    st.subheader("1. Zidentyfikowane urządzenia (do weryfikacji)")
    st.caption("Kolumna 'Źródło' pokazuje, czy sygnał pochodzi z danych (kolumna), "
               "czy z reguły typu urządzenia. Zweryfikuj pozycje z uwagami.")
    st.dataframe(build_io_dataframe(devices), use_container_width=True)

    st.subheader("1a. Urządzenia obiektowe wchodzące w zakres wyceny AKPiA")
    st.caption(
        "Większość urządzeń obiektowych (pompy, zawory, siłowniki) fizycznie "
        "znajduje się na hali i jest dostarczana/wyceniana poza automatyką - "
        "program ich NIE wycenia automatycznie. Zaznacz ręcznie te pozycje "
        "(typowo: przetworniki pomiarowe), które WCHODZĄ w zakres dostawy "
        "AKPiA i mają trafić do kosztorysu."
    )
    render_device_budget_selector(devices, rabaty)

    st.subheader("2. Bilans sygnałów I/O")
    cols = st.columns(len(IO_TYPES) + 1)
    for i, t in enumerate(IO_TYPES):
        cols[i].metric(t, balance.base[t], f"+{balance.reserved[t] - balance.base[t]} rez.")
    cols[-1].metric("RAZEM", balance.base_total, f"→ {balance.reserved_total}")

    if balance.undecided:
        st.warning(f"⚠ {len(balance.undecided)} sygnał(ów) BRAK DANYCH - wymaga decyzji inżyniera "
                   f"(szczegóły w raporcie).")
    inferred = balance.source_counts.get("typ_urzadzenia", 0)
    if inferred:
        st.info(f"ℹ {inferred} sygnał(ów) wywnioskowano z typu urządzenia (puste kolumny). Zweryfikuj.")

    st.subheader(f"3. Dobór sterownika ({platforma})")
    st.caption("Reguły zweryfikowane na realnych projektach (Wujek, TOM, Malbork).")
    sel = select_plc(balance, platforma)
    df_plc = pd.DataFrame([
        {"Ilość": it.ilosc, "Nr katalogowy": it.nr, "Opis": it.opis} for it in sel.items
    ])
    st.dataframe(df_plc, use_container_width=True)
    util_cols = st.columns(len(IO_TYPES))
    for i, t in enumerate(IO_TYPES):
        u = sel.utilization.get(t, {})
        util_cols[i].metric(f"{t} karty", u.get("kart", 0),
                            f"zapas {u.get('zapas_kanałów', 0)} kan.")

    st.subheader("4. Zestawienie kablowe")
    cab = select_cables(devices, srednia_trasa_m=cable_length)
    df_cab = pd.DataFrame([
        {"Typ sygnału": it.typ_sygnalu, "Typ kabla": it.typ_kabla,
         "Urządzeń": it.ilosc_urzadzen, "Metraż [m]": it.metraz_m}
        for it in cab.items
    ])
    st.dataframe(df_cab, use_container_width=True)
    st.metric("Razem metraż", f"{cab.total_metraz:.0f} m",
              f"(śr. trasa {cable_length}m, naddatek +15%)")

    st.subheader("5. SCADA ASIX")
    asix = select_asix(balance, wspolczynnik=asix_factor)
    scada_cols = st.columns(3)
    scada_cols[0].metric("Sygnałów I/O", asix.zmienne_io)
    scada_cols[1].metric("Zmiennych procesowych", asix.zmienne_obliczone,
                         f"×{asix.wspolczynnik}")
    scada_cols[2].metric("Pakiet licencyjny", asix.prog_nazwa)
    st.info(f"💡 Sugestia architektury: {asix.sugestia_opis}")
    if asix.items:
        df_asix = pd.DataFrame([
            {"Nr katalogowy": it.nr_katalogowy, "Nazwa": it.nazwa,
             "Ilość": it.ilosc,
             "Cena kat. [PLN]": f"{it.cena_katalogowa:.2f}" if it.cena_katalogowa else "BRAK"}
            for it in asix.items
        ])
        st.dataframe(df_asix, use_container_width=True)
    for w in asix.warnings:
        st.warning(w)

    st.subheader("6. HMI — panele operatorskie lokalne")
    st.caption("Traktowane OSOBNO od SCADA/ASIX (wytyczna: HMI = sterowanie lokalne, "
               "ASIX = stacja operatorska/serwer). Wybór manualny — brak zwalidowanego "
               "wzorca doboru w dokumentacji firmy, więc inżynier dodaje pozycje ręcznie.")

    if "hmi_entries" not in st.session_state:
        st.session_state.hmi_entries = []

    with st.form("hmi_add_form", clear_on_submit=True):
        hc1, hc2, hc3, hc4 = st.columns([3, 1, 2, 1])
        with hc1:
            hmi_nazwa = st.selectbox("Model panelu", TYPOWE_PANELE, key="hmi_model_select")
            hmi_nazwa_custom = st.text_input("...lub wpisz własny model", key="hmi_model_custom")
        with hc2:
            hmi_ilosc = st.number_input("Ilość", min_value=1, value=1, key="hmi_ilosc_input")
        with hc3:
            hmi_lok = st.text_input("Lokalizacja", key="hmi_lok_input",
                                    placeholder="np. szafa sterownicza")
        with hc4:
            st.markdown("&nbsp;")
            hmi_submit = st.form_submit_button("➕ Dodaj")
        if hmi_submit:
            final_nazwa = hmi_nazwa_custom.strip() or hmi_nazwa
            if final_nazwa and final_nazwa != "Inny / wpisz ręcznie":
                st.session_state.hmi_entries.append(
                    {"nazwa": final_nazwa, "ilosc": hmi_ilosc, "lokalizacja": hmi_lok}
                )

    hmi_sel = build_hmi_selection(st.session_state.hmi_entries)
    if hmi_sel.items:
        df_hmi = pd.DataFrame([
            {"Ilość": it.ilosc, "Model": it.nazwa, "Lokalizacja": it.lokalizacja}
            for it in hmi_sel.items
        ])
        st.dataframe(df_hmi, use_container_width=True)
        if st.button("🗑 Wyczyść listę HMI"):
            st.session_state.hmi_entries = []
            st.rerun()
    else:
        st.caption("Brak dodanych paneli HMI — poprawny stan, jeśli projekt ich nie wymaga.")

    st.subheader("7. Szafa sterownicza (+SAKG)")
    cab_sel = select_cabinet(balance, sel)
    df_cab_items = pd.DataFrame([
        {"Ilość": it.ilosc, "Nr katalogowy": it.nr_katalogowy,
         "Nazwa": it.nazwa, "Reguła": it.uwaga}
        for it in cab_sel.items
    ])
    st.dataframe(df_cab_items, use_container_width=True)
    pw = st.columns(4)
    pw[0].metric("Karty PLC", f"{cab_sel.prad_karty_ma} mA")
    pw[1].metric("Przekaźniki", f"{cab_sel.prad_przekazniki_ma} mA")
    pw[2].metric("Przetworniki", f"{cab_sel.prad_przetworniki_ma} mA")
    pw[3].metric("Zasilacz 24V", f"{cab_sel.zasilacz_a} A",
                 f"bilans {cab_sel.prad_z_zapasem_a} A")
    for w in cab_sel.warnings:
        st.info(f"ℹ {w}")

    st.subheader("8. Porównanie wariantów sterowników")
    st.caption("Ten sam bilans I/O — trzy platformy obok siebie.")
    variants = compare_variants(balance, rabaty=rabaty)
    df_cmp = pd.DataFrame([
        {"Platforma": v.platforma, "CPU": v.cpu,
         "Karty DI": v.karty_io.get("DI", 0), "Karty DO": v.karty_io.get("DO", 0),
         "Karty AI": v.karty_io.get("AI", 0), "Karty AO": v.karty_io.get("AO", 0),
         "Moduły łącznie": v.total_modules,
         "Netto [PLN]": f"{v.suma_netto:,.2f}" if v.suma_netto > 0 else "brak cen"}
        for v in variants
    ])
    st.dataframe(df_cmp, use_container_width=True)

    st.subheader("9. Kosztorys")
    budget = calculate_budget(sel.items, rabaty=rabaty)
    df_budget = pd.DataFrame([
        {
            "Nr katalogowy": it.nr_katalogowy,
            "Nazwa": it.nazwa,
            "Ilość": it.ilosc,
            "Cena kat. [PLN]": f"{it.cena_katalogowa:.2f}" if it.cena_katalogowa else "BRAK",
            "Rabat [%]": f"{it.rabat_pct:.0f}",
            "Netto/szt [PLN]": f"{it.cena_netto_jed:.2f}" if it.cena_netto_jed else "-",
            "Wartość netto [PLN]": f"{it.wartosc_netto:.2f}" if it.wartosc_netto else "-",
        }
        for it in budget.items
    ])
    st.dataframe(df_budget, use_container_width=True)

    sum_cols = st.columns(2)
    sum_cols[0].metric("Suma katalogowa", f"{budget.suma_katalogowa:,.2f} PLN")
    sum_cols[1].metric("Suma netto (po rabatach)", f"{budget.suma_netto:,.2f} PLN")

    if budget.brak_ceny:
        st.warning(f"⚠ {len(budget.brak_ceny)} pozycji bez ceny katalogowej — "
                   "uzupełnij cennik, aby uzyskać pełny kosztorys.")

    st.subheader("9a. Kosztorys urządzeń AKPiA (wybór ręczny)")
    st.caption("Pozycje zaznaczone w sekcji 1a — osobno od sprzętu sterowniczego, "
               "bo dotyczą urządzeń obiektowych (np. przetworników), a nie kart PLC/szafy/SCADA.")
    dev_budget = build_device_budget(devices, st.session_state.get("wycena_akpia_keys", set()), rabaty=rabaty)
    if dev_budget.items:
        df_dev_budget = pd.DataFrame([
            {
                "Oznaczenie": it.oznaczenie,
                "Opis": it.opis,
                "Ilość": it.ilosc,
                "Cena kat. [PLN]": f"{it.cena_katalogowa:.2f}" if it.cena_katalogowa else "BRAK",
                "Rabat [%]": f"{it.rabat_pct:.0f}",
                "Wartość netto [PLN]": f"{it.wartosc_netto:.2f}" if it.wartosc_netto else "-",
            }
            for it in dev_budget.items
        ])
        st.dataframe(df_dev_budget, use_container_width=True)
        sum_cols2 = st.columns(2)
        sum_cols2[0].metric("Suma katalogowa (AKPiA)", f"{dev_budget.suma_katalogowa:,.2f} PLN")
        sum_cols2[1].metric("Suma netto (AKPiA)", f"{dev_budget.suma_netto:,.2f} PLN")
        if dev_budget.brak_ceny:
            st.warning(f"⚠ {len(dev_budget.brak_ceny)} pozycji bez ceny katalogowej — "
                       "cennik nie zawiera jeszcze urządzeń obiektowych, uzupełnij ręcznie.")
    else:
        st.caption("Brak zaznaczonych urządzeń — sekcja 1a pozwala je dodać.")

    st.subheader("10. Weryfikacja kompletności oferty")
    cab_wires = select_cables(devices, srednia_trasa_m=cable_length)
    asix_val = select_asix(balance, wspolczynnik=asix_factor)
    budget_val = calculate_budget(sel.items, rabaty=rabaty or {})
    val_report = validate_offer(devices, balance, sel, cab_wires, cab_sel, asix_val, budget_val)

    if val_report.is_clean:
        st.success("✓ Brak zastrzeżeń — oferta wygląda na spójną.")
    else:
        for issue in val_report.errors:
            st.error(f"🔴 **[{issue.category}]** {issue.message}")
        for issue in val_report.warnings:
            st.warning(f"🟡 **[{issue.category}]** {issue.message}")
        for issue in val_report.infos:
            st.info(f"ℹ️ **[{issue.category}]** {issue.message}")
        st.caption("Powyższe to sugestie do weryfikacji — nie blokują generowania dokumentów. "
                   "Ostateczna decyzja należy do inżyniera.")

    st.subheader("11. Pobierz dokumenty")
    word_bio = create_word_report(devices, balance, project_label, platforma, rabaty, st.session_state.get("hmi_entries", []), st.session_state.get("wycena_akpia_keys", set()))
    excel_bio = create_devices_excel(devices, balance, platforma, rabaty, cable_length, asix_factor, st.session_state.get("hmi_entries", []), st.session_state.get("wycena_akpia_keys", set()))

    # Dane potrzebne dla PDF (te same co dla Word/Excel, budowane raz)
    sel_for_pdf = select_plc(balance, platforma)
    cab_for_pdf = select_cabinet(balance, sel_for_pdf)
    asix_for_pdf = select_asix(balance, wspolczynnik=asix_factor)
    budget_for_pdf = calculate_budget(sel_for_pdf.items, rabaty=rabaty or {})
    dev_budget_for_pdf = build_device_budget(
        devices, st.session_state.get("wycena_akpia_keys", set()), rabaty=rabaty or {}
    )
    pdf_bio = create_pdf_report(
        devices, balance, project_label, platforma,
        sel_for_pdf, cab_for_pdf, asix_for_pdf, budget_for_pdf, IO_TYPES,
        dev_budget=dev_budget_for_pdf,
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        st.download_button("📄 Raport (Word)", data=word_bio,
                           file_name=f"Raport_{project_label}.docx", use_container_width=True)
    with c2:
        st.download_button("📊 Zestawienie (Excel)", data=excel_bio,
                           file_name=f"Zestawienie_{project_label}.xlsx",
                           type="primary", use_container_width=True)
    with c3:
        st.download_button("📕 Raport (PDF)", data=pdf_bio,
                           file_name=f"Raport_{project_label}.pdf", use_container_width=True)


def main():
    st.set_page_config(page_title="Panel Inżyniera AKPiA", layout="wide")
    if not check_password():
        st.stop()

    for key, default in [("api_key_override", ""), ("devices", None),
                         ("project_label", None), ("current_file", (None, None)),
                         ("extraction_diff", None),
                         ("devices_ai_alternative", None)]:
        if key not in st.session_state:
            st.session_state[key] = default

    page, settings = render_sidebar()
    api_key = get_api_key()

    st.title("Panel Inżyniera AKPiA")
    st.warning("**Human-in-the-Loop:** AI jedynie ekstrahuje listę urządzeń. "
               "Zliczanie I/O i dobór wykonuje deterministyczny rdzeń. "
               "Ostateczna weryfikacja należy do inżyniera.")

    if page == "Analiza Projektu":
        st.header("Analiza Projektu")
        st.caption("Wgraj zestawienie (Excel) i/lub dokumentację (PDF). "
                   "AI zbuduje listę urządzeń, rdzeń policzy sygnały I/O.")

        c1, c2 = st.columns(2)
        with c1:
            excel_file = st.file_uploader("Zestawienie urządzeń (Excel)", type=["xlsx", "xls"], key="xl")
        with c2:
            pdf_file = st.file_uploader("Dokumentacja (PDF)", type=["pdf"], key="pdf")

        # Wybór arkusza - plik może mieć kilka zakładek, aplikacja musi wiedzieć,
        # z której czytać (wcześniej zawsze brała pierwszą po cichu, co mylące
        # przy plikach wieloarkuszowych - patrz historia konwersacji).
        selected_sheet = 0
        if excel_file:
            sheets = list_excel_sheets(excel_file)
            if len(sheets) > 1:
                selected_sheet = st.selectbox(
                    "Arkusz do wczytania", sheets, index=0, key="sheet_select",
                    help="Plik ma więcej niż jeden arkusz. Wybierz właściwy - "
                         "domyślnie wczytywany jest pierwszy od lewej.",
                )
            elif sheets:
                selected_sheet = sheets[0]

        current_files = (excel_file.name if excel_file else None,
                         pdf_file.name if pdf_file else None, selected_sheet)
        if current_files != st.session_state.current_file:
            st.session_state.devices = None
            st.session_state.current_file = current_files
            # Nowy plik -> poprzednie porównanie offline/AI dotyczyło innych
            # danych, więc traci sens i musi zniknąć razem z devices.
            st.session_state.extraction_diff = None
            st.session_state.devices_ai_alternative = None

        excel_df = None
        if excel_file:
            try:
                excel_df = read_excel_file(excel_file, sheet_name=selected_sheet)
                sheet_label = f"„{selected_sheet}”" if isinstance(selected_sheet, str) else ""
                total_rows = len(excel_df)
                if total_rows > 10:
                    st.markdown(f"**Podgląd arkusza {sheet_label}** — pokazano pierwsze 10 "
                               f"z **{total_rows}** wierszy. Analiza uwzględni wszystkie {total_rows}.")
                else:
                    st.markdown(f"**Podgląd arkusza {sheet_label}** — {total_rows} wierszy (wszystkie widoczne).")
                st.dataframe(excel_df.head(10), use_container_width=True)
            except ValueError as exc:
                st.error(str(exc))

        # --- Ścieżka bez AI: parsuj Excel bezpośrednio rdzeniem ---
        if excel_df is not None:
            if st.button("⚙ Policz I/O z Excela (bez AI)", use_container_width=True):
                devices, warns = parse_devices(excel_df)
                st.session_state.devices = devices
                st.session_state.project_label = build_project_label(current_files[0], current_files[1], selected_sheet)
                st.session_state.extraction_diff = None  # nowa ścieżka - wyczyść poprzednie porównanie
                for w in warns:
                    st.warning(w)

        # --- Ścieżka z AI: ekstrakcja JSON, potem rdzeń ---
        if not excel_file and not pdf_file:
            st.info("Wgraj co najmniej jeden plik, aby rozpocząć.")
        elif not api_key:
            st.error("Brak klucza API Gemini. Skonfiguruj w Ustawieniach lub secrets.toml.")
        elif st.button("🤖 Ekstrahuj urządzenia przez AI, potem policz I/O", type="primary",
                       use_container_width=True):
            with st.spinner("AI ekstrahuje listę urządzeń..."):
                try:
                    records = run_extraction(
                        api_key=api_key, excel_df=excel_df,
                        pdf_bytes=pdf_file.getvalue() if pdf_file else None,
                        excel_filename=current_files[0], pdf_filename=current_files[1],
                    )
                    devices, warns = parse_ai_devices(records)
                    st.session_state.devices = devices
                    st.session_state.project_label = build_project_label(current_files[0], current_files[1], selected_sheet)
                    st.session_state.extraction_diff = None  # nowa ścieżka - wyczyść poprzednie porównanie
                    for w in warns:
                        st.warning(w)
                except Exception as exc:
                    st.error(f"Błąd ekstrakcji: {exc}")

        # --- Ścieżka weryfikacyjna: obie metody naraz + porównanie (opcjonalna, kosztowa) ---
        if excel_df is not None:
            if not api_key:
                st.caption("⚖ Weryfikacja przez AI wymaga klucza API Gemini (patrz wyżej).")
            elif st.button("⚖ Policz + zweryfikuj przez AI", use_container_width=True,
                           help="Uruchamia OBIE metody (offline i AI) na tych samych danych "
                                "i pokazuje różnice. Zużywa dodatkowe zapytania do API - "
                                "użyj, gdy chcesz sprawdzić jakość ekstrakcji, nie przy każdej "
                                "iteracji."):
                with st.spinner("Liczenie offline + ekstrakcja AI + porównanie..."):
                    try:
                        devices_off, warns_off = parse_devices(excel_df)
                        records = run_extraction(
                            api_key=api_key, excel_df=excel_df,
                            pdf_bytes=pdf_file.getvalue() if pdf_file else None,
                            excel_filename=current_files[0], pdf_filename=current_files[1],
                        )
                        devices_ai, warns_ai = parse_ai_devices(records)

                        diff = compare_extractions(devices_off, devices_ai)
                        st.session_state.extraction_diff = diff

                        # Domyślnie do dalszej pracy bierzemy wynik OFFLINE (deterministyczny,
                        # bezpieczniejszy domyślny wybór) - inżynier może ręcznie przełączyć
                        # na wynik AI w sekcji porównania niżej.
                        st.session_state.devices = devices_off
                        st.session_state.project_label = build_project_label(
                            current_files[0], current_files[1], selected_sheet
                        )
                        st.session_state.devices_ai_alternative = devices_ai
                        for w in warns_off:
                            st.warning(w)
                    except Exception as exc:
                        st.error(f"Błąd weryfikacji: {exc}")

        # --- Panel porównania (widoczny tylko po użyciu przycisku weryfikacji) ---
        if st.session_state.get("extraction_diff") is not None:
            render_extraction_diff_panel()

        # --- Wyniki + zapis (wspólne dla obu ścieżek) ---
        if st.session_state.devices is not None:
            devices = st.session_state.devices
            balance = count_io(devices, reserve_percent=settings["reserve_percent"])
            render_results(devices, balance, st.session_state.project_label,
                          settings['platforma'], settings['rabaty'],
                          settings['cable_length'], settings['asix_factor'])

            # Zapis na dysk (raz)
            try:
                word_bio = create_word_report(devices, balance, st.session_state.project_label, settings['platforma'], settings['rabaty'], st.session_state.get('hmi_entries', []), st.session_state.get('wycena_akpia_keys', set()))
                excel_bio = create_devices_excel(devices, balance, settings['platforma'], settings['rabaty'], settings['cable_length'], settings['asix_factor'], st.session_state.get('hmi_entries', []), st.session_state.get('wycena_akpia_keys', set()))
                save_outputs_to_disk(st.session_state.project_label, devices, balance, word_bio, excel_bio)
            except Exception as exc:
                st.error(f"Błąd zapisu plików: {exc}")

    elif page == "Historia Projektów":
        st.header("Archiwum (snapshoty JSON)")
        files = sorted([f for f in os.listdir(HISTORY_DIR) if f.endswith(".json")], reverse=True)
        if not files:
            st.info("Brak zapisanych projektów.")
        else:
            sel = st.selectbox("Wybierz analizę:", ["-- Wybierz --"] + files)
            if sel != "-- Wybierz --":
                with open(os.path.join(HISTORY_DIR, sel), encoding="utf-8") as f:
                    snap = json.load(f)
                st.json({"project": snap["project"], "reserve_percent": snap["reserve_percent"],
                         "balance_base": snap["balance_base"], "balance_reserved": snap["balance_reserved"]})
                st.dataframe(pd.DataFrame(snap["devices"]), use_container_width=True)

    elif page == "Ustawienia API":
        st.header("Konfiguracja klucza Gemini")
        st.info("Klucz z `.streamlit/secrets.toml` ładuje się automatycznie. Tutaj nadpiszesz go na czas sesji.")
        st.session_state.api_key_override = st.text_input(
            "Tymczasowy klucz API", value=st.session_state.api_key_override, type="password")


if __name__ == "__main__":
    main()
