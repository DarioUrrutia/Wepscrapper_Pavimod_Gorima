"""
Aggiornamento dati in LOCALE (rete che raggiunge ANAS).

Esegue: scraping ANAS -> comparativo -> aggiorna master_avanzamento.xlsx.
NON pubblica nulla da solo: il push lo fa Actualizar_Datos.bat, ma SOLO se
questo script termina con successo (codice 0).

Se ANAS non restituisce opere (rete bloccata/timeout), esce con codice 1 e
NON tocca il master, così non si pubblicano dati vecchi.

Uso diretto:
    python actualizar_datos.py
"""

import sys

# stdout in UTF-8: i print dello scraper contengono caratteri come €, — che
# fanno crashare la console Windows (cp1252).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import pandas as pd
from scraper import scrape
from comparador import actualizar_master, MASTER_FILE
from enriquecedor import enriquecer_obras


def _arricchisci_idempotente():
    """
    Arricchimento OpenCUP/coordinate dopo il comparativo.
    - Idempotente: enriquecer_obras() salta le righe già complete (no forza),
      quindi alla prima esecuzione le fa tutte, poi solo le nuove.
    - Salta le opere 'Non interessa' (inutile arricchire scarti).
    - BEST-EFFORT: un errore qui NON deve impedire la pubblicazione del master,
      che è già valido dopo il comparativo.
    """
    try:
        if not MASTER_FILE.exists():
            return
        m = pd.read_excel(MASTER_FILE, dtype=str).fillna("")
        if "Cup" not in m.columns:
            return
        if "Stato" in m.columns:
            m = m[m["Stato"] != "Non interessa"]
        cups = [c for c in m["Cup"].astype(str).str.strip().unique() if c and c not in ("None", "nan")]
        if not cups:
            print("  (nessun CUP da arricchire)")
            return
        print(f"  {len(cups)} CUP candidati (salta quelli già completi)...")
        enriquecer_obras(cups, progress_callback=lambda p, m: print(f"  [{int(p*100):3d}%] {m}"))
    except Exception as e:
        print(f"  [AVVISO] Arricchimento non completato: {e}")
        print("  Il master resta valido e verra' pubblicato comunque.")


def main():
    print("=" * 60)
    print("  AGGIORNAMENTO DATI ANAS (locale)")
    print("=" * 60)

    print("\n[1/3] Scraping ANAS...")
    result = scrape(progress_callback=lambda p, m: print(f"  [{int(p*100):3d}%] {m}"))

    # Guard: se lo scraping non ha prodotto dati, NON proseguire e NON pubblicare.
    if not result or not result.get("csv") or not result.get("total"):
        print("\n" + "!" * 60)
        print("  ERRORE: ANAS non ha restituito opere.")
        print("  Possibile blocco/timeout di rete verso stradeanas.it.")
        print("  Il master NON e' stato modificato. Niente da pubblicare.")
        print("!" * 60)
        return 1

    print(f"\n[2/3] Scraping OK ({result['total']} opere). Genero comparativo...")
    actualizar_master(result["csv"], progress_callback=lambda p, m: print(f"  [{int(p*100):3d}%] {m}"))

    print("\n[3/3] Arricchimento OpenCUP (idempotente, salta i già completi)...")
    _arricchisci_idempotente()

    print("\n" + "=" * 60)
    print(f"  FATTO. Master aggiornato con {result['total']} opere.")
    print("  Pronto per il push (lo fa il .bat).")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
