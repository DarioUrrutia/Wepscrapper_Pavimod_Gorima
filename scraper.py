"""
ANAS Lavori in Corso — Scraper
Regioni: SICILIA, BASILICATA, PUGLIA, CALABRIA, MOLISE, CAMPANIA

Flusso:
  1. Scaricamento dati ANAS (parallelo per strada)
  2. Filtro opere > 10 milioni €
  3. Costruzione DataFrame (coordinate da ANAS dirette)
  4. Salvataggio CSV + pulizia vecchi file (mantiene ultimi 5)

L'arricchimento OpenCUP avviene su richiesta dal frontend (enriquecedor.py).

Uso:
    python scraper.py
"""

import requests
import json
import re
import pandas as pd
from datetime import datetime
from pathlib import Path
from bs4 import BeautifulSoup
import time
import random
import string
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------------------------------------------------------------------------
# Regioni di interesse
# ---------------------------------------------------------------------------
REGIONES = [
    {"db": "SICILIA",    "nome": "Sicilia"},
    {"db": "BASILICATA", "nome": "Basilicata"},
    {"db": "PUGLIA",     "nome": "Puglia"},
    {"db": "CALABRIA",   "nome": "Calabria"},
    {"db": "MOLISE",     "nome": "Molise"},
    {"db": "CAMPANIA",   "nome": "Campania"},
]

IMPORTO_MINIMO = 10_000_000   # filtra opere < 10 milioni €

# ---------------------------------------------------------------------------
# URL e headers
# ---------------------------------------------------------------------------
URL_ANAS_LAVORI = "https://www.stradeanas.it/it/anas_lavori_in_corso/getlavori"
URL_OPENCUP     = "https://opencup.gov.it/progetto/-/cup/{cup}"

HEADERS_ANAS = {
    "Referer":    "https://www.stradeanas.it/it/lavori-in-corso",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":     "application/json, text/plain, */*",
}
HEADERS_OPENCUP = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":     "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

DELAY_ANAS    = 0.05
DELAY_OPENCUP = 1.0
ANAS_WORKERS  = 8
MAX_CSV_FILES = 5   # numero massimo di CSV da conservare

# HTTP session globale — riusa connessioni TCP (keep-alive), molto più veloce
_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":     "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
})

# ---------------------------------------------------------------------------
# Cartelle
# ---------------------------------------------------------------------------
DATA_DIR      = Path("data")
RAW_DIR       = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
RUNS_DIR      = DATA_DIR / "runs"
CACHE_DIR     = DATA_DIR / "cache"

for d in [RAW_DIR, PROCESSED_DIR, RUNS_DIR, CACHE_DIR]:
    d.mkdir(parents=True, exist_ok=True)

OPENCUP_CACHE_FILE = CACHE_DIR / "opencup_cache.json"


# ---------------------------------------------------------------------------
# Utilità generali
# ---------------------------------------------------------------------------
def _token():
    return "".join(random.choices(string.ascii_letters + string.digits, k=5))


def _get_json(params, retries=3):
    p = dict(params)
    p["random"] = _token()
    for attempt in range(retries):
        try:
            r = _SESSION.get(URL_ANAS_LAVORI, params=p, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                print(f"    [ANAS ERROR] {e}")
    return None


def _limpiar_texto(texto):
    if not texto:
        return ""
    return re.sub(r"[\r\n\t]+", " ", str(texto)).strip()


def _limpiar_importe(valor):
    """Converte '23.532.434,20' o '2.3532434E7' in float."""
    if not valor or str(valor).strip() in ("", "None", "nan", "null"):
        return None
    s = str(valor).strip()
    try:
        if "E" in s.upper() and "," not in s:
            return round(float(s), 2)
        return float(s.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def _formatear_coordenada(lat, lng):
    """Unisce lat e lng in un campo unico: '38.701000, 16.472000'. Vuoto se assenti o (0,0)."""
    try:
        lat_f = float(lat)
        lng_f = float(lng)
        if lat_f == 0.0 and lng_f == 0.0:
            return ""
        return f"{lat_f:.6f}, {lng_f:.6f}"
    except (TypeError, ValueError):
        return ""


def limpiar_csvs_antiguos(max_files=MAX_CSV_FILES):
    """Elimina i CSV più vecchi, conservando solo gli ultimi max_files."""
    csvs = sorted(PROCESSED_DIR.glob("anas_obras_*.csv"))
    eliminados = 0
    for viejo in csvs[:-max_files] if len(csvs) > max_files else []:
        viejo.unlink()
        print(f"  [CLEANUP] Eliminato: {viejo.name}")
        eliminados += 1
    return eliminados


# ---------------------------------------------------------------------------
# API ANAS
# ---------------------------------------------------------------------------
def api_ultima_actualizacion():
    data = _get_json({"action": "GET_DATA_UPDATE"})
    return data.get("data") if isinstance(data, dict) else None


def api_strade_regione(db):
    data = _get_json({"action": "STRADA_REGIONE", "regione": db})
    return data if isinstance(data, list) else []


def api_lavori_detail(db, codice_strada):
    data = _get_json({
        "action": "LAVORI_DETAIL", "regione": db,
        "strada": codice_strada, "tipo": "*", "stato": "*", "oggetto": "",
    })
    return data if isinstance(data, list) else []


def api_lavori_marker(db, codice_strada):
    data = _get_json({
        "action": "LAVORI_MARKER", "regione": db,
        "strada": codice_strada, "tipo": "*", "stato": "*", "oggetto": "",
    })
    return data if isinstance(data, list) else []


def _fetch_strada(db, cod_strada, nome_strada, nome_regione):
    """Scarica dettagli e markers di una strada — per ThreadPoolExecutor."""
    lavori  = api_lavori_detail(db, cod_strada)
    if DELAY_ANAS > 0: time.sleep(DELAY_ANAS)
    markers = api_lavori_marker(db, cod_strada)
    if DELAY_ANAS > 0: time.sleep(DELAY_ANAS)

    marker_por_id = {str(m.get("id")): m for m in markers}
    obras = []
    for obra in lavori:
        mid = str(obra.get("id", ""))
        if mid in marker_por_id:
            m = marker_por_id[mid]
            obra["lat"] = m.get("lat")
            obra["lng"] = m.get("lng")
        else:
            obra["lat"] = None
            obra["lng"] = None
        obra["_regione"]     = nome_regione
        obra["_strada_cod"]  = cod_strada
        obra["_strada_nome"] = nome_strada
        obras.append(obra)
    return obras


# ---------------------------------------------------------------------------
# OpenCUP scraping (usato da enriquecedor.py, non dal flusso principale)
# ---------------------------------------------------------------------------
def _extraer_por_contexto(lines, etiqueta, siguiente_etiqueta=None):
    for i, l in enumerate(lines):
        if l.strip().lower() == etiqueta.lower():
            if siguiente_etiqueta:
                prev = lines[i-1].strip().lower() if i > 0 else ""
                if prev != siguiente_etiqueta.lower():
                    continue
            for j in range(i+1, min(i+4, len(lines))):
                if lines[j].strip():
                    return lines[j].strip()
    return ""


def scrape_opencup(cup):
    """Scarica dati OpenCUP per un CUP. Usato da enriquecedor.py."""
    if not cup or str(cup).strip() in ("", "None", "nan"):
        return {}
    url = URL_OPENCUP.format(cup=str(cup).strip())
    try:
        r = requests.get(url, headers=HEADERS_OPENCUP, timeout=30)
        r.raise_for_status()
    except Exception as e:
        print(f"    [OPENCUP ERROR] CUP {cup}: {e}")
        return {}

    soup = BeautifulSoup(r.text, "lxml")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    lines = [l.strip() for l in soup.get_text(separator="\n").split("\n")]
    lines = [l for l in lines if l]

    titulo = ""
    for i, l in enumerate(lines):
        if l.strip() == "Dettaglio Progetto":
            for j in range(i+1, min(i+8, len(lines))):
                t = lines[j].strip()
                if t and t != "CUP:" and "version" not in t.lower() and len(t) > 10:
                    titulo = t
                    break
            break

    cup_padre = ""
    for a in soup.find_all("a", href=True):
        t = a.get_text(strip=True)
        if re.match(r"^[A-Z]\d{2}[A-Z]\d{11}$", t) and t != str(cup).strip():
            cup_padre = t
            break

    texto_completo = "\n".join(lines)
    proyectos_vinculados = ""
    m = re.search(r"(\d+)\s*(?:CUP\s+)?collegat", texto_completo, re.IGNORECASE)
    if m:
        proyectos_vinculados = m.group(1)

    return {
        "Nome_Ufficiale_Progetto": titulo,
        "Anno_Decisione":          _extraer_por_contexto(lines, "Anno decisione"),
        "Provincia":               _extraer_por_contexto(lines, "Provincia"),
        "Municipi_Coinvolti":      _extraer_por_contexto(lines, "Comune"),
        "Tipologia":               _extraer_por_contexto(lines, "Tipologia"),
        "Settore":                 _extraer_por_contexto(lines, "Settore"),
        "Sottosettore":            _extraer_por_contexto(lines, "Sottosettore"),
        "Categoria_Settore":       _extraer_por_contexto(
                                       lines, "Categoria",
                                       _extraer_por_contexto(lines, "Sottosettore")
                                   ),
        "Cup_Padre":               cup_padre,
        "Progetti_Collegati_CUP":  proyectos_vinculados,
    }


# ---------------------------------------------------------------------------
# Scraping principale
# ---------------------------------------------------------------------------
def scrape(progress_callback=None):
    """
    Flusso principale:
      FASE 1 — Scaricamento ANAS parallelo       (1% → 55%)
      FASE 2 — Filtro >10M + build DataFrame      (55% → 88%)
      FASE 3 — Salvataggio CSV + pulizia vecchi   (88% → 100%)
    """
    def _cb(pct, msg):
        print(f"  [{int(pct*100):3d}%] {msg}")
        if progress_callback:
            progress_callback(pct, msg)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir   = RUNS_DIR / timestamp
    run_dir.mkdir()

    print("=" * 62)
    print("  ANAS LAVORI IN CORSO — SCRAPER")
    print(f"  Esecuzione: {timestamp}")
    print(f"  Regioni:    {', '.join(r['nome'] for r in REGIONES)}")
    print(f"  Filtro:     importo >= {IMPORTO_MINIMO:,.0f} €")
    print("=" * 62)

    _cb(0.01, "Connessione ad ANAS...")
    ultima_agg = api_ultima_actualizacion()
    print(f"\n  Dati ANAS aggiornati al: {ultima_agg or 'N/D'}\n")

    # -------------------------------------------------------------------
    # FASE 1: Scaricamento ANAS (parallelo per strada)
    # -------------------------------------------------------------------
    todas_las_obras = []

    for i, reg in enumerate(REGIONES, 1):
        db   = reg["db"]
        nome = reg["nome"]
        _cb(0.01 + (i - 1) / len(REGIONES) * 0.54, f"ANAS {i}/{len(REGIONES)}: {nome}...")
        print(f"  [{i}/{len(REGIONES)}] {nome}")

        strade    = api_strade_regione(db)
        completed = 0
        print(f"         {len(strade)} strade")

        obras_region = []
        with ThreadPoolExecutor(max_workers=ANAS_WORKERS) as ex:
            futures = {
                ex.submit(_fetch_strada, db, s.get("codice", ""), s.get("strada", ""), nome): s
                for s in strade
            }
            for future in as_completed(futures):
                completed += 1
                obras_region.extend(future.result())
                _cb(
                    0.01 + ((i - 1) + completed / max(len(strade), 1)) / len(REGIONES) * 0.54,
                    f"ANAS {i}/{len(REGIONES)}: {nome} — {completed}/{len(strade)} strade",
                )

        print(f"         {len(obras_region)} opere totali")
        with open(run_dir / f"{db}.json", "w", encoding="utf-8") as f:
            json.dump(obras_region, f, ensure_ascii=False, indent=2)
        todas_las_obras.extend(obras_region)

    print(f"\n  Totale opere ANAS (pre-filtro): {len(todas_las_obras)}")

    # -------------------------------------------------------------------
    # FASE 2: Filtro >10M + costruzione DataFrame
    # -------------------------------------------------------------------
    _cb(0.56, f"Applicazione filtro importo >= {IMPORTO_MINIMO/1_000_000:.0f}M€...")

    obras_filtradas = [
        o for o in todas_las_obras
        if (_limpiar_importe(o.get("importo_lav_totale")) or 0) >= IMPORTO_MINIMO
    ]
    print(f"  Dopo filtro >10M: {len(obras_filtradas)} opere")

    _cb(0.60, f"Costruzione DataFrame ({len(obras_filtradas)} opere)...")
    filas = []
    for obra in obras_filtradas:
        cup = str(obra.get("cup", "")).strip()

        strade_list = obra.get("strade_list") or []
        km_dal   = strade_list[0].get("DALKM", "") if strade_list else ""
        km_al    = strade_list[0].get("ALKM",  "") if strade_list else ""
        segmentos = "; ".join(
            f"{s.get('CODICE_STRADA','')} {s.get('DALKM','')}–{s.get('ALKM','')}"
            for s in strade_list
        )

        fila = {
            # — Identificazione —
            "Id_ANAS":                   str(obra.get("id", "")),
            "Regione":                   obra.get("_regione", ""),
            "Codice_Strada":             obra.get("_strada_cod", ""),
            "Nome_Strada":               obra.get("_strada_nome", ""),
            # — Dati opera —
            "Cup":                       cup,
            "Descrizione":               _limpiar_texto(obra.get("descrizione", "")),
            "Tipo_Lavoro":               _limpiar_texto(obra.get("tipo_lavoro", "")),
            "Impresa":                   _limpiar_texto(obra.get("impresa", "")),
            "Importo_Principale":        _limpiar_importe(obra.get("importo_lav_principali")),
            "Importo_Totale":            _limpiar_importe(obra.get("importo_lav_totale")),
            "Data_Consegna_Impresa":     obra.get("data_consegna_impresa", ""),
            "Avanzamento_Lavori":        obra.get("avanzamento_lavori", ""),
            "Data_Ultimazione_Prevista": obra.get("ultimazione", ""),
            # — Tratti km —
            "Dal_Km":                    km_dal,
            "Al_Km":                     km_al,
            "Strade_Segmentos":          segmentos,
            # — Coordinate ANAS dirette —
            "Coordinate":                _formatear_coordenada(obra.get("lat"), obra.get("lng")),
            # — OpenCUP (vuoti, da riempire con enriquecedor) —
            "Nome_Ufficiale_Progetto":   "",
            "Anno_Decisione":            "",
            "Provincia_CUP":             "",
            "Municipi_Coinvolti":        "",
            "Tipologia":                 "",
            "Settore":                   "",
            "Sottosettore":              "",
            "Categoria_Settore":         "",
            "Cup_Padre":                 "",
            "Progetti_Collegati_CUP":    "",
        }
        filas.append(fila)

    df = pd.DataFrame(filas)

    # -------------------------------------------------------------------
    # FASE 3: Salvataggio CSV + pulizia vecchi file
    # -------------------------------------------------------------------
    _cb(0.88, "Salvataggio CSV...")

    if df.empty:
        _cb(1.0, "Nessuna opera trovata dopo il filtro. CSV non salvato.")
        print("  [SCRAPER] DataFrame vuoto — CSV non generato.")
        return {"timestamp": timestamp, "total": 0, "csv": None}

    csv_path = PROCESSED_DIR / f"anas_obras_{timestamp}.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    raw_payload = {
        "timestamp":   timestamp,
        "aggiornato":  ultima_agg,
        "regioni":     [r["nome"] for r in REGIONES],
        "totale_anas": len(todas_las_obras),
        "dopo_filtro": len(obras_filtradas),
    }
    with open(RAW_DIR / f"anas_raw_{timestamp}.json", "w", encoding="utf-8") as f:
        json.dump(raw_payload, f, ensure_ascii=False, indent=2)
    with open(run_dir / "_meta.json", "w", encoding="utf-8") as f:
        json.dump(raw_payload, f, ensure_ascii=False, indent=2)

    _cb(0.95, "Pulizia CSV vecchi...")
    eliminados = limpiar_csvs_antiguos(MAX_CSV_FILES)

    print("\n" + "=" * 62)
    print(f"  TOTALE ANAS:        {len(todas_las_obras)}")
    print(f"  DOPO FILTRO >10M:   {len(obras_filtradas)}")
    print(f"  CSV salvato:        {csv_path}")
    print(f"  CSV eliminati:      {eliminados}")
    print("=" * 62)

    _cb(1.0, f"Completato — {len(obras_filtradas)} opere > 10M€")
    return {"timestamp": timestamp, "total": len(obras_filtradas), "csv": str(csv_path)}


if __name__ == "__main__":
    from comparador import actualizar_master
    scrape()
    csvs = sorted(PROCESSED_DIR.glob("anas_obras_*.csv"))
    if csvs:
        actualizar_master(csvs[-1])
