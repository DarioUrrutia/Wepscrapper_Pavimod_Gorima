"""
Comparador de scraping runs — ANAS Lavori in Corso

Mantiene un archivo master Excel con historial de avanzamiento por obra.
Cada nuevo scraping añade una columna Avanz_YYYY-MM-DD y recalcula Differenza.

Etiquetas en columna Differenza:
  - "Obra Nueva"              → CUP/Id aparece por primera vez
  - "Obra Conclusa"           → desaparece con último avance = 100%
  - "Obra Conclusa (probable)"→ desaparece con último avance >= 80% y < 100%
  - "Obra Desaparecida"       → desaparece con último avance < 80%
  - "+7.0%", "0%", "-3.0%"   → diferencia entre las dos últimas columnas

Uso directo:
    python comparador.py                        # usa el CSV más reciente
    python comparador.py data/processed/mi.csv  # usa un CSV específico
"""

import json
import pandas as pd
from pathlib import Path
from datetime import datetime
import sys

# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------
PROCESSED_DIR = Path("data/processed")
MASTER_FILE   = PROCESSED_DIR / "master_avanzamento.xlsx"
AVANZ_PREFIX  = "Avanz_"

# Colonne base che il comparativo gestisce (ordine di inserimento nel master).
# Regola unificata di merge: dal nuovo CSV si sovrascrivono SOLO i valori non
# vuoti → se lo scraping non porta niente di nuovo (campo vuoto) il valore
# precedente (incluso l'arricchimento OpenCUP/Nominatim) rimane intatto.
# Se invece ANAS fornisce un dato nuovo/diverso, il master viene aggiornato.
COLS_BASE = [
    "Id_ANAS", "Regione", "Codice_Strada", "Nome_Strada", "Cup",
    "Descrizione", "Tipo_Lavoro", "Impresa",
    "Importo_Principale", "Importo_Totale",
    "Data_Consegna_Impresa", "Data_Ultimazione_Prevista",
    "Dal_Km", "Al_Km", "Strade_Segmentos",
    "Coordinate",
    "Nome_Ufficiale_Progetto", "Anno_Decisione", "Provincia_CUP",
    "Municipi_Coinvolti", "Tipologia", "Settore", "Sottosettore",
    "Categoria_Settore", "Cup_Padre", "Progetti_Collegati_CUP",
]

KEY = "Id_ANAS"  # chiave tecnica per identificare la riga singola

# Blacklist persistente: progetti eliminati dall'utente che non devono
# più riapparire nei master successivi.
#
# Ogni entry è un dict con:
#   cup          → Codice Unico di Progetto (peso primario, obbligatorio)
#   regione      → Regione (conferma, obbligatoria per il match)
#   impresa      → Impresa (tracciabilità, NON usata per il match perché
#                  un contratto può cambiare appaltatore a progetto in corso)
#   descrizione  → Descrizione (display, non usata per il match)
#   added_at     → data eliminazione (display)
#
# Match logic:
#   Una riga è blacklistata se ∃ entry tale che:
#     - entry.cup == row.Cup                    (obbligatorio)
#     - entry.regione == row.Regione            (obbligatorio se presente)
BLACKLIST_FILE = PROCESSED_DIR / "blacklist.json"


def _es_vacio(val) -> bool:
    """True se il valore è vuoto / None / 'nan' / stringa vuota."""
    if val is None:
        return True
    s = str(val).strip()
    return s in ("", "None", "nan", "0.000000, 0.000000")


def _norm(val) -> str:
    """Normalizza una stringa per il match case-insensitive."""
    if val is None:
        return ""
    return str(val).strip().lower()


def load_blacklist() -> list:
    """
    Carica le entries della blacklist come lista di dict.
    Supporta backward compatibility con i formati precedenti:
      - {"entries": [{...}, ...]}     (formato attuale)
      - {"cups":    ["X", "Y"]}       (legacy, solo CUP)
      - {"ids":     ["X", "Y"]}       (legacy più vecchio, Id_ANAS — deprecato)
    """
    if not BLACKLIST_FILE.exists():
        return []
    try:
        with open(BLACKLIST_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data.get("entries"), list):
            return [e for e in data["entries"] if isinstance(e, dict) and e.get("cup")]
        # Legacy: migra automaticamente
        legacy = data.get("cups") or data.get("ids") or []
        return [{"cup": str(x).strip()} for x in legacy if str(x).strip()]
    except Exception as e:
        print(f"  [BLACKLIST] Errore lettura: {e}")
        return []


def save_blacklist(entries: list):
    """Salva la blacklist come lista di dict."""
    BLACKLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Deduplica: stessa chiave (cup, regione) = stesso progetto
    seen = {}
    for e in entries:
        if not isinstance(e, dict) or not e.get("cup"):
            continue
        key = (str(e.get("cup", "")).strip(), _norm(e.get("regione", "")))
        if key[0] and key not in seen:
            seen[key] = {
                "cup":         str(e.get("cup", "")).strip(),
                "regione":     str(e.get("regione", "")).strip(),
                "impresa":     str(e.get("impresa", "")).strip(),
                "descrizione": str(e.get("descrizione", "")).strip()[:140],
                "added_at":    str(e.get("added_at", "")).strip() or datetime.now().strftime("%Y-%m-%d"),
            }
    data = {"entries": sorted(seen.values(), key=lambda x: (x["cup"], x["regione"]))}
    with open(BLACKLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_to_blacklist(new_entries) -> int:
    """
    Aggiunge entries alla blacklist.
    `new_entries` può essere una lista di dict (con chiavi cup/regione/impresa/descrizione)
    oppure una lista di stringhe CUP (legacy).
    Restituisce quante NUOVE entries sono state aggiunte.
    """
    current = load_blacklist()
    existing_keys = {(e.get("cup", ""), _norm(e.get("regione", ""))) for e in current}

    added = 0
    for item in new_entries:
        if isinstance(item, str):
            entry = {"cup": item.strip()}
        elif isinstance(item, dict):
            entry = {
                "cup":         str(item.get("cup", item.get("Cup", ""))).strip(),
                "regione":     str(item.get("regione", item.get("Regione", ""))).strip(),
                "impresa":     str(item.get("impresa", item.get("Impresa", ""))).strip(),
                "descrizione": str(item.get("descrizione", item.get("Descrizione", ""))).strip(),
            }
        else:
            continue
        if not entry["cup"] or entry["cup"] in ("None", "nan"):
            continue
        key = (entry["cup"], _norm(entry.get("regione", "")))
        if key in existing_keys:
            continue
        entry["added_at"] = datetime.now().strftime("%Y-%m-%d")
        current.append(entry)
        existing_keys.add(key)
        added += 1

    save_blacklist(current)
    return added


def remove_from_blacklist(cups_or_entries) -> int:
    """
    Rimuove entries dalla blacklist. Accetta:
      - lista di stringhe CUP → rimuove tutte le entries con quel CUP
      - lista di dict con cup+regione → rimuove solo match esatti
    """
    current = load_blacklist()
    before = len(current)
    to_remove_cups = set()
    to_remove_keys = set()
    for item in cups_or_entries:
        if isinstance(item, str):
            to_remove_cups.add(str(item).strip())
        elif isinstance(item, dict):
            to_remove_keys.add((
                str(item.get("cup", "")).strip(),
                _norm(item.get("regione", "")),
            ))
    filtered = []
    for e in current:
        cup = str(e.get("cup", "")).strip()
        key = (cup, _norm(e.get("regione", "")))
        if cup in to_remove_cups or key in to_remove_keys:
            continue
        filtered.append(e)
    save_blacklist(filtered)
    return before - len(filtered)


def is_row_blacklisted(row, blacklist_entries) -> bool:
    """
    Verifica se una riga (dict o pd.Series) matcha una entry in blacklist.
    Match: stesso CUP AND (stessa Regione OR regione mancante nell'entry).
    Impresa è salvata per tracciabilità ma non usata per il match.
    """
    row_cup = str(row.get("Cup", "")).strip()
    if not row_cup:
        return False
    row_reg = _norm(row.get("Regione", ""))
    for entry in blacklist_entries:
        if entry.get("cup", "") != row_cup:
            continue
        ent_reg = _norm(entry.get("regione", ""))
        # CUP matcha. Verifica regione se presente in entry.
        if ent_reg and row_reg and ent_reg != row_reg:
            continue   # CUP uguale ma regione diversa → non è lo stesso progetto
        return True
    return False


def row_signature(row) -> tuple:
    """
    Firma composita per matching cross-scraping di una singola riga opera.
    Usa (Cup, Regione, Id_ANAS) come chiave:
      - CUP      → identificatore primario del progetto (peso massimo)
      - Regione  → conferma di coerenza (un progetto non cambia regione)
      - Id_ANAS  → distingue le diverse tratte/segmenti dello stesso progetto
    Due righe di scraping diversi matchano solo se TUTTE e tre le componenti
    corrispondono (esatte dopo normalizzazione). Questo rende il merge robusto
    a eventuali riassegnazioni interne di Id_ANAS o a bug di data entry.
    """
    return (
        str(row.get("Cup", "")).strip(),
        _norm(row.get("Regione", "")),
        str(row.get(KEY, "")).strip(),
    )


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _cols_avanz(cols):
    """Devuelve columnas Avanz_DD-MM-YYYY ordenadas cronológicamente."""
    avanz = [c for c in cols if c.startswith(AVANZ_PREFIX)]
    def _sort_key(c):
        try:
            parte = c.replace(AVANZ_PREFIX, "")  # DD-MM-YYYY o DD-MM-YYYY_HHmm
            data  = parte.split("_")[0]           # toma solo DD-MM-YYYY
            hhmm  = parte.split("_")[1] if "_" in parte else "0000"
            d, m, y = data.split("-")
            return (int(y), int(m), int(d), int(hhmm))
        except Exception:
            return (0, 0, 0, 0)
    return sorted(avanz, key=_sort_key)


def _ultimo_avanz_conocido(row, avanz_cols):
    """Devuelve el último valor de avanzamiento no nulo de una fila."""
    for col in reversed(avanz_cols):
        val = row.get(col)
        if pd.notna(val) and str(val).strip() not in ("", "None", "nan"):
            return val
    return None


def _parsear_pct(val):
    """Convierte '45%', '45.0', 45 a float (0-100). None si no se puede."""
    if val is None:
        return None
    if isinstance(val, float) and pd.isna(val):
        return None
    s = str(val).strip().replace("%", "").replace(",", ".").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _formatear_diff(actual, anterior):
    """Formatea la diferencia entre dos porcentajes."""
    if actual is None or anterior is None:
        return ""
    diff = actual - anterior
    if diff > 0:
        return f"+{diff:.1f}%"
    elif diff < 0:
        return f"{diff:.1f}%"
    else:
        return "0%"


# ---------------------------------------------------------------------------
# Lógica principal
# ---------------------------------------------------------------------------
def actualizar_master(nuevo_csv_path, fecha=None, progress_callback=None):
    """
    Actualiza el archivo master con los datos del nuevo scraping.

    Parámetros:
        nuevo_csv_path    : str o Path — ruta al CSV generado por el scraper
        fecha             : str YYYY-MM-DD — fecha del scraping (default: hoy)
        progress_callback : callable(pct: float, msg: str) — opcional
    """
    def _cb(pct, msg):
        print(f"  [COMPARADOR] [{int(pct*100):3d}%] {msg}")
        if progress_callback:
            progress_callback(pct, msg)

    nuevo_csv_path = Path(nuevo_csv_path)
    if fecha is None:
        fecha = datetime.now().strftime("%d-%m-%Y_%H%M")

    col_nueva = f"{AVANZ_PREFIX}{fecha}"

    _cb(0.05, f"Cargando CSV del scraping ({fecha})...")
    print(f"\n  [COMPARADOR] Fecha scraping : {fecha}")
    print(f"  [COMPARADOR] Columna nueva  : {col_nueva}")

    # Cargar nuevo CSV
    df_nuevo = pd.read_csv(nuevo_csv_path, dtype=str).fillna("")
    df_nuevo[KEY] = df_nuevo[KEY].str.strip()

    # Eliminar duplicados en Id_ANAS (pueden ocurrir por fetch paralelo de varias strade)
    n_antes = len(df_nuevo)
    df_nuevo = df_nuevo.drop_duplicates(subset=[KEY], keep="first")
    n_dedup = n_antes - len(df_nuevo)
    if n_dedup:
        print(f"  [COMPARADOR] Duplicati {KEY} rimossi dal CSV: {n_dedup}")

    # Applica blacklist PRIMA di tutto: i progetti esclusi dall'utente
    # non devono nemmeno entrare nella pipeline di merge.
    # Match composito: CUP + Regione (vedi is_row_blacklisted per dettagli).
    _blacklist_iniziale = load_blacklist()
    if _blacklist_iniziale and "Cup" in df_nuevo.columns:
        n_bl = len(df_nuevo)
        _mask_bl = df_nuevo.apply(lambda r: is_row_blacklisted(r, _blacklist_iniziale), axis=1)
        df_nuevo = df_nuevo[~_mask_bl].reset_index(drop=True)
        n_bl -= len(df_nuevo)
        if n_bl:
            print(f"  [COMPARADOR] Blacklist filtrata dal CSV: {n_bl} righe di {len(_blacklist_iniziale)} progetti esclusi")

    # Asegurar que existan columnas base (por si alguna falta en versiones antiguas)
    for col in COLS_BASE:
        if col not in df_nuevo.columns:
            df_nuevo[col] = ""

    # -----------------------------------------------------------------------
    # CASO 1: No existe master → crear desde cero
    # -----------------------------------------------------------------------
    if not MASTER_FILE.exists():
        _cb(0.50, "Creando master por primera vez...")
        df_master = df_nuevo[COLS_BASE].copy()
        df_master[col_nueva] = df_nuevo["Avanzamento_Lavori"]
        df_master["Differenza"] = "Obra Nueva"

        _cb(0.90, "Guardando master...")
        df_master.to_excel(MASTER_FILE, index=False)

        _cb(1.0, f"Master creado — {len(df_master)} obras registradas")
        print(f"  [COMPARADOR] Master creado      : {MASTER_FILE}")
        print(f"  [COMPARADOR] Obras registradas  : {len(df_master)}")
        return

    # -----------------------------------------------------------------------
    # CASO 2: Master existe → actualizar
    # -----------------------------------------------------------------------
    _cb(0.15, "Cargando master existente...")
    df_master = pd.read_excel(MASTER_FILE, dtype=str).fillna("")
    df_master[KEY] = df_master[KEY].str.strip()
    df_master = df_master.drop_duplicates(subset=[KEY], keep="first")

    avanz_cols_prev = _cols_avanz(df_master.columns.tolist())

    # Evitar duplicar columna si ya existe (re-ejecución del mismo día)
    if col_nueva in df_master.columns:
        print(f"  [COMPARADOR] La columna {col_nueva} ya existe, se sobreescribe.")
        df_master = df_master.drop(columns=[col_nueva, "Differenza"], errors="ignore")
        avanz_cols_prev = _cols_avanz(df_master.columns.tolist())
    else:
        df_master = df_master.drop(columns=["Differenza"], errors="ignore")

    # -----------------------------------------------------------------------
    # Matching composito delle righe tra master e nuovo CSV
    # Firma = (Cup, Regione, Id_ANAS) — vedi row_signature()
    #   CUP      = peso primario (Codice Unico Progetto)
    #   Regione  = conferma di identità progetto
    #   Id_ANAS  = distingue tratte/segmenti dello stesso progetto
    # Due righe matchano SOLO se tutte e tre le componenti sono uguali.
    # -----------------------------------------------------------------------
    sig_to_id_master = {row_signature(r): r[KEY] for _, r in df_master.iterrows()}
    sig_to_id_nuevo  = {row_signature(r): r[KEY] for _, r in df_nuevo.iterrows()}

    sigs_master = set(sig_to_id_master.keys())
    sigs_nuevo  = set(sig_to_id_nuevo.keys())

    sigs_comunes     = sigs_master & sigs_nuevo
    sigs_nuevas      = sigs_nuevo  - sigs_master
    sigs_desaparec   = sigs_master - sigs_nuevo

    # Converte in set di Id_ANAS (usati dal resto della pipeline)
    ids_comunes       = {sig_to_id_master[s] for s in sigs_comunes}
    ids_nuevas_obras  = {sig_to_id_nuevo[s]  for s in sigs_nuevas}
    ids_desaparecidos = {sig_to_id_master[s] for s in sigs_desaparec}

    # Índice del nuevo CSV para acceso rápido
    df_nuevo_idx = df_nuevo.set_index(KEY)

    _cb(0.40, f"Procesando {len(ids_comunes)} obras continuas, {len(ids_nuevas_obras)} nuevas, {len(ids_desaparecidos)} desaparecidas...")
    # Regola di merge unificata: per ogni colonna base, sovrascriviamo il
    # valore del master SOLO se il nuovo CSV ha un valore non vuoto.
    #   - Se lo scraping porta un valore nuovo/diverso → aggiorna ✓
    #   - Se lo scraping ha il campo vuoto             → preserva il master ✓
    # Così l'arricchimento OpenCUP/Nominatim precedente non viene cancellato
    # da un nuovo scraping che lascia quelle colonne vuote.
    aggiornate = 0
    preservate = 0
    for id_obra in ids_comunes:
        mask = df_master[KEY] == id_obra
        for col in COLS_BASE:
            if col == KEY or col not in df_nuevo_idx.columns or col not in df_master.columns:
                continue
            val_nuevo = df_nuevo_idx.at[id_obra, col]
            if _es_vacio(val_nuevo):
                preservate += 1    # nuovo CSV vuoto → preserva master
            else:
                df_master.loc[mask, col] = val_nuevo
                aggiornate += 1

    # Añadir columna nueva de avanzamiento para obras existentes
    df_master[col_nueva] = ""
    for id_obra in ids_comunes:
        df_master.loc[df_master[KEY] == id_obra, col_nueva] = \
            df_nuevo_idx.at[id_obra, "Avanzamento_Lavori"]

    # Añadir filas de obras nuevas (tomando TODAS las columnas base del CSV)
    if ids_nuevas_obras:
        cols_para_nuevas = [c for c in COLS_BASE if c in df_nuevo.columns] + ["Avanzamento_Lavori"]
        nuevas_filas = df_nuevo[df_nuevo[KEY].isin(ids_nuevas_obras)][cols_para_nuevas].copy()
        nuevas_filas = nuevas_filas.rename(columns={"Avanzamento_Lavori": col_nueva})
        for col in avanz_cols_prev:
            nuevas_filas[col] = ""
        df_master = pd.concat([df_master, nuevas_filas], ignore_index=True)

    # -----------------------------------------------------------------------
    # Calcular columna Differenza
    # -----------------------------------------------------------------------
    _cb(0.70, "Calculando diferencias de avanzamiento...")
    avanz_cols_todas = _cols_avanz(df_master.columns.tolist())  # incluye col_nueva

    def calcular_differenza(row):
        id_obra = row[KEY]

        # Obra nueva
        if id_obra in ids_nuevas_obras:
            return "Obra Nueva"

        # Obra desaparecida — evaluar último avance conocido
        if id_obra in ids_desaparecidos:
            ultimo_val = _ultimo_avanz_conocido(row, avanz_cols_prev)
            pct = _parsear_pct(ultimo_val)
            if pct is None:
                return "Obra Desaparecida"
            elif pct >= 100:
                return "Obra Conclusa"
            elif pct >= 80:
                return "Obra Conclusa (probable)"
            else:
                return "Obra Desaparecida"

        # Obra continua → diferencia entre las dos últimas columnas de avanzamiento
        if len(avanz_cols_todas) >= 2:
            col_ant = avanz_cols_todas[-2]
            col_act = avanz_cols_todas[-1]
            anterior = _parsear_pct(row.get(col_ant))
            actual   = _parsear_pct(row.get(col_act))
            return _formatear_diff(actual, anterior)

        return ""

    df_master["Differenza"] = df_master.apply(calcular_differenza, axis=1)

    # -----------------------------------------------------------------------
    # Applica blacklist — rimuove le opere che l'utente ha escluso
    # Match composito CUP + Regione (is_row_blacklisted)
    # -----------------------------------------------------------------------
    blacklist = load_blacklist()
    rimosse_blacklist = 0
    if blacklist and "Cup" in df_master.columns:
        antes = len(df_master)
        _mask = df_master.apply(lambda r: is_row_blacklisted(r, blacklist), axis=1)
        df_master = df_master[~_mask].reset_index(drop=True)
        rimosse_blacklist = antes - len(df_master)
        print(f"  [COMPARADOR] Blacklist applicata : {rimosse_blacklist} righe di {len(blacklist)} progetti esclusi")

    # -----------------------------------------------------------------------
    # Guardar master actualizado
    # -----------------------------------------------------------------------
    _cb(0.88, "Guardando master actualizado...")
    try:
        df_master.to_excel(MASTER_FILE, index=False)
    except PermissionError:
        raise PermissionError(
            f"Impossibile scrivere {MASTER_FILE.name}: il file è aperto in Excel "
            f"(o in un'altra applicazione). Chiudilo e riprova il comparativo."
        )

    msg_bl = f" · {rimosse_blacklist} in blacklist" if rimosse_blacklist else ""
    _cb(1.0, f"Completato — {len(df_master)} opere · {aggiornate} aggiornate · {preservate} preservate{msg_bl}")
    print(f"  [COMPARADOR] Master actualizado   : {MASTER_FILE}")
    print(f"  [COMPARADOR] Obras totales        : {len(df_master)}")
    print(f"  [COMPARADOR] Obras nuevas         : {len(ids_nuevas_obras)}")
    print(f"  [COMPARADOR] Obras desaparecidas  : {len(ids_desaparecidos)}")
    print(f"  [COMPARADOR] Obras continuas      : {len(ids_comunes)}")
    print(f"  [COMPARADOR] Valori aggiornati    : {aggiornate}")
    print(f"  [COMPARADOR] Valori preservati    : {preservate} (nuovo CSV vuoto)")
    print(f"  [COMPARADOR] Blacklist totale     : {len(blacklist)} ({rimosse_blacklist} rimosse da questo run)")
    print(f"  [COMPARADOR] Columnas avanz.      : {avanz_cols_todas}")


# ---------------------------------------------------------------------------
# Ejecución directa
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if len(sys.argv) > 1:
        actualizar_master(sys.argv[1])
    else:
        csvs = sorted(PROCESSED_DIR.glob("anas_obras_*.csv"))
        if not csvs:
            print("No se encontró ningún CSV en data/processed/")
            sys.exit(1)
        print(f"  Usando CSV más reciente: {csvs[-1].name}")
        actualizar_master(csvs[-1])
