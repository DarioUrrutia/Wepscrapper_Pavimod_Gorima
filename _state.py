"""
Estado compartido entre el hilo de scraping y la UI de Streamlit.
Este módulo se importa UNA SOLA VEZ por Python, por lo que los dicts
persisten entre reruns de Streamlit sin reiniciarse.
"""

import threading

scraper = {"pct": 0.0, "msg": "", "running": False, "error": None, "done": False}
comp    = {"pct": 0.0, "msg": "", "running": False, "error": None, "done": False}
enrich  = {"pct": 0.0, "msg": "", "running": False, "error": None, "done": False}

# Coordinamento del salvataggio su GitHub "a blocchi" (coalescing):
# le modifiche di Stato vengono raggruppate in un singolo commit invece di
# committare a ogni cambio. 'pending' = ci sono modifiche da salvare,
# 'saver_alive' = un thread saver è già in esecuzione (debounce).
github_save      = {"pending": False, "saver_alive": False}
github_save_lock = threading.Lock()
