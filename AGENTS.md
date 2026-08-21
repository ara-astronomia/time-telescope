# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Comandi

```bash
# Sviluppo locale — le dipendenze sono gestite con uv (Python 3.14, letto da .python-version)
uv sync                             # crea .venv e installa dal lockfile
uv run uvicorn main:app --reload --port 8010

# Da un altro dispositivo sulla LAN (senza Nginx/Authelia davanti serve la modalità dev)
AUTH_MODE=dev uv run uvicorn main:app --reload --host 0.0.0.0 --port 8010

# Test (pytest + pytest-playwright su Chromium per i test di frontend)
uv run pytest -v --tb=short
uv run pytest tests/test_calendar.py -v        # un solo file
uv run playwright install --with-deps chromium # richiesto una volta per i test *_frontend_*

# Deploy
docker compose up -d --build
docker compose logs -f
docker compose exec telescope_time sqlite3 /data/telescope_time.db

# Dati di esempio (dopo il primo avvio, che crea le tabelle)
sqlite3 telescope_time.db < seed.sql
```

Pagine: `http://localhost:8010/request.html` (e `dashboard.html`, `calendar.html`). Swagger su `/docs`. La root `/` risponde 404: non c'è un `index.html`.

**Va lanciato dalla root del repo**: `main.py` monta `StaticFiles(directory="static")` con path assoluto derivato da `__file__`, e il DB di default è `./telescope_time.db`.

CI (`.github/workflows/`): `tests.yml` esegue `pytest` a ogni push/PR su `main`; `docker-build.yml` builda e pubblica l'immagine su push a `main`/tag `v*` o su PR con label `build-docker`.

## Architettura

Servizio FastAPI monolitico (2 file Python + 3 pagine HTML) per la gestione delle richieste di tempo telescopio del CRaC, esposto su `time_telescope.ara.roma.it` via Nginx (con Authelia in ForwardAuth) → container su :8010.

- `main.py` — app FastAPI. `init_db()` gira nel `lifespan`, non all'import. Include `router` e poi monta `static/` su `/` con `html=True`: l'ordine conta, il mount su `/` è catch-all e va lasciato per ultimo.
- `router.py` — tutto il resto: config da env, schema SQLite, autenticazione/anagrafica, modelli Pydantic, invio email, endpoint sotto il prefix `/telescope-time`.
- `static/*.html` (`request.html`, `dashboard.html`, `calendar.html`) — tre pagine autonome (HTML+CSS+JS inline, nessun build step, nessuna dipendenza esterna) che parlano con l'API via `const API_BASE = '/telescope-time'`. Modificare un endpoint significa aggiornare a mano il `fetch` nella pagina corrispondente.

### Autenticazione

Nessun login applicativo: l'identità arriva dagli header che Nginx riceve da Authelia (`Remote-User`, `Remote-Groups`, `Remote-Email`, `Remote-Name`), letti in `current_user()`. Il gruppo che vale come responsabile è configurabile (`REVIEWERS_GROUP`, default `telescope-responsabili`); `reviewers_only()` protegge gli endpoint di decisione.

`AUTH_MODE=dev` (default `forward-auth`) sintetizza quegli header quando manca Authelia — usato in sviluppo locale e nel `docker-compose.yml` di default. Gli header sono attendibili solo perché il container non deve mai essere raggiunto scavalcando Nginx.

Ogni richiesta autenticata viene conciliata con la tabella `users` da `register_user()` (upsert su `username`, con fallback su `email` per i co-osservatori inseriti a mano, vedi #40): questo è ciò che dà a `TimeRequestOut.requester_id`, non un parametro del body — un campo `observer` inviato nel POST viene ignorato.

### Persistenza

SQLite via `sqlite3` diretto (nessun ORM, WAL journaling), tre tabelle: `research_programs` (`name` UNIQUE), `users` (anagrafica, `username`/`email` UNIQUE ma nullable), `time_requests` (FK su `research_programs` e `users`) più `decision_log` (log di decisioni e spostamenti, un solo tipo di evento per riga con colonne dell'altro tipo a NULL). Connessione per-request tramite `Depends(get_db)` con `row_factory = sqlite3.Row`; le funzioni restituiscono `dict(row)`.

`telescope_time.db` non è versionato: parte vuoto, si popola con `seed.sql`. In locale il default è `./telescope_time.db`; in Docker `TELESCOPE_DB_PATH=/data/telescope_time.db` sul volume `telescope_db`.

Le scritture concorrenti su `time_requests` (approvazione, spostamento) aprono `BEGIN IMMEDIATE` prima di leggere lo stato: senza questo, due decisioni simultanee attraverserebbero entrambe la finestra fra controllo di conflitto e `UPDATE`.

### Modello di dominio

Un *research program* è un progetto osservativo riusabile; una *richiesta* (`time_requests`) prenota un **time slot** (`start`/`end`, `NaiveDatetime` — ora locale dell'osservatorio, mai con fuso) per un programma, entro una singola *notte*. La notte (`requested_night`) è derivata da `start` con soglia alle 12:00 (`night_of()` / `NIGHT_THRESHOLD`): le ore piccole appartengono alla notte precedente. Il modello `TimeSlot` valida che `end` sia dopo `start` e non scavalchi la notte (non oltre le 12:00 del giorno dopo) — client e server applicano lo stesso vincolo, ma solo il server è il presidio reale.

Stati richiesta: `pending` (default) → `approved` | `rejected`, via `PATCH /telescope-time/requests/{id}` (`reviewers_only`). Un cambio di stato o uno spostamento orario (`PATCH .../schedule`) registra un evento in `decision_log`, esposto via `GET .../history`. Chi ha creato la richiesta può spostarla solo finché è `pending` e solo verso il futuro; il responsabile (`user.is_reviewer`) la sposta senza restrizioni, anche a posteriori.

`GET /calendar` deriva lo `night_status` dalle richieste `approved`/`pending` che occupano ciascuna notte: almeno una `approved` → `booked`; sovrapposizione di fasce fra sole `pending` → `contested`; nessuna richiesta → la notte non compare (il frontend la tratta come libera). Le `rejected` sono escluse. Il vincolo di unicità è applicativo: si blocca una seconda richiesta per lo **stesso** research program nella stessa notte (`409`), mentre programmi diversi possono condividere la notte purché le fasce non si sovrappongano (controllato solo all'approvazione, in `time_slot_conflict`).

### Email

`send_notification_email` / `send_outcome_email` / `send_reschedule_email` in `router.py` sono sincrone e chiamate inline nell'handler: un SMTP lento rallenta la risposta HTTP. Senza `SMTP_HOST`/`SMTP_USER` fanno solo `print`, che è la configurazione di default. Le eccezioni SMTP sono catturate e loggate, mai propagate. `send_outcome_email` invia sia all'osservatore (se ha un'email in anagrafica) sia a `REVIEWER_EMAIL`.

## Convenzioni

Dalla PR #66 (issue #38) codice, commenti, docstring e identificatori (schema, endpoint, funzioni, nomi di file HTML/test) sono in **inglese**. Restano in **italiano** i messaggi rivolti all'utente: dettagli d'errore HTTP (`detail=...`), testo e commenti della UI nelle pagine `static/*.html`, e i commenti nei file di infrastruttura (`docker-compose.yml`, `nginx_time_telescope.conf`). Non mescolare le due cose: un identificatore in italiano o un messaggio d'errore in inglese rompono questa convenzione.
