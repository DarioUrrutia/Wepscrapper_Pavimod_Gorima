"""
Enriquecedor OpenCUP — ANAS Lavori in Corso

Descarga datos OpenCUP para los CUPs seleccionados desde el frontend
y los guarda permanentemente en el master Excel. Inoltre, per le opere
dove ANAS non ha restituito coordinate, prova a geocodificarle usando
i dati OpenCUP (Municipio + Provincia) via Nominatim/OpenStreetMap.

Uso desde app.py:
    from enriquecedor import enriquecer_obras
    enriquecer_obras(["CUP1", "CUP2"], progress_callback=cb)
"""

import json
import time
import requests
import pandas as pd
from pathlib import Path

from scraper import (
    scrape_opencup,
    OPENCUP_CACHE_FILE,
    _formatear_coordenada,
)
from comparador import MASTER_FILE

DELAY_OPENCUP   = 1.0
DELAY_NOMINATIM = 1.1   # Nominatim impone max 1 req/sec
NOMINATIM_URL   = "https://nominatim.openstreetmap.org/search"
NOMINATIM_HDRS  = {
    "User-Agent": "PAVIMOD-ANAS-Monitor/1.0 (info@pavimod.it)",
    "Accept-Language": "it",
}

# Mapeo campo OpenCUP → columna del master
OPENCUP_COLS = {
    "Nome_Ufficiale_Progetto": "Nome_Ufficiale_Progetto",
    "Anno_Decisione":          "Anno_Decisione",
    "Provincia":               "Provincia_CUP",
    "Municipi_Coinvolti":      "Municipi_Coinvolti",
    "Tipologia":               "Tipologia",
    "Settore":                 "Settore",
    "Sottosettore":            "Sottosettore",
    "Categoria_Settore":       "Categoria_Settore",
    "Cup_Padre":               "Cup_Padre",
    "Progetti_Collegati_CUP":  "Progetti_Collegati_CUP",
}


def _coord_vuota(val) -> bool:
    """True se il campo Coordinate è vuoto / nullo / (0,0)."""
    if val is None:
        return True
    s = str(val).strip()
    return s in ("", "None", "nan", "0.000000, 0.000000")


def _geocodifica_nominatim(municipio: str, provincia: str, regione: str = "") -> str:
    """
    Interroga Nominatim/OpenStreetMap per ottenere lat/lng di un comune italiano.
    Prova prima 'Municipio, Provincia, Italia', poi fallback più larghi.
    Restituisce stringa 'lat, lng' formattata o '' se non trovato.
    """
    municipio = str(municipio or "").strip()
    provincia = str(provincia or "").strip()
    regione   = str(regione   or "").strip()

    # Costruisce una sequenza di query da tentare in ordine di specificità
    queries = []
    if municipio and provincia:
        queries.append(f"{municipio}, {provincia}, Italia")
    if municipio and regione:
        queries.append(f"{municipio}, {regione}, Italia")
    if municipio:
        queries.append(f"{municipio}, Italia")
    if not queries and provincia:
        queries.append(f"{provincia}, Italia")

    for q in queries:
        try:
            r = requests.get(
                NOMINATIM_URL,
                params={"q": q, "format": "json", "limit": 1, "countrycodes": "it"},
                headers=NOMINATIM_HDRS,
                timeout=15,
            )
            time.sleep(DELAY_NOMINATIM)
            if r.status_code != 200:
                continue
            data = r.json()
            if isinstance(data, list) and data:
                lat = data[0].get("lat")
                lng = data[0].get("lon")
                coord = _formatear_coordenada(lat, lng)
                if coord:
                    return coord
        except Exception as e:
            print(f"    [NOMINATIM ERROR] '{q}': {e}")
            continue
    return ""


def _row_gia_arricchita(row) -> bool:
    """
    True se la riga del master ha già i dati OpenCUP principali.
    Usa Nome_Ufficiale_Progetto come indicatore: se è riempito,
    consideriamo la riga già arricchita e la saltiamo.
    """
    val = row.get("Nome_Ufficiale_Progetto", "")
    s = "" if val is None else str(val).strip()
    return s not in ("", "None", "nan")


def _val_non_vuoto(v) -> bool:
    """True se il valore è una stringa non vuota / non 'nan'."""
    if v is None:
        return False
    s = str(v).strip()
    return s not in ("", "None", "nan")


def _primo_comune(municipio: str) -> str:
    """Estrae il primo comune da una stringa tipo 'Catania, Messina' o 'Roma; Latina'."""
    s = str(municipio or "")
    for sep in (",", ";"):
        if sep in s:
            s = s.split(sep)[0]
    return s.strip()


def enriquecer_obras(cups: list, progress_callback=None) -> dict:
    """
    Arricchisce le opere selezionate con dati OpenCUP + coordinate.

    REGOLE DI IDEMPOTENZA (applicate PER RIGA, non per CUP):
      - OpenCUP viene scaricato SOLO se la riga ha Nome_Ufficiale_Progetto vuoto
      - Scrittura OpenCUP: SOLO campi non vuoti (non sovrascrive con "" esistenti)
      - Coordinate: geocoding Nominatim SOLO se la riga ha Coordinate vuoto
      - Righe già complete (OpenCUP + Coordinate) vengono saltate totalmente
      - Nominatim cache per (municipio, provincia) → evita chiamate duplicate
        per tratte diverse dello stesso comune.
    """
    def _cb(pct, msg):
        print(f"  [ENRICH] [{int(pct*100):3d}%] {msg}")
        if progress_callback:
            progress_callback(pct, msg)

    cups = [str(c).strip() for c in cups if c and str(c).strip() not in ("", "None", "nan")]
    if not cups:
        return {}

    if not MASTER_FILE.exists():
        _cb(1.0, "Master non trovato, impossibile arricchire")
        return {}

    # Carica master upfront
    _cb(0.02, "Analisi master...")
    df = pd.read_excel(MASTER_FILE, dtype=str).fillna("")

    # Assicura colonne OpenCUP presenti
    for col in OPENCUP_COLS.values():
        if col not in df.columns:
            avanz_idx = next(
                (i for i, c in enumerate(df.columns) if c.startswith("Avanz_")),
                len(df.columns)
            )
            df.insert(avanz_idx, col, "")
    if "Coordinate" not in df.columns:
        df["Coordinate"] = ""

    # ---------------------------------------------------------------------
    # FASE 1: Analisi PER RIGA (gestisce correttamente i progetti multi-tratta)
    # ---------------------------------------------------------------------
    cups_set = set(cups)
    rows_need_opencup = []   # [(idx, cup), ...] righe con OpenCUP da scaricare
    rows_need_coord   = []   # [(idx, cup), ...] righe con Coordinate vuote
    rows_skip         = 0
    rows_touched      = 0

    for idx in df.index:
        row_cup = str(df.at[idx, "Cup"]).strip() if "Cup" in df.columns else ""
        if row_cup not in cups_set:
            continue
        rows_touched += 1

        riga = df.loc[idx]
        serve_oc    = not _row_gia_arricchita(riga)
        serve_coord = _coord_vuota(riga.get("Coordinate", ""))

        if serve_oc:
            rows_need_opencup.append((idx, row_cup))
        if serve_coord:
            rows_need_coord.append((idx, row_cup))
        if not serve_oc and not serve_coord:
            rows_skip += 1

    unique_cups_oc = {c for _, c in rows_need_opencup}
    _cb(0.05, f"{len(rows_need_opencup)} righe da arricchire ({len(unique_cups_oc)} CUP unici) · "
              f"{len(rows_need_coord)} coordinate da cercare · {rows_skip} righe già complete")

    if not rows_need_opencup and not rows_need_coord:
        _cb(1.0, f"Nessun arricchimento necessario · {rows_skip} righe già complete")
        return {}

    # ---------------------------------------------------------------------
    # FASE 2: Download OpenCUP per i CUP necessari (uno per CUP, non per riga)
    # ---------------------------------------------------------------------
    if OPENCUP_CACHE_FILE.exists():
        with open(OPENCUP_CACHE_FILE, encoding="utf-8") as f:
            cache = json.load(f)
    else:
        cache = {}

    cups_da_scaricare = [c for c in unique_cups_oc if c not in cache]
    for i, cup in enumerate(cups_da_scaricare, 1):
        _cb(0.05 + (i / max(len(cups_da_scaricare), 1)) * 0.60,
            f"OpenCUP {i}/{len(cups_da_scaricare)}: {cup}")
        cache[cup] = scrape_opencup(cup)
        time.sleep(DELAY_OPENCUP)

    with open(OPENCUP_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)

    # ---------------------------------------------------------------------
    # FASE 3: Scrittura OpenCUP nel master (PER RIGA, safe-write)
    # ---------------------------------------------------------------------
    oc_written_fields = 0
    if rows_need_opencup:
        _cb(0.68, f"Scrittura OpenCUP su {len(rows_need_opencup)} righe...")
        for idx, cup in rows_need_opencup:
            oc = cache.get(cup, {}) or {}
            if not oc:
                continue
            for oc_key, master_col in OPENCUP_COLS.items():
                if master_col not in df.columns:
                    continue
                val = oc.get(oc_key, "")
                # SAFE WRITE: scrivi solo se il nuovo valore non è vuoto,
                # così non sovrascriviamo eventuali dati esistenti con ""
                if _val_non_vuoto(val):
                    df.at[idx, master_col] = val
                    oc_written_fields += 1

    # ---------------------------------------------------------------------
    # FASE 4: Backfill coordinate via Nominatim (PER RIGA, con cache municipio)
    # ---------------------------------------------------------------------
    coord_aggiornate = 0
    nominatim_cache  = {}   # (municipio_lower, provincia_lower, regione_lower) → "lat,lng" o ""

    if rows_need_coord:
        _cb(0.75, f"Verifica {len(rows_need_coord)} coordinate mancanti via Nominatim...")
        for i, (idx, cup) in enumerate(rows_need_coord, 1):
            _cb(0.75 + (i / max(len(rows_need_coord), 1)) * 0.22,
                f"Geocoding {i}/{len(rows_need_coord)}: {cup}")

            # Priorità fonte: OpenCUP cache → master (Municipi_Coinvolti / Provincia_CUP)
            oc = cache.get(cup, {}) or {}
            municipio = oc.get("Municipi_Coinvolti", "") or (
                df.at[idx, "Municipi_Coinvolti"] if "Municipi_Coinvolti" in df.columns else ""
            )
            provincia = oc.get("Provincia", "") or (
                df.at[idx, "Provincia_CUP"] if "Provincia_CUP" in df.columns else ""
            )
            regione = df.at[idx, "Regione"] if "Regione" in df.columns else ""

            municipio_str = _primo_comune(municipio)
            provincia_str = str(provincia or "").strip()
            regione_str   = str(regione or "").strip()

            # Cache key: evita di chiamare Nominatim due volte per lo stesso (comune, provincia)
            key = (municipio_str.lower(), provincia_str.lower(), regione_str.lower())
            if key in nominatim_cache:
                nuova = nominatim_cache[key]
            else:
                nuova = _geocodifica_nominatim(municipio_str, provincia_str, regione_str)
                nominatim_cache[key] = nuova

            if nuova:
                df.at[idx, "Coordinate"] = nuova
                coord_aggiornate += 1
                print(f"  [NOMINATIM OK] {cup} ({municipio_str}, {provincia_str}) -> {nuova}")
            else:
                print(f"  [NOMINATIM KO] {cup} ({municipio_str}, {provincia_str}) - nessun risultato")

    # ---------------------------------------------------------------------
    # FASE 5: Salvataggio master (sempre, anche se nulla è cambiato)
    # ---------------------------------------------------------------------
    _cb(0.98, "Salvataggio master...")
    try:
        df.to_excel(MASTER_FILE, index=False)
    except PermissionError:
        raise PermissionError(
            f"Impossibile scrivere {MASTER_FILE.name}: il file è aperto in Excel "
            f"(o in un'altra applicazione). Chiudilo e riprova l'arricchimento."
        )

    msg_parts = []
    if rows_need_opencup:
        msg_parts.append(f"{len(rows_need_opencup)} righe arricchite ({oc_written_fields} campi scritti)")
    if rows_need_coord:
        msg_parts.append(f"{coord_aggiornate}/{len(rows_need_coord)} coord. recuperate")
    if rows_skip:
        msg_parts.append(f"{rows_skip} skip")
    _cb(1.0, "Master aggiornato · " + " · ".join(msg_parts) if msg_parts else "Master invariato")

    return {cup: cache.get(cup, {}) for cup in cups}
