"""
Estado compartido entre el hilo de scraping y la UI de Streamlit.
Este módulo se importa UNA SOLA VEZ por Python, por lo que los dicts
persisten entre reruns de Streamlit sin reiniciarse.
"""

scraper = {"pct": 0.0, "msg": "", "running": False, "error": None, "done": False}
comp    = {"pct": 0.0, "msg": "", "running": False, "error": None, "done": False}
enrich  = {"pct": 0.0, "msg": "", "running": False, "error": None, "done": False}
