# Guida di manutenzione — PAVIMOD ANAS Monitor

Questo documento descrive **cosa controllare periodicamente**, **come aggiornare in sicurezza il codice**, **come recuperare da incidenti** e **cosa tenere d'occhio nel tempo** per mantenere l'applicazione in buono stato di funzionamento.

L'app è deployata in modalità `free` su Render (https://render.com) con persistenza dello stato tramite commit automatici su questo stesso repository GitHub. Il deploy si rigenera ad ogni push su `main` e il keep-alive è gestito da un cron esterno su [cron-job.org](https://cron-job.org).

**Indice**

- [Sezione 1 — Schema dei controlli periodici](#sezione-1--schema-dei-controlli-periodici)
- [Sezione 2 — Flusso di release (update flow)](#sezione-2--flusso-di-release-update-flow)
- [Sezione 3 — Rollback procedure](#sezione-3--rollback-procedure)
- [Sezione 4 — Incident runbook](#sezione-4--incident-runbook)
- [Sezione 5 — Sicurezza e gestione dei secrets](#sezione-5--sicurezza-e-gestione-dei-secrets)
- [Sezione 6 — Upgrade paths](#sezione-6--upgrade-paths)
- [Sezione 7 — Backup e disaster recovery](#sezione-7--backup-e-disaster-recovery)
- [Sezione 8 — Raccomandazioni future (non implementate)](#sezione-8--raccomandazioni-future-non-implementate)

---

## Sezione 1 — Schema dei controlli periodici

La manutenzione richiede pochi controlli ben distribuiti nel tempo. Questa tabella è la cosa più importante di tutto il documento: stampala o tienila bookmarkata.

| Frequenza | Controllo | Dove verificare | Azione se fallisce |
|---|---|---|---|
| **Continuo** (passivo) | Keep-alive cron-job.org attivo e funzionante | Dashboard [cron-job.org](https://cron-job.org) → sezione Cronjobs → "Pavimod Keep Alive" | Se lo status è `failed`: verificare che l'URL Render nel cronjob sia corretto (dominio cambia se ricrei il servizio), verificare che Render non sia in manutenzione, ricreare il cronjob se necessario. |
| **Mensile** | Ore consumate su Render (cap piano free: 750 ore/mese) | [Dashboard Render](https://dashboard.render.com) → tuo servizio → tab "Metrics" → "Uptime" | Se > 700 ore verso fine mese: ridurre la frequenza del keep-alive da 10 min a 15 o 20 min, o accettare un cold start occasionale. |
| **Mensile** | ANAS rispondere correttamente | Apri [https://www.stradeanas.it/it/lavori-in-corso](https://www.stradeanas.it/it/lavori-in-corso) nel browser. Se si vede la mappa normale, OK. | Se la pagina ha cambiato struttura o il JSON endpoint di `/getlavori` non risponde più: ispezionare e adattare `_get_json()` in [`scraper.py`](scraper.py) riga ~86. |
| **Mensile** | OpenCUP rispondere correttamente | Apri [https://opencup.gov.it/progetto/-/cup/F87H17000190001](https://opencup.gov.it/progetto/-/cup/F87H17000190001) (un CUP qualsiasi). Devi vedere la pagina "Dettaglio Progetto" con tutti i campi (Anno decisione, Provincia, Comune, ecc.). | Se il layout è cambiato: ispezionare `scrape_opencup()` in [`scraper.py`](scraper.py) righe 214-269, aggiornare le etichette testuali cercate. |
| **Ogni sessione di lavoro** | Dopo aver cliccato `💾 Salva su GitHub`, verificare che il commit esista davvero | [commits/main](https://github.com/DarioUrrutia/Wepscrapper_Pavimod_Gorima/commits/main) → dovrebbe esserci un commit recente con messaggio `"Update master state — YYYY-MM-DD HH:MM"`. | Se non appare: la notifica in UI dovrebbe mostrare un errore. Se invece dice "success" ma il commit non c'è, problema nella cache di GitHub — refresha la pagina dei commit. Se persiste: aprire la dashboard Render → Logs e cercare `[GITHUB_SYNC]` per capire l'errore. |
| **Semestrale** | Dimensione del `master_avanzamento.xlsx` e numero di colonne `Avanz_*` | Scarica il master localmente (dal repo GitHub) oppure controlla nei log Render. Apri in Excel e conta le colonne che iniziano con `Avanz_`. | Se > 50 colonne (~1 anno di scraping bi-settimanale): considerare archiviazione manuale — creare un secondo file `master_avanzamento_archivio_2026.xlsx` con le colonne più vecchie e tenere nel master attivo solo le ultime 26. |
| **Semestrale** | Crescita di `opencup_cache.json` | Aprire il file nel repo GitHub o localmente. Se > 1-2 MB è molto grande. | Nella maggior parte dei casi non serve pulire nulla. Se si sospetta che i dati OpenCUP siano obsoleti per qualche progetto specifico, svuotare manualmente la cache: eliminare `data/cache/opencup_cache.json` in locale e fare un commit vuoto, oppure eliminarlo via UI GitHub. Al prossimo arricchimento verrà ricreata. |
| **Annuale** (critico) | Scadenza del Personal Access Token GitHub (PAT) | Nel tuo password manager, appuntare la data di creazione. Il PAT fine-grained scade dopo **12 mesi** esatti dalla creazione. | **Un mese prima della scadenza**: creare un nuovo PAT seguendo le istruzioni in [README.md](README.md#passo-1--crea-un-personal-access-token-pat-su-github), aggiornare la env var `GITHUB_TOKEN` nella [dashboard Render](https://dashboard.render.com) → Settings → Environment, Render farà un rebuild automatico (~2 min). Testare subito il bottone `💾 Salva su GitHub` per verifica. |
| **Annuale** | Controllo visivo completo dell'app | Apri l'URL Render e fai il giro completo delle feature: scraping, comparativo, arricchimento OpenCUP, blacklist, salvataggio GitHub. | Se qualcosa non va: consultare la [Sezione 4 — Incident runbook](#sezione-4--incident-runbook). |

### Lettura dei log di Render

Quando devi controllare il comportamento dell'app in produzione:

1. Apri [dashboard.render.com](https://dashboard.render.com)
2. Click sul servizio `pavimod-anas-monitor` (o il nome che gli hai dato)
3. Tab **"Logs"** → vedi gli ultimi 100 log in real-time
4. Cerca pattern chiave:
   - `[ENRICH]` → log dell'arricchimento OpenCUP
   - `[COMPARADOR]` → log del comparativo
   - `[NOMINATIM OK/KO]` → log del geocoding
   - `[GITHUB_SYNC]` → log del salvataggio su GitHub
   - `Exception`, `Error`, `Traceback` → errori bloccanti

Puoi anche scaricare i log con il bottone **"Download Logs"** se servono per analisi più lunghe.

---

## Sezione 2 — Flusso di release (update flow)

Questa è la procedura standard per modificare il codice e rilasciare in produzione in sicurezza.

### Prerequisiti una tantum (setup locale)

Se stai lavorando su una macchina nuova:

```bash
# Clona il repo
git clone https://github.com/DarioUrrutia/Wepscrapper_Pavimod_Gorima.git
cd Wepscrapper_Pavimod_Gorima

# Crea il virtual environment
python -m venv .venv
source .venv/Scripts/activate    # Windows Git Bash
# oppure .venv\Scripts\activate  # Windows PowerShell/CMD
# oppure source .venv/bin/activate   # macOS/Linux

# Installa le dipendenze
pip install -r requirements.txt

# Test che l'app parta
streamlit run app.py
```

Se `streamlit run app.py` apre il browser su `http://localhost:8501` e vedi l'interfaccia PAVIMOD, sei pronto.

### Flusso normale di modifica

Ogni volta che vuoi cambiare qualcosa nel codice:

```bash
# 1. Pull delle ultime modifiche (importante se lavori da più macchine)
git pull origin main

# 2. Attiva il venv
source .venv/Scripts/activate

# 3. Modifica i file che ti servono (con editor a scelta)
# ...edit...

# 4. Test locale
streamlit run app.py
# Apri http://localhost:8501 e verifica manualmente:
#   - La pagina carica
#   - I filtri funzionano
#   - Il bottone modificato (se ne hai toccato uno) si comporta come previsto
#   - Genera Comparativo / Arricchimento / Salva GitHub: prova quelli rilevanti
# Ctrl+C per fermare Streamlit

# 5. Verifica cosa stai committando
git status
git diff

# 6. Aggiungi solo i file modificati (NO git add .)
git add app.py scraper.py  # per esempio

# 7. Commit con messaggio chiaro in italiano
git commit -m "Fix bug XYZ: descrizione breve del perché"

# 8. Push su GitHub
git push origin main
```

### Deploy automatico

Dopo il `git push`:

1. Render rileva il push entro ~30 secondi
2. Inizia un nuovo build (vedi tab "Events" nel dashboard)
3. La build dura ~2-3 minuti (dipendenze sono in cache, quindi più veloce del primo deploy)
4. Quando lo status torna verde **Live**, l'app è aggiornata con il nuovo codice

**Mentre la build è in corso**, l'app continua a servire la versione precedente. Non c'è downtime.

### Verifica post-deploy

Apri l'URL pubblico dell'app e testa la modifica che hai appena fatto. Se funziona → tutto ok, hai finito. Se qualcosa è rotto → vai alla [Sezione 3 — Rollback procedure](#sezione-3--rollback-procedure).

### Buone pratiche

- **Un commit per ogni modifica logica**: non mischiare bug fix e nuove feature nello stesso commit
- **Test locale prima di push**: ogni push innesca un deploy pubblico, quindi non pushare roba rotta
- **Messaggi di commit chiari**: `"Fix: avanzamento non aggiornato quando ..."` è meglio di `"update"`
- **Salva su GitHub lo stato prima di modifiche rischiose**: se stai per toccare il comparator, clicca prima `💾 Salva su GitHub` dall'app così hai un backup dello stato, nel caso qualcosa si rompa

---

## Sezione 3 — Rollback procedure

Se dopo un deploy l'app si comporta male (errori visibili, feature rotte, build fallita), hai due strade.

### Metodo 1 — Git revert (pulito, mantiene la storia)

Raccomandato quando hai tempo per una soluzione corretta e vuoi che la storia git resti tracciabile.

```bash
# Individua il commit rotto
git log --oneline -5

# Crea un commit inverso che annulla l'ultimo commit
git revert HEAD

# Push → Render ridispiega con il codice del commit precedente
git push origin main
```

Il revert crea un NUOVO commit che annulla le modifiche del precedente. Lo storico è trasparente: vedi sia il commit originale che il revert.

Tempo totale: ~3 minuti (git operations + Render rebuild).

### Metodo 2 — Rollback da dashboard Render (rapido, non tocca git)

Raccomandato per emergenze quando vuoi ripristinare l'app in meno di 30 secondi senza toccare il codice.

1. Apri [dashboard.render.com](https://dashboard.render.com) → tuo servizio
2. Tab **"Deploys"**
3. Scorri i deploy fino a trovare uno con status **"Live"** e **verde** (un deploy che sai essere funzionante)
4. Click sui tre puntini `⋯` accanto al deploy → **"Rollback to this deploy"**
5. Conferma

Render ripristina immediatamente quel deploy. La versione del codice su GitHub **NON cambia** — questo è importante: significa che se poi fai un altro `git push`, Render tornerà a buildare il codice attuale del repo. Il rollback è una "ripresa" momentanea di una versione precedente, non una modifica del codice.

Tempo totale: ~20-30 secondi.

### Quando usare quale

| Situazione | Metodo consigliato |
|---|---|
| Build failed, app non parte | Metodo 2 (Render rollback) subito per ripristinare, poi Metodo 1 per fix |
| App parte ma c'è un bug funzionale | Metodo 1 (git revert) |
| Sei in emergenza, utenti bloccati | Metodo 2 |
| Hai il tempo di fare le cose bene | Metodo 1 |

---

## Sezione 4 — Incident runbook

Elenco dei problemi più probabili, con causa e fix diretto.

| Sintomo | Causa probabile | Fix |
|---|---|---|
| Bottone `💾 Salva su GitHub` fallisce con `HTTP 401 Unauthorized` | Il Personal Access Token GitHub è scaduto | Rigenera un nuovo PAT su GitHub (vedi [README.md — Passo 1](README.md#passo-1--crea-un-personal-access-token-pat-su-github)), copia il valore, vai su Render Dashboard → tuo servizio → Environment, modifica `GITHUB_TOKEN` con il nuovo valore, salva. Render riavvia automaticamente (~2 min). Testa di nuovo. |
| Bottone `💾 Salva su GitHub` fallisce con `HTTP 403 Forbidden` | Il PAT ha permessi insufficienti (non ha `Contents: Write`) | Rigenera il PAT assicurandoti di selezionare **Contents: Read and Write** durante la creazione. Aggiorna la env var come sopra. |
| Bottone `💾 Salva su GitHub` fallisce con messaggio "Impossibile scrivere..." | Il file `master_avanzamento.xlsx` è aperto in Excel sulla tua macchina locale (succede solo in dev locale) | Chiudi Excel e riprova. Non succede su Render perché lì nessuno apre il file in Excel. |
| `Esegui Scraping` fallisce con "Password errata" | La password scritta nel form non corrisponde a `PAVIMOD_PASSWORD` | Se sei su Render: la password è quella che hai settato come env var `PAVIMOD_PASSWORD`. Se sei in locale: usa il default `Pavimodvai` (o quella che hai modificato in `app.py`). |
| `Esegui Scraping` restituisce 0 opere | L'API ANAS ha cambiato formato/URL | Prova ad aprire manualmente `https://www.stradeanas.it/it/lavori-in-corso` nel browser. Se la mappa funziona visivamente, il problema è più sottile: ispeziona `_get_json()` in [`scraper.py`](scraper.py) riga ~86, verifica gli header, params, struttura della response JSON, e adatta. |
| `Arricchimento OpenCUP`: tutti i campi restano vuoti | Il layout HTML di opencup.gov.it è cambiato | Apri un CUP qualsiasi nel browser (es. `https://opencup.gov.it/progetto/-/cup/F87H17000190001`). Ispeziona la pagina e guarda come sono strutturati i campi "Anno decisione", "Provincia", "Comune", ecc. Adatta `scrape_opencup()` in [`scraper.py`](scraper.py) righe 214-269: la funzione cerca etichette testuali specifiche, se sono cambiate devi aggiornarle. |
| `Genera Comparativo` fallisce con `PermissionError: master_avanzamento.xlsx` (solo locale) | Excel ha il file aperto | Chiudi Excel e riprova. |
| App risponde con cold start di 30-60 secondi | Il cron-job.org non sta pingando | Verifica sulla dashboard cron-job.org che il cronjob sia attivo e che l'ultimo ping sia riuscito (status `200 OK`). Se lo status è `failed`, controlla che l'URL nel cronjob corrisponda all'URL attuale del servizio Render (se hai ricreato il servizio, il dominio cambia). |
| Build Render fallisce con errori di dipendenze (es. `pandas x.y conflicts with...`) | Una nuova release di un pacchetto Python ha introdotto un breaking change. Le versioni non sono pinnate, quindi Render prende sempre le ultime. | **Fix immediato**: su Render, Deploys → Rollback all'ultimo deploy funzionante (vedi Sezione 3). **Fix permanente**: localmente, fai `pip freeze > requirements.txt` per pinnare le versioni ora funzionanti. Commit + push. |
| Render sospende il servizio con messaggio `>750 hours this month` | Il keep-alive è troppo aggressivo e hai raggiunto il limite mensile del piano free | Aspetta l'inizio del mese successivo (il contatore si resetta). Nel frattempo riduci la frequenza del cron-job.org da 10 min a 15 o 20 min. |
| La tabella mostra dati vecchi, non aggiorni nulla | Il browser ha cachato la pagina Streamlit | Premi `R` o `F5` nel browser per forzare un rerun. Se persiste, svuota la cache del browser o apri in incognito. |
| Dopo un `git push`, Render non fa redeploy | Il webhook GitHub → Render non è configurato | Render → Settings → Verifica che "Auto-Deploy" sia su `Yes`. Se no, attivalo. |
| Errori di encoding `UnicodeEncodeError: 'charmap'` nei log | Un `print()` con caratteri speciali fallisce su stdout cp1252 | Già gestito via `sys.stdout.reconfigure(encoding='utf-8')` in cima a [`app.py`](app.py). Se vedi ancora questo errore: verifica che la riconfigurazione sia ancora presente e non sia stata rimossa. |
| L'arricchimento di un progetto multi-tratta salta alcune righe | Ricordare che la logica è "per riga": le righe con `Nome_Ufficiale_Progetto` già riempito vengono saltate | Comportamento corretto. Se vuoi forzare il refresh di quella specifica riga, svuota manualmente il campo nel master Excel (o elimina la cache e rifai tutto). |

---

## Sezione 5 — Sicurezza e gestione dei secrets

L'app gestisce due secret critici. Vanno trattati con attenzione.

### Secret 1 — `PAVIMOD_PASSWORD`

**Cos'è**: la password che protegge i bottoni `▶ Esegui Scraping` e `💾 Salva su GitHub` nell'app Streamlit.

**Dove vive**:
- In produzione: env var `PAVIMOD_PASSWORD` nella dashboard Render (Environment)
- In locale (sviluppo): fallback hardcoded in [`app.py`](app.py) riga ~31 (`"Pavimodvai"` di default)

**Rotazione consigliata**: ogni 6-12 mesi, o subito se sospetti sia stata esposta.

**Come ruotare**:
1. Scegli una nuova password
2. Dashboard Render → Environment → modifica `PAVIMOD_PASSWORD` → salva
3. Render riavvia (~30 secondi)
4. Testa i due bottoni con la nuova password

**Nota**: la password in `app.py` come fallback è visibile nel codice sorgente pubblico su GitHub. Questo è accettato perché:
1. In produzione viene SEMPRE sovrascritta dalla env var Render
2. Il codice è un uso interno PAVIMOD, non pubblico-pubblico
3. La password protegge solo contro click accidentali, non è una barriera di sicurezza forte

Se vuoi togliere anche quella, cambia la riga 31 in `app.py` da `os.getenv("PAVIMOD_PASSWORD", "Pavimodvai")` a `os.getenv("PAVIMOD_PASSWORD", "")` — in quel caso senza env var la password è stringa vuota e l'app rifiuterà qualsiasi input non vuoto.

### Secret 2 — `GITHUB_TOKEN` (PAT)

**Cos'è**: il Personal Access Token GitHub usato da `github_sync.py` per committare sul repo. Senza questo, il bottone `💾 Salva su GitHub` non funziona.

**Dove vive**: env var `GITHUB_TOKEN` nella dashboard Render. **Non ha fallback nel codice** — intenzionalmente. Se manca, il bottone è disabilitato.

**Scope minimo richiesto**:
- **Contents**: `Read and Write`

**Scope da NON concedere mai**:
- `admin`, `workflow`, `delete_repo`, `packages`, `issues` (questi non servono per il sync)

**Scadenza**: i Fine-grained PAT hanno scadenza obbligatoria fino a **12 mesi**. Non esistono token senza scadenza per questo tipo.

**Come ruotare**:
1. Apri https://github.com/settings/tokens?type=beta
2. Se il vecchio token è ancora valido, puoi prima revocare (opzionale): click sul vecchio token → Revoke
3. Generate new token con stessi permessi del precedente (vedi [README.md — Passo 1](README.md#passo-1--crea-un-personal-access-token-pat-su-github))
4. Copia il nuovo token (sola chance)
5. Dashboard Render → Environment → modifica `GITHUB_TOKEN` → incolla → salva
6. Render riavvia (~30 secondi)
7. Testa: apri l'app, clicca `💾 Salva su GitHub` → dovrebbe funzionare

### Regole generali sui secret

- **Mai committarli nel codice**: il `.gitignore` protegge `.env`, `.env.local`. Se accidentalmente committi un secret, **rigeneralo subito** (GitHub li invalida automaticamente se rileva un PAT in un commit pubblico, ma non contarci — rigenera manualmente).
- **Mai condividerli in chat pubbliche**: Slack, email, issue tracker. Usa un password manager.
- **URL Render è semi-pubblico**: non contiene la password ma la tua app è accessibile a chi conosce l'URL. Non condividerlo apertamente.
- **Log Render**: i log possono contenere dati sensibili (Id_ANAS, nomi progetti). Sono visibili solo a chi ha accesso all'account Render. Non screenshot-arli senza rivederli prima.

---

## Sezione 6 — Upgrade paths

Procedure per aggiornamenti maggiori del sistema.

### Upgrade di Python (minore, es. 3.11 → 3.12)

Procedura testata:

1. **Locale**: crea un nuovo venv con la versione target
   ```bash
   deactivate
   rm -rf .venv
   python3.12 -m venv .venv
   source .venv/Scripts/activate
   pip install -r requirements.txt
   streamlit run app.py
   # Test completo end-to-end
   ```
2. Se tutto funziona, modifica `runtime.txt`:
   ```
   python-3.12.x
   ```
3. Modifica `render.yaml` → env var `PYTHON_VERSION` al nuovo valore
4. Commit + push
5. Verifica che la build Render passi e che l'app riparta normalmente

Se la build fallisce: rollback con il metodo 2 (Sezione 3), poi investigare i warning di deprecation che la nuova versione ha portato.

### Upgrade delle dipendenze

Le dipendenze in `requirements.txt` sono attualmente **non pinnate**. Questo significa che ogni deploy Render installa le ultime versioni disponibili — comodo, ma rischioso se una release fa breaking.

**Procedura di upgrade controllato**:

1. **Locale**: aggiorna tutto
   ```bash
   source .venv/Scripts/activate
   pip install --upgrade -r requirements.txt
   ```
2. **Test end-to-end**: scraping, comparativo, arricchimento, salva su GitHub
3. Se qualcosa è rotto: leggi il changelog del pacchetto nuovo, adatta il codice
4. Quando tutto funziona, **pinna le versioni**:
   ```bash
   pip freeze > requirements.txt
   ```
   > Questo cattura le versioni esatte del venv attuale. Il file risultante avrà righe tipo `pandas==3.0.2`.
5. Commit + push
6. La build Render installerà esattamente queste versioni, d'ora in poi deploy deterministici

**Nota**: il pinning è un **trade-off**. Pro: build riproducibili, no breaking changes inaspettati. Contro: non ricevi più automaticamente security patches minori — dovrai farle manualmente ad ogni ciclo di aggiornamento.

### Upgrade maggiore di Streamlit

Streamlit rilascia nuove versioni con una certa frequenza. Le versioni major (es. 1.x → 2.0) possono introdurre breaking changes nelle API del frontend.

Prima di aggiornare Streamlit in produzione:

1. Leggi attentamente il [release notes di Streamlit](https://docs.streamlit.io/library/changelog)
2. Cerca keyword come "breaking change", "deprecated", "removed"
3. Se vedi deprecation/rimozioni che toccano `st.dialog`, `st.data_editor`, `st.session_state`, `st.dataframe` → probabile che tu debba adattare il codice
4. Test molto approfondito in locale
5. Pin della versione nuova in `requirements.txt`
6. Push

### Migrazione a un altro hosting

Se un giorno Render non ti soddisfa più (troppi limiti free, preferenze diverse, ecc.), il codice è **portabile**:

- **Requisiti minimi dell'hosting**: Python 3.11+, capacità di installare pacchetti da requirements, env vars, disco temporaneo (~500MB), porta HTTP esposta.
- **Hosting free alternativi**:
  - **Railway** — $5/mese di credito free, simile a Render, ottimo per Streamlit
  - **Fly.io** — 3 VM free, più complesso ma potente
  - **Streamlit Community Cloud** — gratis e specifico per Streamlit, **ma** persistenza non garantita (è pensato per demo)
  - **Google Cloud Run** — free tier generoso, complesso da configurare
- **La persistenza GitHub-based funziona ovunque**: basta passare `GITHUB_TOKEN` come env var e il codice funziona senza modifiche.
- **Quello che dovrai riconfigurare**: env vars sul nuovo host, cronjob di keep-alive con l'URL nuovo (se l'host dorme), eventualmente ajustare il `startCommand` al formato del nuovo host.

---

## Sezione 7 — Backup e disaster recovery

### Il backup esiste già

**Il repository GitHub È il tuo backup**. Tutti i file di stato critici (master, blacklist, cache OpenCUP, ultimi CSV) sono committati periodicamente tramite il bottone `💾 Salva su GitHub`. Se perdi completamente l'accesso a Render, puoi:

1. Clonare il repo su una nuova macchina o nuovo hosting
2. Configurare le env vars (`GITHUB_TOKEN`, `PAVIMOD_PASSWORD`)
3. Far partire `streamlit run app.py`
4. Tutto lo stato è già lì

**Zero data loss**, tranne al massimo il lavoro dell'ultima sessione se non avevi cliccato `💾 Salva` prima della catastrofe.

### Backup locale manuale (raccomandato annuale)

Ogni 12 mesi, fai un clone completo su disco esterno o storage locale:

```bash
# Clone "mirror" → include TUTTA la storia, branch, tag
git clone --mirror https://github.com/DarioUrrutia/Wepscrapper_Pavimod_Gorima.git pavimod-backup-2026.git

# Archivia lo ZIP su USB/cloud personale
tar -czf pavimod-backup-2026.tar.gz pavimod-backup-2026.git
```

Questo è un safety net contro eventi estremi (account GitHub sospeso, repo eliminato per errore, ecc.).

### Export emergenza del master

Se serve un export immediato dei dati senza passare da GitHub o Render:

1. Apri l'app
2. Applica eventuali filtri che ti interessano (o nessuno per avere tutto)
3. Click `⬇ Scarica dati filtrati (CSV)`
4. Il file `.csv` scaricato contiene tutte le colonne del master filtrato

Puoi farlo in qualsiasi momento, anche senza essere autenticato con password (il download è libero). È il fallback più semplice per avere una copia locale dei dati.

### Disaster recovery checklist

Se per qualsiasi motivo l'app è inaccessibile e ti serve ripristinarla in urgenza:

1. **Prima cosa**: verifica se è un problema Render (dashboard), cron-job.org, o GitHub (status page)
2. **Se Render è down**: aspetta. Solitamente tornano su in minuti/ore. Non fare nulla di affrettato.
3. **Se Render richiede redeploy**: Settings → Manual Deploy → "Clear build cache & deploy"
4. **Se il servizio Render è sparito del tutto** (es. sospeso per limite free): crealo nuovo, procedura in [README.md — Deploy su Render](README.md#deploy-su-render-free-con-persistenza-via-github). Configura le env vars, aggiorna l'URL in cron-job.org.
5. **Se GitHub è down**: il servizio Render continua a funzionare (ha i dati in filesystem locale). Puoi fare scraping e lavorare, ma non salvare. Quando GitHub torna, clicca `💾 Salva su GitHub`.
6. **Se il repo è stato eliminato**: hai il backup locale? Usa quello per ricreare il repo. Se no, procurati lo stato più recente possibile dai tuoi download CSV e dai log Render se ancora accessibili.

---

## Sezione 8 — Raccomandazioni future (non implementate)

Durante l'audit di manutenzione sono stati identificati alcuni miglioramenti opzionali che riducono rischi latenti ma **non sono stati implementati** in questa iterazione. Considera di applicarli in futuro quando avrai tempo:

### 1. Pinning di `requirements.txt`

**Problema**: il file attuale elenca i pacchetti senza versione (`pandas`, `streamlit`, ecc.). Ad ogni rebuild Render, vengono installate le ultime versioni disponibili. Un breaking change in una release può rompere l'app silenziosamente.

**Fix (5 minuti)**:
```bash
source .venv/Scripts/activate
pip freeze > requirements.txt
git add requirements.txt
git commit -m "Pin dependency versions for reproducible builds"
git push
```

**Risultato**: il file conterrà righe tipo `pandas==3.0.2`, `streamlit==1.56.0`. Build riproducibili garantite.

### 2. Reminder in-app di scadenza PAT GitHub

**Problema**: il PAT scade dopo 12 mesi senza alcun warning visivo. Ti accorgi solo quando il bottone `💾 Salva su GitHub` fallisce con `401`.

**Fix (15-20 minuti di codice)**:
- Aggiungere un file `data/.pat_created` con la data di creazione del token (input manuale)
- In [`github_sync.py`](github_sync.py), aggiungere una funzione `days_until_pat_expires() -> int | None` che legge il file e calcola i giorni residui assumendo 365 gg di TTL
- In [`app.py`](app.py), sopra il bottone `💾 Salva su GitHub`, mostrare un warning giallo se `days_until_pat_expires() < 30`

### 3. TTL sulla cache OpenCUP

**Problema**: `opencup_cache.json` cresce indefinitamente e non scade mai. Se i dati OpenCUP di un progetto cambiano (raro ma possibile), la cache restituisce il valore obsoleto per sempre.

**Fix**: aggiungere un timestamp per ogni entry della cache e invalidare dopo N mesi. ~10 righe in `enriquecedor.py`.

### 4. Rotation automatica delle colonne `Avanz_*`

**Problema**: ogni comparativo aggiunge una colonna `Avanz_DD-MM-YYYY_HHmm` al master. Dopo anni, il master diventa un file con decine di colonne storiche, pesante da aprire in Excel.

**Fix**: creare un file `master_avanzamento_archivio.xlsx` dove spostare automaticamente le colonne `Avanz_*` più vecchie di X mesi, mantenendo nel master attivo solo le ultime ~26 (circa 1 anno). Procedura da chiamare dal comparator al termine del merge.

### 5. Suite di test minima

**Problema**: nessun test automatizzato. Le regressioni si scoprono solo manualmente.

**Fix**: creare una cartella `tests/` con almeno:
- `test_matching.py` — verifica `row_signature()` e `is_row_blacklisted()` in [`comparador.py`](comparador.py)
- `test_github_sync.py` — verifica `commit_files_to_github()` con GitHub API mockata
- `test_enrichment.py` — verifica `_val_non_vuoto()`, `_primo_comune()`, `_coord_vuota()` in [`enriquecedor.py`](enriquecedor.py)

Con `pytest` come runner. Da eseguire prima di ogni push.

### Priorità di implementazione

Se dovessi sceglierne una, la più urgente è **il pinning di requirements.txt** (#1): costa 5 minuti, elimina una classe intera di rischi, zero complessità aggiuntiva nel codice.

Seguita da **#2 reminder PAT**: evita l'unica incidente "programmato" e inevitabile del sistema (la scadenza annuale).

Le altre (TTL cache, rotation Avanz_*, test) sono "nice to have" a lungo termine.

---

## In sintesi

| Operazione | Frequenza | Tempo stimato |
|---|---|---|
| Verificare cron-job.org | Quando sospetti problemi | 1 min |
| Controllare ore Render | Mensile | 1 min |
| Test manuale siti sorgente | Mensile | 2 min |
| Salvare su GitHub dopo ogni sessione di lavoro | Ogni uso dell'app | 10 sec |
| Rinnovo PAT GitHub | Annuale (obbligatorio) | 10 min |
| Upgrade dipendenze (consapevole) | Semestrale | 30 min |
| Backup `git clone --mirror` su disco | Annuale | 5 min |

**Tempo totale di manutenzione stimato: ~30 min/mese + 1-2 ore/anno per il rinnovo del PAT e gli upgrade semestrali.**

Per ogni dubbio su come reagire a un incidente, questa guida è il primo posto dove guardare. Se trovi un problema non documentato qui, aggiungilo alla [Sezione 4 — Incident runbook](#sezione-4--incident-runbook) così non perdi il sapere.

---

**Ultima revisione**: 2026-04-11
**Maintainer**: DarioUrrutia
**Repo**: https://github.com/DarioUrrutia/Wepscrapper_Pavimod_Gorima
