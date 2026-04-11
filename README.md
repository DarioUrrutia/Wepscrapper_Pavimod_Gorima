# ANAS Lavori in Corso — Monitor PAVIMOD

Applicazione completa per il **monitoraggio temporale delle opere viarie in corso** gestite da ANAS (Agenzia Nazionale per la Sicurezza delle Strade) nelle regioni del Centro-Sud Italia. Combina uno **scraper multi-fonte**, un **motore di comparativi storici**, un **arricchimento on-demand** e un **frontend Streamlit** con branding PAVIMOD.

L'obiettivo non è solo fotografare lo stato attuale dei cantieri, ma **tracciare il loro movimento nel tempo**: velocità di esecuzione, apparizioni, stalli, sparizioni e conclusioni. Ogni esecuzione periodica dello scraper aggiunge una nuova colonna storica di avanzamento, permettendo di vedere a colpo d'occhio se un'opera sta procedendo, è ferma, o è stata chiusa.

---

## Indice

- [Funzionalità principali](#funzionalità-principali)
- [Regioni monitorate](#regioni-monitorate)
- [Architettura e flusso dati](#architettura-e-flusso-dati)
- [Moduli del progetto](#moduli-del-progetto)
- [Colonne del master](#colonne-del-master)
- [Logica di matching composito](#logica-di-matching-composito)
- [Blacklist persistente](#blacklist-persistente)
- [Regole di merge idempotente](#regole-di-merge-idempotente)
- [Arricchimento OpenCUP + Nominatim](#arricchimento-opencup--nominatim)
- [Installazione](#installazione)
- [Uso dell'app Streamlit](#uso-dellapp-streamlit)
- [Uso da CLI](#uso-da-cli)
- [Fonti dati e rate limit](#fonti-dati-e-rate-limit)
- [Struttura del repository](#struttura-del-repository)
- [Credenziali](#credenziali)

---

## Funzionalità principali

- **Scraping ANAS parallelo** — 6 regioni, `ThreadPoolExecutor` a 3 worker, rate-limit rispettato
- **Filtro 10M€** — scarta opere con importo totale inferiore a 10 milioni di euro
- **Comparativo storico** — ogni esecuzione aggiunge una colonna `Avanz_DD-MM-YYYY_HHmm` al master Excel
- **Colonna `Differenza`** automatica: `+N.N%`, `0%`, `-N.N%`, `Obra Nueva`, `Obra Conclusa`, `Obra Conclusa (probable)`, `Obra Desaparecida`
- **Arricchimento OpenCUP on-demand** — download dati ufficiali dal registro CUP nazionale per i progetti selezionati
- **Backfill coordinate via Nominatim** — quando ANAS non fornisce lat/lng, geocoding automatico da Municipio + Provincia OpenCUP
- **Blacklist persistente** — elimina progetti che non ti interessano, con match composito CUP + Regione; non torneranno nei scraping successivi
- **Frontend Streamlit PAVIMOD** — filtri multi-colonna, metriche live, download CSV, selezione rapida, colonne configurabili
- **Password protection** sul bottone scraping (configurabile in `app.py`)
- **Preservazione arricchimenti** — gli scraping successivi non cancellano mai i dati arricchiti manualmente

---

## Regioni monitorate

| Regione | Codice ANAS |
|---|---|
| Sicilia | `SICILIA` |
| Basilicata | `BASILICATA` |
| Puglia | `PUGLIA` |
| Calabria | `CALABRIA` |
| Molise | `MOLISE` |
| Campania | `CAMPANIA` |

La lista è configurabile in [`scraper.py`](scraper.py) nella costante `REGIONES`.

---

## Architettura e flusso dati

```
┌──────────────────────────────────────────────────────────────────┐
│                      UTENTE (browser)                            │
│                  http://localhost:8501                           │
└───────────────────────┬──────────────────────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────────────────────┐
│                      app.py (Streamlit)                          │
│  ─ filtri, metriche, tabella, bottoni, notifiche                 │
│  ─ thread di background per scraping/comparativo/arricchimento   │
│  ─ stato progressivo condiviso via _state.py                     │
└───┬────────────────┬────────────────┬────────────────────────────┘
    │                │                │
    ▼                ▼                ▼
┌─────────┐   ┌─────────────┐   ┌──────────────┐
│scraper  │   │comparador   │   │enriquecedor  │
│  .py    │   │  .py        │   │  .py         │
└────┬────┘   └──────┬──────┘   └──────┬───────┘
     │               │                 │
     │  ANAS         │  merge          │  OpenCUP + Nominatim
     │  (CSV)        │  signature      │  (per riga, idempotente)
     ▼               ▼                 ▼
┌──────────────────────────────────────────────────┐
│              data/                               │
│  processed/                                      │
│    ├ anas_obras_TIMESTAMP.csv  ← output scraper  │
│    ├ master_avanzamento.xlsx   ← master storico  │
│    └ blacklist.json            ← progetti esclusi│
│  cache/                                          │
│    └ opencup_cache.json        ← cache OpenCUP   │
│  raw/     runs/                ← intermediate    │
└──────────────────────────────────────────────────┘
```

### Flusso operativo tipico

1. **Primo utilizzo** (master non esistente)
   - `Esegui Scraping` → genera `anas_obras_TIMESTAMP.csv`
   - `Genera Comparativo` → crea `master_avanzamento.xlsx` con tutte le opere marcate `Obra Nueva`
   - (opzionale) `Arricchisci OpenCUP` sui progetti di interesse → riempie Nome_Ufficiale_Progetto, Municipi_Coinvolti, coordinate Nominatim, ecc.

2. **Iterativo** (master esistente, ogni 15-30 giorni)
   - `Esegui Scraping` → nuovo CSV
   - `Genera Comparativo` → aggiunge `Avanz_DD-MM-YYYY_HHmm` al master, aggiorna `Differenza`, preserva gli arricchimenti precedenti, filtra la blacklist
   - Vedi nella colonna `Differenza` quali cantieri si sono mossi (`+N%`), fermati (`0%`), conclusi (`Obra Conclusa`) o spariti (`Obra Desaparecida`)

---

## Moduli del progetto

| File | Responsabilità |
|---|---|
| [`app.py`](app.py) | Frontend Streamlit: UI, filtri, tabella, bottoni, thread management, notifiche di sessione |
| [`scraper.py`](scraper.py) | Scraper ANAS parallelo multi-regione, filtro importi, generazione CSV |
| [`comparador.py`](comparador.py) | Merge iterativo nuovi CSV nel master Excel, gestione colonne storiche `Avanz_*`, calcolo `Differenza`, blacklist filter, matching composito |
| [`enriquecedor.py`](enriquecedor.py) | Download OpenCUP on-demand + backfill coordinate via Nominatim; idempotente per riga (gestisce multi-tratta) |
| [`_state.py`](_state.py) | Stato globale thread-safe per le barre di avanzamento Streamlit (persiste tra i rerun) |
| [`.streamlit/config.toml`](.streamlit/config.toml) | Tema PAVIMOD (rosso `#CC2229`, grigio `#6D6E71`) |

---

## Colonne del master

Il file [`data/processed/master_avanzamento.xlsx`](data/processed/master_avanzamento.xlsx) è la fonte di verità. Ogni riga è un'opera (tratta/segmento), ogni colonna un attributo.

### Dati di identificazione (sempre aggiornati da ANAS)

| Colonna | Descrizione |
|---|---|
| `Id_ANAS` | ID interno ANAS (chiave tecnica di riga) |
| `Regione` | Regione italiana |
| `Codice_Strada` | Codice strada (es. `SS106`) |
| `Nome_Strada` | Nome completo della strada |
| `Cup` | **Codice Unico di Progetto** (chiave logica, stabile) |

### Dati di contratto (aggiornati se ANAS cambia)

| Colonna | Descrizione |
|---|---|
| `Descrizione` | Descrizione completa dei lavori |
| `Tipo_Lavoro` | Manutenzione / Nuova costruzione / ecc. |
| `Impresa` | Impresa esecutrice |
| `Importo_Principale` | Importo lavori principali (€) |
| `Importo_Totale` | Importo totale contratto (€) |
| `Data_Consegna_Impresa` | Data consegna lavori |
| `Data_Ultimazione_Prevista` | Data prevista ultimazione |
| `Dal_Km` / `Al_Km` | Progressive km del tratto |
| `Strade_Segmentos` | Elenco tratti se più segmenti |

### Coordinate

| Colonna | Fonte | Descrizione |
|---|---|---|
| `Coordinate` | ANAS o Nominatim | `lat, lng` con 6 decimali. ANAS prima; se vuoto, geocoding Nominatim al primo arricchimento. |

### Dati OpenCUP (on-demand via arricchimento)

| Colonna | Descrizione |
|---|---|
| `Nome_Ufficiale_Progetto` | Titolo ufficiale dal registro CUP |
| `Anno_Decisione` | Anno registrazione CUP |
| `Provincia_CUP` | Provincia del progetto |
| `Municipi_Coinvolti` | Comuni interessati |
| `Tipologia` | Tipologia CUP |
| `Settore` | Settore CUP |
| `Sottosettore` | Sottosettore |
| `Categoria_Settore` | Categoria di spesa |
| `Cup_Padre` | CUP del progetto padre (se sotto-progetto) |
| `Progetti_Collegati_CUP` | Numero di CUP collegati |

### Colonne storiche (generate dal comparativo)

| Colonna | Descrizione |
|---|---|
| `Avanz_DD-MM-YYYY_HHmm` | Percentuale di avanzamento a quella data. Una per ogni scraping. |
| `Differenza` | **Calcolata automaticamente** sulla base delle ultime due colonne `Avanz_*`. Valori possibili: <br>• `+N.N%` → opera in avanzamento <br>• `0%` → opera ferma <br>• `-N.N%` → regressione (raro) <br>• `Obra Nueva` → prima apparizione <br>• `Obra Conclusa` → sparita con ultimo avanzamento ≥ 100% <br>• `Obra Conclusa (probable)` → sparita con ultimo avanzamento ≥ 80% <br>• `Obra Desaparecida` → sparita con ultimo avanzamento < 80% |

---

## Logica di matching composito

Due sistemi di identificazione convivono:

| Chiave | Ruolo | Stabilità | Usata da |
|---|---|---|---|
| `Id_ANAS` | Identificativo tecnico di riga | Interno ANAS, potenzialmente instabile | Comparator (con verifica) |
| `Cup` | Identificativo logico di progetto | Codice Unico di Progetto nazionale italiano, **stabile per sempre** | Blacklist, arricchimento, filtri cross-tempo |

### Firma composita delle righe

Per il matching tra master e nuovo CSV, il comparator usa una **tripla**:

```python
def row_signature(row):
    return (
        str(row.get("Cup", "")).strip(),          # peso primario: progetto
        _norm(row.get("Regione", "")),            # conferma geografica
        str(row.get("Id_ANAS", "")).strip(),      # tratta/segmento
    )
```

Due righe vengono considerate **la stessa opera** solo se **tutte e tre** le componenti coincidono. Questo significa:

- **CUP diverso** → opere diverse (safety)
- **CUP uguale ma Regione diversa** → probabile errore dati, trattate come opere diverse
- **CUP + Regione uguali ma Id_ANAS diverso** → tratte/segmenti diversi dello stesso progetto (corretto)

---

## Blacklist persistente

File: [`data/processed/blacklist.json`](data/processed/blacklist.json)

Se un progetto non ti interessa (es. opera fuori scope geografico/tipologico), selezionalo nella tabella e clicca **🗑 Elimina selezionate**. Viene salvato in blacklist con i campi:

```json
{
  "entries": [
    {
      "cup": "F87H17000190001",
      "regione": "Sicilia",
      "impresa": "CONSORZIO STABILE INFRA.TECH S.C.A.R.L.",
      "descrizione": "Lavori di MS di risanamento strutturale...",
      "added_at": "2026-04-11"
    }
  ]
}
```

### Match della blacklist

Una riga viene esclusa se **esiste** una entry in blacklist tale che:

- `entry.cup == row.Cup` (obbligatorio)
- `entry.regione == row.Regione` (obbligatorio, se presente nell'entry)

L'**Impresa** è salvata per tracciabilità ma **non** fa parte del match: se cambia appaltatore a metà progetto (evento possibile), l'esclusione continua a funzionare correttamente.

### Effetto sui scraping futuri

Il comparator applica la blacklist in **due punti**:

1. **In ingresso**: appena caricato il nuovo CSV, le righe blacklistate vengono filtrate subito (non partecipano nemmeno al merge)
2. **In uscita**: prima del salvataggio finale del master (safety net)

Così i progetti eliminati non tornano mai, anche se ANAS continua a pubblicarli.

---

## Regole di merge idempotente

Quando il comparator fonde il nuovo CSV con il master esistente, applica **una regola unificata**:

> Sovrascrivi una colonna del master **solo se il nuovo CSV ha un valore non vuoto**. Altrimenti lascia stare.

Questo significa:

- Se lo scraping porta un valore nuovo (diverso o identico al precedente, ma non vuoto) → **aggiorna** ✓
- Se lo scraping ha il campo vuoto → **preserva** il master ✓

L'effetto collaterale positivo: i dati di arricchimento OpenCUP/Nominatim (che lo scraper non tocca, lascia vuoti nel CSV) **non vengono mai cancellati** da un comparativo successivo. E se ANAS un giorno fornisce coordinate reali per un'opera che prima era geocodificata solo via Nominatim, il master viene aggiornato al dato più preciso.

### Colonne ANAS vs Arricchimento

| Colonna | Fonte normale | Comportamento merge |
|---|---|---|
| `Regione`, `Descrizione`, `Importo_*`, `Data_*`, `Dal_Km`, `Al_Km`, `Strade_Segmentos`, `Tipo_Lavoro`, `Impresa` | ANAS CSV | Aggiornate quasi sempre (ANAS le fornisce) |
| `Coordinate` | ANAS CSV o Nominatim | Se ANAS ora le fornisce, update. Altrimenti preservate. |
| `Nome_Ufficiale_Progetto` e altre OpenCUP | Arricchimento | Mai sovrascritte dal comparator (CSV sempre vuoto). Solo l'arricchimento le modifica. |

### Colonna `Avanz_*` — sempre creata

Ad ogni esecuzione del comparativo viene **sempre** aggiunta una nuova colonna `Avanz_DD-MM-YYYY_HHmm` (con data + ora + minuto per permettere esecuzioni multiple nello stesso giorno). Il valore viene scritto anche se è **identico a quello del periodo precedente**, perché questo è proprio il segnale che il cantiere è fermo — la colonna `Differenza` mostrerà `0%`.

---

## Arricchimento OpenCUP + Nominatim

Innescato dal bottone **🔍 Arricchisci con OpenCUP** nella UI, su una selezione di progetti.

### Logica idempotente (per riga)

La decisione "serve arricchire?" viene presa **per ogni riga del master individualmente**:

1. Se `Nome_Ufficiale_Progetto` è vuoto → scaricare OpenCUP per quella riga
2. Se `Coordinate` è vuoto → geocodificare via Nominatim per quella riga
3. Se entrambi sono pieni → `skip` totale, nessuna chiamata di rete

Questo garantisce che i progetti con **più tratte** (stesso CUP, Id_ANAS diversi) vengano gestiti correttamente: ogni tratta viene valutata singolarmente. Se una ha già le coordinate ma le altre no, solo le altre verranno geocodificate.

### Safe-write

La scrittura dei campi OpenCUP usa una safe-write:

```python
if _val_non_vuoto(val):
    df.at[idx, master_col] = val
```

Se OpenCUP restituisce stringa vuota per un campo (es. `Tipologia` non disponibile per un vecchio progetto), **non sovrascrive** un eventuale valore già presente. Mai `df.at[idx, col] = ""` con overwrite.

### Cache Nominatim per comune

Durante il backfill coordinate, le chiamate Nominatim vengono dedotte per `(municipio, provincia, regione)`:

```python
key = (municipio.lower(), provincia.lower(), regione.lower())
if key in nominatim_cache:
    nuova = nominatim_cache[key]
else:
    nuova = _geocodifica_nominatim(...)
    nominatim_cache[key] = nuova
```

Se un progetto ha 5 tratte tutte in "Palermo, PA", Nominatim viene interrogato **una sola volta** e il risultato applicato a tutte le 5 righe — rispetto del rate limit (1 req/sec) e velocità 5× superiore.

### Cache OpenCUP persistente

File: [`data/cache/opencup_cache.json`](data/cache/opencup_cache.json)

Tutte le risposte OpenCUP vengono salvate qui. Se richiedi l'arricchimento di un CUP già in cache, viene usato senza una nuova chiamata di rete. Se vuoi forzare un refresh, elimina il file.

---

## Installazione

```bash
# Clona il repo
git clone https://github.com/DarioUrrutia/Wepscrapper_Pavimod_Gorima.git
cd Wepscrapper_Pavimod_Gorima

# Crea virtual env (Python 3.10+)
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash
# oppure
.venv\Scripts\activate          # Windows PowerShell/CMD
# oppure
source .venv/bin/activate       # macOS/Linux

# Installa le dipendenze
pip install -r requirements.txt
```

### Dipendenze

```
requests         # HTTP client per ANAS, OpenCUP, Nominatim
pandas           # DataFrame per CSV/Excel
openpyxl         # Lettura/scrittura .xlsx
beautifulsoup4   # Parsing HTML OpenCUP
lxml             # Parser veloce per BeautifulSoup
streamlit        # Frontend web
Pillow           # Logo PAVIMOD nel sidebar
```

---

## Uso dell'app Streamlit

```bash
streamlit run app.py
```

Browser: **http://localhost:8501**

### Caratteristiche dell'UI

- **Header PAVIMOD** con logo e info regioni coperte
- **Bottoni azione**
  - `▶ Esegui Scraping` (protetto da password, default `Pavimodvai`)
  - `⚡ Genera Comparativo` (attivo solo dopo uno scraping)
- **Metriche live**: totale opere, nuove, concluse, scomparse, importo totale
- **Sidebar filtri**: regione, avanzamento %, stato opera, tipo lavoro, cerca testo
- **Tabella interattiva** con:
  - Checkbox selezione per arricchimento/eliminazione
  - `Seleziona tutti` / `Deseleziona tutti` / `🗑 Elimina` in cima
  - Popover `⚙ Colonne` per scegliere e ordinare le colonne visibili
  - Link cliccabile su `Coordinate` → apre Google Maps
  - Altezza fissa per mostrare 15 righe, scroll interno per il resto
- **Download CSV** filtrato con tutte le colonne (non solo quelle visibili)
- **Notifiche non invasive** in cima (success comparativo) e in fondo (success arricchimento)

### Password scraping

Default: `Pavimodvai`. Cambiabile in [`app.py`](app.py):

```python
PASSWORD_SCRAPING = "Pavimodvai"
```

---

## Uso da CLI

Puoi eseguire i singoli moduli anche senza frontend:

```bash
# Solo scraping ANAS (genera CSV)
python scraper.py

# Solo comparativo (usa il CSV più recente)
python comparador.py

# Comparativo con CSV specifico
python comparador.py data/processed/anas_obras_20260411_190348.csv
```

L'arricchimento OpenCUP non ha un entry point CLI standalone — si invoca via `enriquecedor.enriquecer_obras(cups, progress_callback)` da codice (o via Streamlit).

---

## Fonti dati e rate limit

| Fonte | URL | Auth | Rate limit applicato |
|---|---|---|---|
| ANAS Lavori in Corso | `stradeanas.it/it/anas_lavori_in_corso/getlavori` | No | 0.25s tra chiamate, 3 worker paralleli |
| OpenCUP | `opencup.gov.it/progetto/-/cup/{CUP}` | No | 1.0s tra chiamate |
| Nominatim (OSM) | `nominatim.openstreetmap.org/search` | No (User-Agent obbligatorio) | 1.1s tra chiamate (Nominatim richiede max 1 req/sec) |

User-Agent Nominatim: `PAVIMOD-ANAS-Monitor/1.0 (info@pavimod.it)` — identificativo corretto come da TOS.

---

## Struttura del repository

```
Wepscrapper_Pavimod_Gorima/
│
├── app.py                  ← Frontend Streamlit (PAVIMOD)
├── scraper.py              ← Scraping ANAS
├── comparador.py           ← Merge + comparativo storico
├── enriquecedor.py         ← Arricchimento OpenCUP + Nominatim
├── _state.py               ← Stato thread-safe per progress bar
│
├── requirements.txt
├── README.md               ← questo file
├── .gitignore
├── logo.png                ← logo PAVIMOD
│
├── .streamlit/
│   └── config.toml         ← tema PAVIMOD (rosso + grigio)
│
└── data/
    ├── processed/          ← COMMITTATO: dati per far partire l'app
    │   ├── anas_obras_*.csv
    │   ├── master_avanzamento.xlsx
    │   └── blacklist.json
    ├── cache/              ← COMMITTATO: cache OpenCUP
    │   └── opencup_cache.json
    ├── raw/                ← IGNORATO: raw JSON delle scraping run
    └── runs/               ← IGNORATO: file per regione/run (debug)
```

### Cosa è committato

I file `data/processed/*` e `data/cache/*` sono **intenzionalmente versionati** così chi clona il repo ha subito l'app funzionante senza dover fare uno scraping da zero. Le directory `data/raw/` e `data/runs/` sono ignorate (sono artifact intermedi, rigenerati a ogni scraping).

---

## Credenziali

Non ci sono credenziali di API esterne da gestire — tutte le fonti sono pubbliche. L'unica "credenziale" è la password locale per il bottone di scraping dell'app Streamlit, che serve solo a evitare trigger accidentali.

---

## Note operative

- Quando il master è **aperto in Excel**, il comparativo e l'arricchimento falliscono con `PermissionError` (Windows blocca il file). Chiudi Excel prima di eseguirli. L'app mostrerà un messaggio chiaro:
  > *"Impossibile scrivere master_avanzamento.xlsx: il file è aperto in Excel. Chiudilo e riprova."*
- Lo scraper mantiene solo gli **ultimi 5 CSV** in `data/processed/` (pulizia automatica via `limpiar_csvs_antiguos`).
- La colonna `Differenza` considera la penultima e l'ultima colonna `Avanz_*` ordinate per timestamp. Quindi anche esecuzioni molto ravvicinate nel tempo restano confrontabili.

---

## Licenza

Codice proprietario PAVIMOD. Scraper per uso interno / monitoraggio istituzionale.
