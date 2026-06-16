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

from scraper import scrape
from comparador import actualizar_master


def main():
    print("=" * 60)
    print("  AGGIORNAMENTO DATI ANAS (locale)")
    print("=" * 60)

    print("\n[1/2] Scraping ANAS...")
    result = scrape(progress_callback=lambda p, m: print(f"  [{int(p*100):3d}%] {m}"))

    # Guard: se lo scraping non ha prodotto dati, NON proseguire e NON pubblicare.
    if not result or not result.get("csv") or not result.get("total"):
        print("\n" + "!" * 60)
        print("  ERRORE: ANAS non ha restituito opere.")
        print("  Possibile blocco/timeout di rete verso stradeanas.it.")
        print("  Il master NON e' stato modificato. Niente da pubblicare.")
        print("!" * 60)
        return 1

    print(f"\n[2/2] Scraping OK ({result['total']} opere). Genero comparativo...")
    actualizar_master(result["csv"], progress_callback=lambda p, m: print(f"  [{int(p*100):3d}%] {m}"))

    print("\n" + "=" * 60)
    print(f"  FATTO. Master aggiornato con {result['total']} opere.")
    print("  Pronto per il push (lo fa il .bat).")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
