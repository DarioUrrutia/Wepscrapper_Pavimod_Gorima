"""
github_sync.py — Sincronizzazione dello stato PAVIMOD su GitHub via REST API

Usa le GitHub git-data API per creare commit atomici contenenti il master
Excel, la blacklist, la cache OpenCUP e gli ultimi CSV di scraping. Permette
all'app Streamlit (girante su Render con filesystem effimero) di persistere
lo stato tra una sessione e l'altra, committandolo direttamente sul repo.

Un commit contiene in un'unica atomica operazione:
  - data/processed/master_avanzamento.xlsx
  - data/processed/blacklist.json           (se esiste)
  - data/processed/anas_obras_*.csv         (tutti gli ultimi snapshot)
  - data/cache/opencup_cache.json           (se esiste)

Variabili ambiente richieste per funzionare:
  GITHUB_TOKEN   → Personal Access Token con scope Contents: Write
  GITHUB_OWNER   → default "DarioUrrutia"
  GITHUB_REPO    → default "Wepscrapper_Pavimod_Gorima"
  GITHUB_BRANCH  → default "main"

Se GITHUB_TOKEN non è definito, il modulo espone `token_configured() == False`
e tutte le chiamate al commit ritornano un errore esplicito (l'app disabilita
il bottone di salvataggio in UI).
"""

import os
import json
import base64
import requests
from pathlib import Path
from datetime import datetime

# ---------------------------------------------------------------------------
# Default del repo di destinazione (sovrascrivibili via env var)
# ---------------------------------------------------------------------------
DEFAULT_OWNER  = "DarioUrrutia"
DEFAULT_REPO   = "Wepscrapper_Pavimod_Gorima"
DEFAULT_BRANCH = "main"

# File "singolari" sempre inclusi (se esistono)
_FIXED_FILES = {
    "data/processed/master_avanzamento.xlsx": Path("data/processed/master_avanzamento.xlsx"),
    "data/processed/blacklist.json":          Path("data/processed/blacklist.json"),
    "data/cache/opencup_cache.json":          Path("data/cache/opencup_cache.json"),
}

# File di stato locale per tracciare l'ultimo save andato a buon fine
LAST_SAVE_FILE = Path("data/.last_save")


# ---------------------------------------------------------------------------
# Config helper
# ---------------------------------------------------------------------------
def _get_config() -> dict:
    return {
        "token":  os.getenv("GITHUB_TOKEN", ""),
        "owner":  os.getenv("GITHUB_OWNER",  DEFAULT_OWNER),
        "repo":   os.getenv("GITHUB_REPO",   DEFAULT_REPO),
        "branch": os.getenv("GITHUB_BRANCH", DEFAULT_BRANCH),
    }


def token_configured() -> bool:
    """True se la env var GITHUB_TOKEN è definita e non vuota."""
    return bool(_get_config()["token"])


def _headers(token: str) -> dict:
    return {
        "Authorization":        f"Bearer {token}",
        "Accept":               "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _api_base(cfg: dict) -> str:
    return f"https://api.github.com/repos/{cfg['owner']}/{cfg['repo']}"


# ---------------------------------------------------------------------------
# Scoperta file da committare
# ---------------------------------------------------------------------------
def list_files_to_commit() -> list:
    """
    Ritorna la lista dei file da includere nel prossimo commit come
    tuple (repo_path, local_path, size_bytes).
    Include i CSV di scraping correnti (il scraper tiene solo gli ultimi 5).
    Filtra via i file che non esistono realmente sul filesystem.
    """
    files: dict = dict(_FIXED_FILES)

    # Aggiungi tutti i CSV snapshot presenti in data/processed/
    csv_dir = Path("data/processed")
    if csv_dir.exists():
        for csv in sorted(csv_dir.glob("anas_obras_*.csv")):
            files[f"data/processed/{csv.name}"] = csv

    out = []
    for repo_path, local_path in files.items():
        if local_path.exists() and local_path.is_file():
            out.append((repo_path, local_path, local_path.stat().st_size))
    return out


# ---------------------------------------------------------------------------
# Tracciamento ultimo save
# ---------------------------------------------------------------------------
def _ensure_last_save_exists():
    """
    Crea il file .last_save con timestamp corrente se non esiste già.
    Serve a evitare che 'has_unsaved_changes()' dia falso positivo la prima
    volta che si avvia l'app su un deploy fresco (dove il master è stato
    appena clonato da git e la sua mtime è recente).
    """
    if LAST_SAVE_FILE.exists():
        return
    try:
        LAST_SAVE_FILE.parent.mkdir(parents=True, exist_ok=True)
        LAST_SAVE_FILE.write_text(json.dumps({
            "timestamp":  datetime.now().isoformat(),
            "commit_sha": "(uninitialized)",
            "files":      [],
        }), encoding="utf-8")
    except Exception:
        pass


def has_unsaved_changes() -> bool:
    """
    True se il master Excel è stato modificato DOPO l'ultimo save riuscito.
    Confronta la mtime del master con il timestamp in .last_save.
    """
    master = Path("data/processed/master_avanzamento.xlsx")
    if not master.exists():
        return False

    _ensure_last_save_exists()
    try:
        data = json.loads(LAST_SAVE_FILE.read_text(encoding="utf-8"))
        last_ts = datetime.fromisoformat(data["timestamp"]).timestamp()
        return master.stat().st_mtime > last_ts + 1  # +1s di tolleranza
    except Exception:
        return True


def get_last_save_info() -> dict:
    """Ritorna il dict dell'ultimo save (timestamp, commit_sha, files) o {} se assente."""
    if not LAST_SAVE_FILE.exists():
        return {}
    try:
        return json.loads(LAST_SAVE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Commit atomico via git-data API
# ---------------------------------------------------------------------------
def commit_files_to_github(progress_callback=None) -> dict:
    """
    Crea un commit atomico su GitHub con tutti i file di stato correnti.

    Usa git-data API per creare:
      1. Un blob per ogni file (upload base64)
      2. Un tree che referenzia i nuovi blob (basato sul tree corrente)
      3. Un commit che punta al nuovo tree (parent: ref HEAD attuale)
      4. Update del ref della branch al nuovo commit

    Parametri:
      progress_callback : opzionale, callable(pct: float, msg: str)

    Ritorna:
      dict con:
        ok          : bool
        error       : str              (se ok=False)
        commit_sha  : str              (se ok=True)
        commit_url  : str              (se ok=True)
        files       : list[str]        (se ok=True, path committati)
        num_files   : int              (se ok=True)
    """
    def _cb(pct, msg):
        if progress_callback:
            progress_callback(pct, msg)

    cfg = _get_config()
    if not cfg["token"]:
        return {"ok": False, "error": "GITHUB_TOKEN non configurato nelle env var"}

    file_list = list_files_to_commit()
    if not file_list:
        return {"ok": False, "error": "Nessun file da committare"}

    headers = _headers(cfg["token"])
    api     = _api_base(cfg)

    try:
        # 1. Ref della branch
        _cb(0.10, f"Lettura ref {cfg['branch']}...")
        r = requests.get(f"{api}/git/refs/heads/{cfg['branch']}", headers=headers, timeout=30)
        if r.status_code == 401:
            return {"ok": False, "error": "Token GitHub non valido (401 Unauthorized)"}
        if r.status_code == 403:
            return {"ok": False, "error": "Token GitHub senza permessi di scrittura (403 Forbidden)"}
        if r.status_code == 404:
            return {"ok": False, "error": f"Repo o branch non trovati: {cfg['owner']}/{cfg['repo']}:{cfg['branch']}"}
        r.raise_for_status()
        ref_sha = r.json()["object"]["sha"]

        # 2. Tree di base dal commit corrente
        _cb(0.18, "Lettura commit corrente...")
        r = requests.get(f"{api}/git/commits/{ref_sha}", headers=headers, timeout=30)
        r.raise_for_status()
        base_tree_sha = r.json()["tree"]["sha"]

        # 3. Crea un blob per ogni file
        tree_entries = []
        total = len(file_list)
        for i, (repo_path, local_path, _size) in enumerate(file_list, 1):
            _cb(0.20 + (i / total) * 0.55, f"Upload {i}/{total}: {local_path.name}")
            content = local_path.read_bytes()
            b64 = base64.b64encode(content).decode("ascii")
            r = requests.post(
                f"{api}/git/blobs",
                headers=headers,
                json={"content": b64, "encoding": "base64"},
                timeout=60,
            )
            r.raise_for_status()
            blob_sha = r.json()["sha"]
            tree_entries.append({
                "path": repo_path,
                "mode": "100644",
                "type": "blob",
                "sha":  blob_sha,
            })

        # 4. Crea il tree nuovo (basato su quello del commit corrente)
        _cb(0.80, "Creazione tree...")
        r = requests.post(
            f"{api}/git/trees",
            headers=headers,
            json={"base_tree": base_tree_sha, "tree": tree_entries},
            timeout=30,
        )
        r.raise_for_status()
        new_tree_sha = r.json()["sha"]

        # 5. Crea il commit
        _cb(0.88, "Creazione commit...")
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        commit_msg = (
            f"Update master state — {ts}\n\n"
            f"{len(tree_entries)} file sincronizzati dalla UI PAVIMOD:\n"
            + "\n".join(f"  - {e['path']}" for e in tree_entries)
        )
        r = requests.post(
            f"{api}/git/commits",
            headers=headers,
            json={
                "message": commit_msg,
                "tree":    new_tree_sha,
                "parents": [ref_sha],
            },
            timeout=30,
        )
        r.raise_for_status()
        new_commit_sha = r.json()["sha"]

        # 6. Update del ref della branch al nuovo commit
        _cb(0.95, "Aggiornamento ref branch...")
        r = requests.patch(
            f"{api}/git/refs/heads/{cfg['branch']}",
            headers=headers,
            json={"sha": new_commit_sha},
            timeout=30,
        )
        r.raise_for_status()

        # 7. Salva .last_save locale
        try:
            LAST_SAVE_FILE.parent.mkdir(parents=True, exist_ok=True)
            LAST_SAVE_FILE.write_text(json.dumps({
                "timestamp":  datetime.now().isoformat(),
                "commit_sha": new_commit_sha,
                "files":      [e["path"] for e in tree_entries],
            }), encoding="utf-8")
        except Exception as e:
            print(f"  [GITHUB_SYNC] Warning: impossibile scrivere .last_save: {e}")

        commit_url = f"https://github.com/{cfg['owner']}/{cfg['repo']}/commit/{new_commit_sha}"
        _cb(1.0, f"Commit creato: {new_commit_sha[:7]}")
        return {
            "ok":         True,
            "commit_sha": new_commit_sha,
            "commit_url": commit_url,
            "files":      [e["path"] for e in tree_entries],
            "num_files":  len(tree_entries),
        }

    except requests.HTTPError as e:
        body = e.response.text[:300] if e.response is not None else ""
        return {"ok": False, "error": f"HTTP {e.response.status_code if e.response else '?'}: {body}"}
    except requests.Timeout:
        return {"ok": False, "error": "Timeout nella chiamata a GitHub API (>60s)"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
