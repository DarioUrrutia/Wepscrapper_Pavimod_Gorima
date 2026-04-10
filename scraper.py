"""
ANAS Lavori in Corso — Scraper Iterativo con enriquecimiento OpenCUP
Regiones: SICILIA, BASILICATA, PUGLIA, CALABRIA, MOLISE, CAMPANIA

Columnas finales definidas por el usuario.

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

# ---------------------------------------------------------------------------
# Regiones de interes
# ---------------------------------------------------------------------------
REGIONES = [
    {"db": "SICILIA",    "nome": "Sicilia"},
    {"db": "BASILICATA", "nome": "Basilicata"},
    {"db": "PUGLIA",     "nome": "Puglia"},
    {"db": "CALABRIA",   "nome": "Calabria"},
    {"db": "MOLISE",     "nome": "Molise"},
    {"db": "CAMPANIA",   "nome": "Campania"},
]

# ---------------------------------------------------------------------------
# URLs
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

DELAY_ANAS    = 0.5   # segundos entre requests ANAS
DELAY_OPENCUP = 1.0   # segundos entre requests OpenCUP (mas respetuoso)

# ---------------------------------------------------------------------------
# Carpetas
# ---------------------------------------------------------------------------
DATA_DIR      = Path("data")
RAW_DIR       = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
RUNS_DIR      = DATA_DIR / "runs"

for d in [RAW_DIR, PROCESSED_DIR, RUNS_DIR]:
    d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _token():
    return "".join(random.choices(string.ascii_letters + string.digits, k=5))


def _get_json(params, retries=3):
    """GET a la API de ANAS, devuelve JSON o None."""
    p = dict(params)
    p["random"] = _token()
    for attempt in range(retries):
        try:
            r = requests.get(URL_ANAS_LAVORI, params=p, headers=HEADERS_ANAS, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                print(f"    [ANAS ERROR] {e}")
    return None


def _limpiar_texto(texto):
    """Elimina saltos de linea y tabulaciones de un string."""
    if not texto:
        return ""
    return re.sub(r"[\r\n\t]+", " ", str(texto)).strip()


def _limpiar_importe(valor):
    """Convierte '23.532.434,20' o '2.3532434E7' a float."""
    if not valor or str(valor).strip() in ("", "None", "nan", "null"):
        return None
    s = str(valor).strip()
    # Notacion cientifica italiana: 1.2485838E7
    try:
        if "E" in s.upper() and "," not in s:
            return round(float(s), 2)
        return float(s.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def _formatear_coordenada(lat, lng):
    """Une lat y lng en un solo campo: '38.701000, 16.472000'"""
    try:
        return f"{float(lat):.6f}, {float(lng):.6f}"
    except (TypeError, ValueError):
        return ""


_cache_geo = {}

def _geocodificar(municipio, provincia):
    """
    Convierte municipio + provincia a coordenadas usando Nominatim (OpenStreetMap).
    Devuelve (lat, lng) como strings o ('', '').
    """
    if not municipio and not provincia:
        return "", ""

    # Tomar solo el primer municipio si hay varios
    primer_municipio = municipio.split(",")[0].strip() if municipio else ""
    clave = f"{primer_municipio}|{provincia}"

    if clave in _cache_geo:
        return _cache_geo[clave]

    query = ", ".join(filter(None, [primer_municipio, provincia, "Italia"]))
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "json", "limit": 1},
            headers={"User-Agent": "ANAS-lavori-scraper/1.0"},
            timeout=10,
        )
        data = r.json()
        if data:
            lat = data[0].get("lat", "")
            lng = data[0].get("lon", "")
            _cache_geo[clave] = (lat, lng)
            return lat, lng
    except Exception:
        pass

    _cache_geo[clave] = ("", "")
    return "", ""


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
        "action":  "LAVORI_DETAIL",
        "regione": db,
        "strada":  codice_strada,
        "tipo":    "*",
        "stato":   "*",
        "oggetto": "",
    })
    return data if isinstance(data, list) else []


def api_lavori_marker(db, codice_strada):
    data = _get_json({
        "action":  "LAVORI_MARKER",
        "regione": db,
        "strada":  codice_strada,
        "tipo":    "*",
        "stato":   "*",
        "oggetto": "",
    })
    return data if isinstance(data, list) else []


# ---------------------------------------------------------------------------
# OpenCUP scraping
# ---------------------------------------------------------------------------
def _extraer_por_contexto(lines, etiqueta, siguiente_etiqueta=None):
    """
    Busca 'etiqueta' en la lista de lineas y devuelve la linea siguiente no vacia.
    Si siguiente_etiqueta se indica, solo busca ocurrencias donde la linea anterior
    sea esa etiqueta (permite distinguir Categoria del proyecto vs del soggetto).
    """
    for i, l in enumerate(lines):
        if l.strip().lower() == etiqueta.lower():
            if siguiente_etiqueta:
                # Verificar que la linea previa sea la etiqueta de contexto
                prev = lines[i-1].strip().lower() if i > 0 else ""
                if prev != siguiente_etiqueta.lower():
                    continue
            # Devolver la siguiente linea no vacia
            for j in range(i+1, min(i+4, len(lines))):
                if lines[j].strip():
                    return lines[j].strip()
    return ""


def scrape_opencup(cup):
    """
    Scrape la pagina publica de OpenCUP para un codigo CUP.
    Devuelve dict con los campos de interes o dict vacio si falla.
    """
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

    # Lista de lineas limpias (sin vacias consecutivas)
    lines = [l.strip() for l in soup.get_text(separator="\n").split("\n")]
    lines = [l for l in lines if l]

    # Titulo del proyecto: 4ta linea despues de "Dettaglio Progetto"
    # Estructura: Dettaglio Progetto / version portlet / version theme / TITULO / CUP: / ...
    titulo = ""
    for i, l in enumerate(lines):
        if l.strip() == "Dettaglio Progetto":
            for j in range(i+1, min(i+8, len(lines))):
                t = lines[j].strip()
                if t and t != "CUP:" and "version" not in t.lower() and len(t) > 10:
                    titulo = t
                    break
            break

    # CUP padre: link con patron de CUP distinto al actual
    cup_padre = ""
    for a in soup.find_all("a", href=True):
        t = a.get_text(strip=True)
        if re.match(r"^[A-Z]\d{2}[A-Z]\d{11}$", t) and t != str(cup).strip():
            cup_padre = t
            break

    # Proyectos vinculados
    proyectos_vinculados = ""
    texto_completo = "\n".join(lines)
    m = re.search(r"(\d+)\s*(?:CUP\s+)?collegat", texto_completo, re.IGNORECASE)
    if m:
        proyectos_vinculados = m.group(1)

    # Coordenadas
    cup_lat, cup_lng = "", ""
    m_coord = re.search(r"Lat\s+([\d.]+).*?Lng\s+([\d.]+)", texto_completo, re.IGNORECASE | re.DOTALL)
    if m_coord:
        cup_lat = m_coord.group(1)
        cup_lng = m_coord.group(2)

    # Campos por contexto de lineas
    # Categoria_Settore: la que viene despues de Sottosettore (no la del soggetto)
    CAMPOS = {
        "Nome_Ufficiale_Progetto": titulo,
        "Anno_Decisione":          _extraer_por_contexto(lines, "Anno decisione"),
        "Provincia":               _extraer_por_contexto(lines, "Provincia"),
        "Municipi_Coinvolti":      _extraer_por_contexto(lines, "Comune"),
        "Tipologia":               _extraer_por_contexto(lines, "Tipologia"),
        "Settore":                 _extraer_por_contexto(lines, "Settore"),
        "Sottosettore":            _extraer_por_contexto(lines, "Sottosettore"),
        # Categoria correcta: la que viene inmediatamente despues del VALOR de Sottosettore
        # (no de la etiqueta), para no confundirla con la categoria del soggetto titolare
        "Categoria_Settore":       _extraer_por_contexto(
                                       lines, "Categoria",
                                       _extraer_por_contexto(lines, "Sottosettore")
                                   ),
        "Cup_Padre":               cup_padre,
        "Progetti_Collegati_CUP":  proyectos_vinculados,
        "_cup_lat":                cup_lat,
        "_cup_lng":                cup_lng,
    }

    return CAMPOS


# ---------------------------------------------------------------------------
# Scraping principal
# ---------------------------------------------------------------------------
def scrape():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir   = RUNS_DIR / timestamp
    run_dir.mkdir()

    print("=" * 62)
    print("  ANAS LAVORI IN CORSO — SCRAPER")
    print(f"  Ejecucion: {timestamp}")
    print(f"  Regiones:  {', '.join(r['nome'] for r in REGIONES)}")
    print("=" * 62)

    ultima_agg = api_ultima_actualizacion()
    print(f"\n  Datos ANAS actualizados al: {ultima_agg or 'N/D'}\n")

    todas_las_obras = []

    # -------------------------------------------------------------------
    # FASE 1: Descargar datos ANAS
    # -------------------------------------------------------------------
    for i, reg in enumerate(REGIONES, 1):
        db   = reg["db"]
        nome = reg["nome"]
        print(f"  [{i}/{len(REGIONES)}] {nome}")

        strade = api_strade_regione(db)
        print(f"         {len(strade)} carreteras")
        time.sleep(DELAY_ANAS)

        obras_region = []

        for strada in strade:
            cod_strada  = strada.get("codice", "")
            nome_strada = strada.get("strada", "")

            lavori  = api_lavori_detail(db, cod_strada)
            time.sleep(DELAY_ANAS)

            markers = api_lavori_marker(db, cod_strada)
            marker_por_id = {str(m.get("id")): m for m in markers}
            time.sleep(DELAY_ANAS)

            for obra in lavori:
                mid = str(obra.get("id", ""))
                if mid in marker_por_id:
                    m = marker_por_id[mid]
                    obra["lat"]   = m.get("lat")
                    obra["lng"]   = m.get("lng")
                else:
                    obra["lat"] = None
                    obra["lng"] = None

                obra["_regione"]     = nome
                obra["_strada_cod"]  = cod_strada
                obra["_strada_nome"] = nome_strada

                obras_region.append(obra)

        print(f"         {len(obras_region)} obras")

        with open(run_dir / f"{db}.json", "w", encoding="utf-8") as f:
            json.dump(obras_region, f, ensure_ascii=False, indent=2)

        todas_las_obras.extend(obras_region)

    print(f"\n  Total obras ANAS: {len(todas_las_obras)}")

    # -------------------------------------------------------------------
    # FASE 2: Enriquecer con OpenCUP
    # -------------------------------------------------------------------
    cups_unicos = list({
        str(o.get("cup", "")).strip()
        for o in todas_las_obras
        if o.get("cup") and str(o.get("cup")).strip() not in ("", "None")
    })

    print(f"\n  Enriqueciendo {len(cups_unicos)} CUPs desde OpenCUP...")

    cache_opencup = {}
    for j, cup in enumerate(cups_unicos, 1):
        if j % 50 == 0:
            print(f"    {j}/{len(cups_unicos)}...")
        cache_opencup[cup] = scrape_opencup(cup)
        time.sleep(DELAY_OPENCUP)

    # -------------------------------------------------------------------
    # FASE 3: Pre-geocodificar municipios unicos via Nominatim
    # (solo los que no tienen coordenadas de ANAS)
    # -------------------------------------------------------------------
    combos_sin_coords = list({
        (str(cache_opencup.get(str(o.get("cup","")), {}).get("Municipi_Coinvolti","")),
         str(cache_opencup.get(str(o.get("cup","")), {}).get("Provincia","")))
        for o in todas_las_obras
        if o.get("lat") in (None, "", "0", 0) or o.get("lng") in (None, "", "0", 0)
    })
    print(f"\n  Geocodificando {len(combos_sin_coords)} ubicaciones via OpenStreetMap...")
    for idx, (mun, prov) in enumerate(combos_sin_coords, 1):
        if idx % 50 == 0:
            print(f"    {idx}/{len(combos_sin_coords)}...")
        _geocodificar(mun, prov)
        time.sleep(1.1)  # Nominatim: max 1 req/seg

    # -------------------------------------------------------------------
    # FASE 4: Construir DataFrame final
    # -------------------------------------------------------------------
    filas = []
    for obra in todas_las_obras:
        cup = str(obra.get("cup", "")).strip()
        oc  = cache_opencup.get(cup, {})

        # Tramo km desde strade_list
        strade_list = obra.get("strade_list") or []
        km_dal = strade_list[0].get("DALKM", "") if strade_list else ""
        km_al  = strade_list[0].get("ALKM",  "") if strade_list else ""
        segmentos = "; ".join(
            f"{s.get('CODICE_STRADA','')} {s.get('DALKM','')}–{s.get('ALKM','')}"
            for s in strade_list
        )

        fila = {
            # --- Identificacion y contexto ---
            "Regione":                  obra.get("_regione", ""),
            "Codice_Strada":            obra.get("_strada_cod", ""),
            "Nome_Strada":              obra.get("_strada_nome", ""),
            # --- Datos de la obra ---
            "Cup":                      cup,
            "Descrizione":              _limpiar_texto(obra.get("descrizione", "")),
            "Tipo_Lavoro":              _limpiar_texto(obra.get("tipo_lavoro", "")),
            "Impresa":                  _limpiar_texto(obra.get("impresa", "")),
            "Importo_Principale":       _limpiar_importe(obra.get("importo_lav_principali")),
            "Importo_Totale":           _limpiar_importe(obra.get("importo_lav_totale")),
            "Data_Consegna_Impresa":    obra.get("data_consegna_impresa", ""),
            "Avanzamento_Lavori":       obra.get("avanzamento_lavori", ""),
            "Data_Ultimazione_Prevista": obra.get("ultimazione", ""),
            # --- Tramos ---
            "Dal_Km":                   km_dal,
            "Al_Km":                    km_al,
            "Strade_Segmentos":         segmentos,
            # --- Coordenadas: ANAS si tiene valores reales (sin importar geocodificato),
            #     fallback a Nominatim (OSM) usando municipio + provincia de OpenCUP ---
            "Coordinate":               _formatear_coordenada(
                                            *( (obra.get("lat"), obra.get("lng"))
                                               if (obra.get("lat") not in (None, "", "0", 0)
                                                   and obra.get("lng") not in (None, "", "0", 0))
                                               else _geocodificar(
                                                   oc.get("Municipi_Coinvolti", ""),
                                                   oc.get("Provincia", "")
                                               )
                                            )
                                        ),
            # --- OpenCUP ---
            "Nome_Ufficiale_Progetto":  oc.get("Nome_Ufficiale_Progetto", ""),
            "Anno_Decisione":           oc.get("Anno_Decisione", ""),
            "Provincia":                oc.get("Provincia", ""),
            "Municipi_Coinvolti":       oc.get("Municipi_Coinvolti", ""),
            "Tipologia":                oc.get("Tipologia", ""),
            "Settore":                  oc.get("Settore", ""),
            "Sottosettore":             oc.get("Sottosettore", ""),
            "Categoria_Settore":        oc.get("Categoria_Settore", ""),
            "Cup_Padre":                oc.get("Cup_Padre", ""),
            "Progetti_Collegati_CUP":   oc.get("Progetti_Collegati_CUP", ""),
        }
        filas.append(fila)

    df = pd.DataFrame(filas)

    # -------------------------------------------------------------------
    # FASE 5: Guardar archivos
    # -------------------------------------------------------------------

    # JSON raw consolidado
    raw_payload = {
        "timestamp":                 timestamp,
        "ultima_aggiornamento_anas": ultima_agg,
        "regiones":                  [r["nome"] for r in REGIONES],
        "total_obras":               len(todas_las_obras),
        "obras":                     todas_las_obras,
    }
    raw_path = RAW_DIR / f"anas_raw_{timestamp}.json"
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(raw_payload, f, ensure_ascii=False, indent=2)

    # Cache OpenCUP
    with open(run_dir / "_opencup_cache.json", "w", encoding="utf-8") as f:
        json.dump(cache_opencup, f, ensure_ascii=False, indent=2)

    # Metadata
    meta = {
        "timestamp":                 timestamp,
        "ultima_aggiornamento_anas": ultima_agg,
        "regiones":                  [r["nome"] for r in REGIONES],
        "total_obras":               len(todas_las_obras),
        "total_cups_enriquecidos":   len(cache_opencup),
    }
    with open(run_dir / "_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # CSV final
    csv_path = PROCESSED_DIR / f"anas_obras_{timestamp}.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    # -------------------------------------------------------------------
    # Resumen
    # -------------------------------------------------------------------
    print("\n" + "=" * 62)
    print(f"  TOTAL OBRAS:        {len(df)}")
    print(f"  CUPs enriquecidos:  {len(cache_opencup)}")
    print(f"  CSV final:          {csv_path}")
    print(f"  JSON raw:           {raw_path}")
    print(f"  Archivos por run:   {run_dir}/")
    print("=" * 62)

    return meta


if __name__ == "__main__":
    scrape()
