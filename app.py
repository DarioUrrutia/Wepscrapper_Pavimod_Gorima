"""
ANAS Lavori in Corso — Monitor Web  (PAVIMOD)
"""

import sys
# Forza stdout/stderr in UTF-8 così i print() dei thread di background
# (scraper, enriquecedor) non crashano su console Windows cp1252 quando
# contengono caratteri speciali come —, →, €, ecc.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import streamlit as st
import threading
import time
import re
import pandas as pd
from pathlib import Path
from PIL import Image
import _state

# ---------------------------------------------------------------------------
# Configurazione pagina
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="ANAS Lavori Monitor — PAVIMOD",
    page_icon="🏗️",
    layout="wide",
    initial_sidebar_state="expanded",
)

PASSWORD_SCRAPING = "Pavimodvai"

PROCESSED_DIR = Path("data/processed")
MASTER_FILE   = PROCESSED_DIR / "master_avanzamento.xlsx"
AVANZ_PREFIX  = "Avanz_"
PAVIMOD_RED   = "#CC2229"
PAVIMOD_GRAY  = "#6D6E71"

PASSI_SCRAPING = [(0.55, "ANAS"), (0.88, "Filtro + DataFrame"), (1.00, "Salvataggio")]
PASSI_COMP     = [(0.15, "CSV"), (0.70, "Elaborazione"), (0.88, "Differenze"), (1.00, "Salvataggio")]
PASSI_ENRICH   = [(0.80, "OpenCUP"), (1.00, "Master")]

# ---------------------------------------------------------------------------
# Progresso thread-safe — riferimenti agli oggetti in _state.py
# (persistono tra i rerun perché _state viene importato una sola volta)
# ---------------------------------------------------------------------------
_scraper_prog = _state.scraper
_comp_prog    = _state.comp
_enrich_prog  = _state.enrich

# ---------------------------------------------------------------------------
# Reset _state dicts una sola volta per sessione (previene stati "sporchi"
# da sessioni precedenti che avevano done=True o running=True)
# ---------------------------------------------------------------------------
if "session_initialized" not in st.session_state:
    st.session_state.session_initialized = True
    for _d in [_scraper_prog, _comp_prog, _enrich_prog]:
        _d.update({"pct": 0.0, "msg": "", "running": False, "error": None, "done": False})

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
for k, v in {
    "scraper_was_running": False,
    "comp_was_running":    False,
    "enrich_was_running":  False,
    "col_selection":       [],
    "notif_scraping":      None,   # ("success"|"error", msg)
    "notif_comp":          None,   # ("success"|"error", msg)
    "notif_enrich":        None,   # ("success"|"error", msg)
    "notif_delete":        None,   # ("success"|"error", msg)
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ---------------------------------------------------------------------------
# CSS PAVIMOD
# ---------------------------------------------------------------------------
st.markdown(f"""
<style>
.stButton > button[kind="primary"] {{
    background-color:{PAVIMOD_RED}; border-color:{PAVIMOD_RED}; color:white; font-weight:600;
}}
.stButton > button[kind="primary"]:hover {{ background-color:#a51c21; }}
.stProgress > div > div > div > div {{ background-color:{PAVIMOD_RED}; }}
[data-testid="stMetricValue"] {{ font-size:1.8rem; font-weight:700; color:{PAVIMOD_RED}; }}
[data-testid="stMetricLabel"] {{ color:{PAVIMOD_GRAY}; font-size:0.85rem; }}

/* Scrollbar più visibile con colori PAVIMOD (senza toccare layout) */
::-webkit-scrollbar {{ width: 10px; height: 12px; }}
::-webkit-scrollbar-track {{ background: #f1f1f1; border-radius: 4px; }}
::-webkit-scrollbar-thumb {{ background: {PAVIMOD_GRAY}; border-radius: 4px; }}
::-webkit-scrollbar-thumb:hover {{ background: {PAVIMOD_RED}; }}

/* Download button in grigio soft */
[data-testid="stDownloadButton"] > button {{
    background-color: #e8e8e8 !important;
    color: #555 !important;
    border: 1px solid #d0d0d0 !important;
    font-weight: 500 !important;
}}
[data-testid="stDownloadButton"] > button:hover {{
    background-color: #dcdcdc !important;
    color: #333 !important;
    border-color: #bcbcbc !important;
}}
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Utilità
# ---------------------------------------------------------------------------
def load_master():
    if not MASTER_FILE.exists():
        return None
    return pd.read_excel(MASTER_FILE, dtype=str).fillna("")

def cols_avanz(df):
    avanz = [c for c in df.columns if c.startswith(AVANZ_PREFIX)]
    def _key(c):
        try:
            parte = c.replace(AVANZ_PREFIX, "")
            data  = parte.split("_")[0]
            hhmm  = parte.split("_")[1] if "_" in parte else "0000"
            d, m, y = data.split("-")
            return (int(y), int(m), int(d), int(hhmm))
        except Exception:
            return (0, 0, 0, 0)
    return sorted(avanz, key=_key)

def parse_pct(val):
    try:
        return float(str(val).replace("%", "").replace(",", ".").strip())
    except Exception:
        return None

def ultimo_csv():
    csvs = sorted(PROCESSED_DIR.glob("anas_obras_*.csv"))
    return csvs[-1] if csvs else None

def ya_comparado():
    """True se il CSV più recente è già stato comparato (master più recente del CSV)."""
    csv = ultimo_csv()
    if not csv or not MASTER_FILE.exists():
        return False
    return MASTER_FILE.stat().st_mtime >= csv.stat().st_mtime

def _coord_to_maps(coord):
    """Converte 'lat, lng' in URL Google Maps. Restituisce stringa vuota se non valido."""
    if not coord or str(coord).strip() in ("", "None", "nan"):
        return ""
    try:
        parts = str(coord).replace(";", ",").split(",")
        lat, lng = parts[0].strip(), parts[1].strip()
        float(lat); float(lng)   # valida che siano numeri
        return f"https://www.google.com/maps?q={lat},{lng}"
    except Exception:
        return ""

def render_passi(pct, passi):
    prev, html = 0.0, '<div style="display:flex;gap:20px;margin:4px 0 8px 0;flex-wrap:wrap">'
    for soglia, label in passi:
        if pct >= soglia:
            ico, col = "✅", "#28a745"
        elif pct >= prev:
            ico, col = "🔄", PAVIMOD_RED
        else:
            ico, col = "○", PAVIMOD_GRAY
        html += f'<span style="color:{col};font-size:0.82rem;font-weight:600">{ico} {label}</span>'
        prev = soglia
    return html + "</div>"

def _render_progress(prog, passi, label):
    pct = min(prog["pct"], 1.0)
    st.markdown(f"**{label}** — {int(pct*100)}%")
    st.progress(pct, text=prog["msg"])
    st.markdown(render_passi(pct, passi), unsafe_allow_html=True)
    if prog["error"]:
        st.error(prog["error"])
        return False
    if prog["done"]:
        st.success(prog["msg"])
        return False
    if prog["running"]:
        return True   # ancora in corso
    return False

# ---------------------------------------------------------------------------
# Thread di sfondo
# ---------------------------------------------------------------------------
def _hilo_scraper():
    try:
        from scraper import scrape
        scrape(progress_callback=lambda p, m: _scraper_prog.update({"pct": p, "msg": m}))
        _scraper_prog.update({"pct": 1.0, "msg": "Scraping completato", "done": True})
    except Exception as e:
        _scraper_prog.update({"error": str(e), "msg": f"Errore: {e}"})
    finally:
        _scraper_prog["running"] = False

def _hilo_comp():
    try:
        from comparador import actualizar_master
        csv = ultimo_csv()
        if not csv:
            _comp_prog.update({"error": "Nessun CSV. Eseguire prima lo scraping."})
            return
        actualizar_master(csv, progress_callback=lambda p, m: _comp_prog.update({"pct": p, "msg": m}))
        _comp_prog.update({"pct": 1.0, "msg": "Comparativo generato", "done": True})
    except Exception as e:
        _comp_prog.update({"error": str(e), "msg": f"Errore: {e}"})
    finally:
        _comp_prog["running"] = False

def _hilo_enrich(cups):
    try:
        from enriquecedor import enriquecer_obras
        enriquecer_obras(cups, progress_callback=lambda p, m: _enrich_prog.update({"pct": p, "msg": m}))
        _enrich_prog.update({"pct": 1.0, "msg": f"{len(cups)} CUP arricchiti e salvati", "done": True})
    except Exception as e:
        _enrich_prog.update({"error": str(e), "msg": f"Errore: {e}"})
    finally:
        _enrich_prog["running"] = False

# ---------------------------------------------------------------------------
# HEADER
# ---------------------------------------------------------------------------
h1, h2 = st.columns([1, 7])
with h1:
    try:
        st.image(Image.open("logo.png"), width=110)
    except Exception:
        st.markdown("🏗️")
with h2:
    st.markdown("## ANAS Lavori in Corso")
    st.markdown(
        f'<span style="color:{PAVIMOD_GRAY};font-size:0.9rem">'
        "Monitor opere pubbliche · Sicilia · Basilicata · Puglia · Calabria · Molise · Campania"
        "</span>", unsafe_allow_html=True
    )
st.divider()

# ---------------------------------------------------------------------------
# BOTTONI AZIONE
# ---------------------------------------------------------------------------
b1, b2, binfo = st.columns([1, 1, 2])
with b1:
    btn_scraping = st.button(
        "⏳ Scrappeando..." if _scraper_prog["running"] else "▶ Esegui Scraping",
        type="primary", use_container_width=True,
        disabled=_scraper_prog["running"] or _comp_prog["running"],
    )
with b2:
    _gia_comp = ya_comparado()
    _lbl_comp = (
        "⏳ Elaborando..."   if _comp_prog["running"] else
        "✅ Già comparato"   if _gia_comp             else
        "⚡ Genera Comparativo"
    )
    _help_comp = (
        "Comparativo già generato per questo scraping. Eseguire un nuovo scraping per comparare." if _gia_comp else
        "Eseguire prima lo scraping" if not ultimo_csv() else ""
    )
    btn_comp = st.button(
        _lbl_comp,
        type="secondary", use_container_width=True,
        disabled=_comp_prog["running"] or _scraper_prog["running"] or (ultimo_csv() is None) or _gia_comp,
        help=_help_comp,
    )
with binfo:
    csv = ultimo_csv()
    if csv:
        st.info(f"📄 Ultimo CSV: `{csv.name}`")
    else:
        st.warning("⚠️ Nessun dato disponibile.")

# — password dialog per scraping —
if btn_scraping and not _scraper_prog["running"]:
    st.session_state.mostra_pwd = True

if st.session_state.get("mostra_pwd") and not _scraper_prog["running"]:
    with st.form("form_pwd_scraping", clear_on_submit=True):
        pwd = st.text_input("🔒 Password per avviare lo scraping",
                            type="password", placeholder="Inserisci password...")
        submitted = st.form_submit_button("✔ Conferma", type="primary")

    if submitted:
        if pwd == PASSWORD_SCRAPING:
            st.session_state.mostra_pwd = False
            _scraper_prog.update({"pct": 0.0, "msg": "Connessione ad ANAS...",
                                  "running": True, "error": None, "done": False})
            st.session_state.scraper_was_running = True
            threading.Thread(target=_hilo_scraper, daemon=True).start()
            st.rerun()
        else:
            st.error("❌ Password errata.")

# — avvio comparativo —
if btn_comp and not _comp_prog["running"]:
    st.session_state.notif_scraping = None   # rimuove notifica scraping al click
    _comp_prog.update({"pct": 0.0, "msg": "Avvio comparativo...",
                       "running": True, "error": None, "done": False})
    st.session_state.comp_was_running = True
    threading.Thread(target=_hilo_comp, daemon=True).start()
    st.rerun()

# ---------------------------------------------------------------------------
# BARRE DI AVANZAMENTO  (while-loop + st.empty — aggiornamento in tempo reale)
# ---------------------------------------------------------------------------
def _mostra_progresso_live(prog, passi, label, was_running_key, notif_key=None):
    """
    Mostra la barra in tempo reale.
    Al termine (done o error) salva la notifica in session_state e fa rerun
    immediatamente — nessun sleep bloccante.
    """
    if not (prog["running"] or prog["done"] or prog["error"]):
        return

    placeholder = st.empty()

    # — Polling live mentre il thread gira —
    while prog["running"]:
        pct = min(prog["pct"], 1.0)
        with placeholder.container():
            st.markdown(f"**{label}** — {int(pct * 100)}%")
            st.progress(pct, text=prog["msg"])
            st.markdown(render_passi(pct, passi), unsafe_allow_html=True)
        time.sleep(0.3)

    # — Thread terminato: mostra stato finale un frame —
    pct = min(prog["pct"], 1.0)
    with placeholder.container():
        st.markdown(f"**{label}** — {int(pct * 100)}%")
        st.progress(pct, text=prog["msg"])
        st.markdown(render_passi(pct, passi), unsafe_allow_html=True)
        if prog["error"]:
            st.error(prog["error"])
        elif prog["done"]:
            st.success(prog["msg"])

    # — Salva notifica e resetta stato —
    if st.session_state.get(was_running_key):
        st.session_state[was_running_key] = False
        if notif_key:
            if prog["error"]:
                st.session_state[notif_key] = ("error", prog["error"])
            elif prog["done"]:
                st.session_state[notif_key] = ("success", prog["msg"])
        prog.update({"pct": 0.0, "msg": "", "done": False, "error": None})
        st.rerun()

_mostra_progresso_live(_scraper_prog, PASSI_SCRAPING, "Scraping ANAS",          "scraper_was_running", "notif_scraping")
_mostra_progresso_live(_comp_prog,    PASSI_COMP,     "Generazione Comparativo", "comp_was_running",    "notif_comp")
_mostra_progresso_live(_enrich_prog,  PASSI_ENRICH,   "Arricchimento OpenCUP",   "enrich_was_running",  "notif_enrich")

# ---------------------------------------------------------------------------
# NOTIFICHE DI SESSIONE
# scraping: scompare al click su Genera Comparativo
# comp: rimane per la sessione, area notifiche (info non invasiva)
# ---------------------------------------------------------------------------
_ns = st.session_state.get("notif_scraping")
if _ns:
    _typ, _msg = _ns
    if _typ == "success":
        st.success(f"✅ {_msg} — clicca ⚡ **Genera Comparativo** per aggiornare il master")
    else:
        st.error(f"❌ {_msg}")

_nc = st.session_state.get("notif_comp")
if _nc:
    _typ, _msg = _nc
    if _typ == "success":
        st.info(f"ℹ️ {_msg}")
    else:
        st.error(f"❌ {_msg}")

st.divider()

# ---------------------------------------------------------------------------
# CARICAMENTO DATI
# ---------------------------------------------------------------------------
df = load_master()
if df is None:
    st.info("Nessun dato. Eseguire lo scraping e generare il comparativo.")
    st.stop()

av_cols   = cols_avanz(df)
last_avanz = av_cols[-1] if av_cols else None

if last_avanz:
    df["_avanz_num"] = df[last_avanz].apply(parse_pct)
if "Importo_Totale" in df.columns:
    df["_importo_num"] = df["Importo_Totale"].apply(
        lambda v: float(str(v).replace(",", ".").strip()) if v else None
    )

# ---------------------------------------------------------------------------
# METRICHE
# ---------------------------------------------------------------------------
total     = len(df)
nuevas    = int((df.get("Differenza", pd.Series(dtype=str)) == "Obra Nueva").sum())
conclusas = int(df.get("Differenza", pd.Series(dtype=str)).isin(["Obra Conclusa", "Obra Conclusa (probable)"]).sum())
desapar   = int((df.get("Differenza", pd.Series(dtype=str)) == "Obra Desaparecida").sum())
imp_tot   = df["_importo_num"].sum() if "_importo_num" in df.columns else 0

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Totale Opere",        f"{total:,}")
m2.metric("Opere Nuove",         f"{nuevas:,}")
m3.metric("Opere Concluse",      f"{conclusas:,}")
m4.metric("Opere Scomparse",     f"{desapar:,}")
m5.metric("Importo Totale (M€)", f"{imp_tot/1_000_000:.1f}" if imp_tot else "N/D")

st.divider()

# ---------------------------------------------------------------------------
# FILTRI (sidebar)
# ---------------------------------------------------------------------------
try:
    st.sidebar.image(Image.open("logo.png"), width=140)
except Exception:
    pass
st.sidebar.markdown(f"## 🔎 Filtri")

# Regione
regioni    = sorted(df["Regione"].dropna().unique()) if "Regione" in df.columns else []
sel_reg    = st.sidebar.multiselect("Regione", regioni, default=list(regioni))

# Avanzamento
st.sidebar.markdown("**Avanzamento Lavori (%)**")
avanz_rng  = st.sidebar.slider("", 0, 100, (0, 100), label_visibility="collapsed")

# Stato opera — selezione vuota = mostra tutto
if "Differenza" in df.columns:
    stati_label  = ["Obra Nueva", "Obra Conclusa", "Obra Conclusa (probable)", "Obra Desaparecida"]
    stati_exist  = [s for s in stati_label if s in df["Differenza"].values]
    sel_stati    = st.sidebar.multiselect("Stato dell'opera", stati_exist, default=[])
    st.sidebar.caption("Nessuna selezione = mostra tutte le opere")

# Tipo lavoro
if "Tipo_Lavoro" in df.columns:
    tipi     = sorted(df["Tipo_Lavoro"].dropna().unique())
    sel_tipi = st.sidebar.multiselect("Tipo di lavoro", tipi, default=list(tipi))

# Periodi registrati
if av_cols:
    st.sidebar.markdown("**Periodi registrati**")
    for c in av_cols:
        st.sidebar.caption(f"📅 {c.replace(AVANZ_PREFIX,'')}")

# Cerca
cerca = st.sidebar.text_input("🔍 Cerca", placeholder="Descrizione, CUP, impresa...")

# ---------------------------------------------------------------------------
# APPLICAZIONE FILTRI
# ---------------------------------------------------------------------------
df_f = df.copy()

if sel_reg:
    df_f = df_f[df_f["Regione"].isin(sel_reg)]

if "_avanz_num" in df_f.columns:
    df_f["_avanz_num"] = df_f[last_avanz].apply(parse_pct)
    df_f = df_f[
        df_f["_avanz_num"].isna() |
        ((df_f["_avanz_num"] >= avanz_rng[0]) & (df_f["_avanz_num"] <= avanz_rng[1]))
    ]

# Stato: selezione vuota = mostra tutto; altrimenti filtra per etichette + differenze numeriche
if "Differenza" in df_f.columns and sel_stati:
    es_num = df_f["Differenza"].apply(lambda x: bool(re.match(r"^[+\-]?\d", str(x))))
    df_f   = df_f[df_f["Differenza"].isin(sel_stati) | es_num]

if "Tipo_Lavoro" in df_f.columns and sel_tipi:
    df_f = df_f[df_f["Tipo_Lavoro"].isin(sel_tipi)]

if cerca:
    mask = pd.Series(False, index=df_f.index)
    for col in ["Descrizione", "Nome_Ufficiale_Progetto", "Cup", "Impresa"]:
        if col in df_f.columns:
            mask |= df_f[col].str.contains(cerca, case=False, na=False)
    df_f = df_f[mask]

# ---------------------------------------------------------------------------
# DEFINIZIONE COLONNE
# Fisse al fondo: Avanz_* + Differenza
# Selezionabili: tutto il resto
# ---------------------------------------------------------------------------
cols_fisse  = av_cols + (["Differenza"] if "Differenza" in df_f.columns else [])
cols_libere = [c for c in df_f.columns if c not in cols_fisse and not c.startswith("_")]

# Default visibili (selezione iniziale ragionevole)
COLS_DEFAULT = [
    "Regione", "Cup", "Descrizione", "Tipo_Lavoro",
    "Impresa", "Importo_Totale", "Avanzamento_Lavori",
    "Data_Ultimazione_Prevista", "Coordinate",
]
cols_default_valide = [c for c in COLS_DEFAULT if c in cols_libere]

# ---------------------------------------------------------------------------
# INTESTAZIONE TABELLA + BOTTONE COLONNE
# ---------------------------------------------------------------------------
r_head, r_btn = st.columns([6, 1])
with r_head:
    st.markdown(f"### Risultati: **{len(df_f):,}** opere")
with r_btn:
    with st.popover("⚙ Colonne"):
        st.markdown("**Seleziona e ordina le colonne**")
        st.caption("L'ordine di selezione determina l'ordine delle colonne. Le colonne Avanz_* e Differenza sono sempre alla fine.")

        if st.button("↩ Ripristina default", use_container_width=True):
            st.session_state.col_selection = cols_default_valide
            st.rerun()

        sel_cols = st.multiselect(
            "Colonne visibili",
            options=cols_libere,
            default=st.session_state.col_selection or cols_default_valide,
            label_visibility="collapsed",
        )
        st.session_state.col_selection = sel_cols

# Costruire ordine finale: selezionate + fisse al fondo
cols_finali = [c for c in (st.session_state.col_selection or cols_default_valide) if c in df_f.columns]
cols_finali += [c for c in cols_fisse if c in df_f.columns]

# ---------------------------------------------------------------------------
# TABELLA CON CHECKBOX (data_editor)
# ---------------------------------------------------------------------------

# — riga comandi sopra la tabella: Seleziona / Deseleziona / Elimina —
if "sel_all_ts"  not in st.session_state: st.session_state.sel_all_ts  = 0
if "sel_all_val" not in st.session_state: st.session_state.sel_all_val = False

_sa_col, _sd_col, _el_col, _gap = st.columns([1.4, 1.6, 1.8, 2.2])
with _sa_col:
    if st.button("☑ Seleziona tutti", use_container_width=True, key="btn_sel_all"):
        st.session_state.sel_all_ts  += 1
        st.session_state.sel_all_val  = True
with _sd_col:
    if st.button("☐ Deseleziona tutti", use_container_width=True, key="btn_desel_all"):
        st.session_state.sel_all_ts  += 1
        st.session_state.sel_all_val  = False
with _el_col:
    # Placeholder: popolato dopo il data_editor quando conosciamo selected_entries
    _delete_placeholder = st.empty()

df_display = df_f[cols_finali].copy()

# — coordinate → link Google Maps —
if "Coordinate" in df_display.columns:
    df_display["Coordinate"] = df_display["Coordinate"].apply(_coord_to_maps)

df_display.insert(0, "✓", st.session_state.sel_all_val)

_col_cfg = {
    "✓": st.column_config.CheckboxColumn("Arricchisci", default=False, width="small"),
}
if "Descrizione" in df_display.columns:
    _col_cfg["Descrizione"] = st.column_config.TextColumn("Descrizione", width="large")
if "Coordinate" in df_display.columns:
    # display_text come regex: estrae lat,lng dall'URL "?q=lat,lng"
    _col_cfg["Coordinate"] = st.column_config.LinkColumn(
        "📍 Coordinate",
        display_text=r"q=(.*)",
    )

# Altezza dinamica: mostra fino a 15 righe (cap ≈ 581px), oltre scrolla internamente
_row_h, _hdr_h = 35, 38
_tbl_h = min(_hdr_h + _row_h * max(len(df_f), 1) + 18, 581)
_tbl_h = max(_tbl_h, 180)

edited = st.data_editor(
    df_display,
    key=f"tbl_{st.session_state.sel_all_ts}_{len(df_f)}",
    use_container_width=True,
    height=_tbl_h,
    column_config=_col_cfg,
    disabled=[c for c in df_display.columns if c != "✓"],
    hide_index=True,
)

# Righe selezionate: ricaviamo sia i CUP (per arricchimento) sia entries ricche
# (Cup + Regione + Impresa + Descrizione) per la blacklist composita.
_selected_mask  = edited["✓"] if "✓" in edited.columns else pd.Series([], dtype=bool)
_selected_idx   = _selected_mask[_selected_mask].index
selected_cups   = df_f.loc[_selected_idx, "Cup"].dropna().astype(str).unique().tolist() if "Cup" in df_f.columns else []

# Entries ricche per blacklist: un dict per ogni riga selezionata
selected_entries = []
if len(_selected_idx) > 0:
    _fields_bl = [c for c in ["Cup", "Regione", "Impresa", "Descrizione"] if c in df_f.columns]
    for _, _r in df_f.loc[_selected_idx, _fields_bl].iterrows():
        _cup = str(_r.get("Cup", "")).strip()
        if _cup and _cup not in ("None", "nan"):
            selected_entries.append({
                "Cup":         _cup,
                "Regione":     str(_r.get("Regione", "")).strip(),
                "Impresa":     str(_r.get("Impresa", "")).strip(),
                "Descrizione": str(_r.get("Descrizione", "")).strip(),
            })

# — Popola il bottone Elimina sopra la tabella (ora conosciamo le selezioni) —
with _delete_placeholder.container():
    btn_delete = st.button(
        f"🗑 Elimina ({len(selected_entries)})" if selected_entries else "🗑 Elimina selezionate",
        disabled=not selected_entries,
        use_container_width=True,
        key="btn_delete_rows",
        help="Aggiunge i progetti alla blacklist permanente (match composito CUP + Regione): non torneranno nei prossimi scraping.",
    )

# ---------------------------------------------------------------------------
# AZIONI SULLE RIGHE SELEZIONATE — solo Arricchisci (Elimina è sopra la tabella)
# ---------------------------------------------------------------------------
enrich_col1, enrich_col2 = st.columns([3, 1])
with enrich_col1:
    if selected_cups:
        st.info(f"**{len(selected_cups)}** opere selezionate: {', '.join(selected_cups[:5])}{'...' if len(selected_cups) > 5 else ''}")
    else:
        st.caption("Seleziona le opere con ✓ per arricchirle con OpenCUP")
with enrich_col2:
    btn_enrich = st.button(
        "🔍 Arricchisci OpenCUP",
        disabled=not selected_cups or _enrich_prog["running"],
        use_container_width=True,
        type="primary",
        key="btn_enrich_bottom",
    )

if btn_enrich and selected_cups and not _enrich_prog["running"]:
    _enrich_prog.update({"pct": 0.0, "msg": "Avvio arricchimento...", "running": True, "error": None, "done": False})
    st.session_state.enrich_was_running = True
    threading.Thread(target=_hilo_enrich, args=(selected_cups,), daemon=True).start()
    st.rerun()

# — Eliminazione: aggiunge entries (Cup+Regione+Impresa) alla blacklist
#   e rimuove dal master le righe che matchano il filtro composito.
if btn_delete and selected_entries:
    from comparador import add_to_blacklist, load_blacklist, is_row_blacklisted, MASTER_FILE as _MF
    try:
        nuovi_bl = add_to_blacklist(selected_entries)
        # Rimuove subito tutte le righe dal master usando il match composito
        if _MF.exists():
            _df = pd.read_excel(_MF, dtype=str).fillna("")
            _before = len(_df)
            _bl_now = load_blacklist()
            _mask = _df.apply(lambda r: is_row_blacklisted(r, _bl_now), axis=1)
            _df = _df[~_mask].reset_index(drop=True)
            _removed = _before - len(_df)
            _df.to_excel(_MF, index=False)
            st.session_state.notif_delete = ("success",
                f"Eliminate {_removed} righe · {nuovi_bl} nuovi progetti in blacklist (match CUP+Regione)")
        else:
            st.session_state.notif_delete = ("success",
                f"{nuovi_bl} progetti aggiunti alla blacklist")
    except Exception as e:
        st.session_state.notif_delete = ("error", f"Errore eliminazione: {e}")
    st.rerun()

# Notifica eliminazione (non invasiva)
_nd = st.session_state.get("notif_delete")
if _nd:
    _typ, _msg = _nd
    if _typ == "success":
        st.success(f"🗑 {_msg}")
    else:
        st.error(f"❌ {_msg}")

# ---------------------------------------------------------------------------
# DOWNLOAD
# ---------------------------------------------------------------------------
# Scarica TUTTE le colonne (non solo quelle visibili), escludendo quelle helper "_*"
_cols_export = [c for c in df_f.columns if not c.startswith("_")]
st.download_button(
    "⬇ Scarica dati filtrati (CSV)",
    data=df_f[_cols_export].to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
    file_name=f"anas_filtrato_{pd.Timestamp.now().strftime('%d%m%Y_%H%M')}.csv",
    mime="text/csv",
)

# — notifica arricchimento a fondo pagina (non invasiva) —
_ne = st.session_state.get("notif_enrich")
if _ne:
    _typ, _msg = _ne
    if _typ == "success":
        st.markdown(
            f'<p style="color:{PAVIMOD_GRAY};font-size:0.78rem;margin-top:12px">'
            f'🔍 {_msg}</p>',
            unsafe_allow_html=True,
        )
    else:
        st.warning(f"⚠️ {_msg}")
