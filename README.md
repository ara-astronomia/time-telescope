# Telescope Time — CRaC Module

**Languages:** [English](#english) · [Italiano](#italiano)

---

<a id="english"></a>
## English

Service for managing telescope time requests.
Served at `time_telescope.ara.roma.it`.

### Structure

```
time-telescope/
├── main.py                         ← FastAPI app
├── router.py                       ← API endpoints
├── pyproject.toml                  ← dependencies (uv)
├── uv.lock                         ← locked versions
├── .python-version                 ← required interpreter
├── seed.sql                        ← sample data
├── tests/                          ← pytest suite
├── Dockerfile
├── docker-compose.yml
├── nginx_time_telescope.conf       ← Nginx block to copy
├── static/                         ← HTML pages
│   ├── request.html
│   ├── dashboard.html
│   └── calendar.html
└── README.md
```

> The three HTML pages live in the `static/` subfolder.

---

### Local install (dev)

Dependencies are managed with [uv](https://docs.astral.sh/uv/), which also
downloads the interpreter listed in `.python-version` (Python 3.14): no
separate install needed.

```bash
cd time-telescope

uv sync                             # creates .venv and installs from the lockfile
uv run uvicorn main:app --reload --port 8010
```

To add a dependency — `pyproject.toml` and `uv.lock` stay in sync on their
own:

```bash
uv add <package>
uv add --dev <package>              # test-only
```

Open in your browser:
- http://localhost:8010/request.html
- http://localhost:8010/dashboard.html
- http://localhost:8010/calendar.html

> The root `/` returns `404`: there's no `index.html`, pages must be called
> by name.

The SQLite database is created at `./telescope_time.db` if
`TELESCOPE_DB_PATH` isn't set.

#### Reaching it from the LAN

By default uvicorn listens only on localhost. To try it from another
device — a tablet in the dome, a phone — you need `--host 0.0.0.0`:

```bash
AUTH_MODE=dev uv run uvicorn main:app --reload --host 0.0.0.0 --port 8010
```

Then `http://<machine-ip>:8010/request.html`.
Requires `AUTH_MODE=dev`: without Nginx and Authelia in front, every request
without an identity header gets `401`.

If the port doesn't respond from outside while it does locally, it's the
firewall:

```bash
sudo ufw allow 8010/tcp
```

#### Sample data

The database isn't versioned: it starts empty. To seed it with a few sample
research programs and requests — dates relative to today, so the calendar
always shows a booked day and a contested one:

```bash
sqlite3 telescope_time.db < seed.sql
```

Tables are created by the app on first startup, so the seed must be applied
after starting it at least once. If `sqlite3` isn't installed:

```bash
uv run python -c "import sqlite3; d=sqlite3.connect('telescope_time.db'); \
  d.executescript(open('seed.sql').read()); d.commit()"
```

---

### Tests

```bash
uv run pytest                              # the whole suite
uv run pytest tests/test_calendar.py       # a single file
uv run pytest -k rejected                  # a single test by name
```

`uv run` installs whatever's missing on its own, including the `dev` group's
dependencies: no separate install step needed.

Every test runs on a fresh temporary database: the suite never touches
`telescope_time.db`.

---

### Docker deploy

```bash
cd telescope_time
docker compose up -d --build

# Logs
docker compose logs -f

# DB inspection — the image doesn't include sqlite3, use Python instead
docker compose exec telescope_time python -c \
  "import sqlite3; d=sqlite3.connect('/data/telescope_time.db'); \
   print(d.execute('SELECT id, requested_night, start, end, status FROM time_requests').fetchall())"

# Sample data
docker compose exec -T telescope_time python -c \
  "import sqlite3,sys; d=sqlite3.connect('/data/telescope_time.db'); \
   d.executescript(sys.stdin.read()); d.commit()" < seed.sql
```

The database persists in the `telescope_db` Docker volume.

---

### Nginx configuration

```bash
cp nginx_time_telescope.conf /etc/nginx/sites-available/time_telescope
ln -s /etc/nginx/sites-available/time_telescope /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

Update the SSL paths in the .conf to match the other *.ara.roma.it services.

---

### Environment variables

| Variable             | Default                        | Description              |
|-----------------------|--------------------------------|--------------------------|
| TELESCOPE_DB_PATH    | ./telescope_time.db            | Database path            |
| SMTP_HOST            | (disabled)                     | SMTP server               |
| SMTP_PORT            | 587                             | SMTP port                  |
| SMTP_USER            |                                 | SMTP user                  |
| SMTP_PASSWORD        |                                 | SMTP password               |
| EMAIL_FROM           | crac@osservatorio.it           | Email sender               |
| REVIEWER_EMAIL       | responsabile@osservatorio.it   | Notification recipient     |
| TZ                   | (system timezone)              | Observatory timezone: time slots are local time |
| AUTH_MODE            | forward-auth                   | `forward-auth` or `dev`   |
| DEV_USER             | sviluppo                       | Simulated user (AUTH_MODE=dev only) |
| DEV_GROUPS           | telescope-responsabili         | Simulated groups (AUTH_MODE=dev only) |
| REVIEWERS_GROUP      | telescope-responsabili         | Group allowed to approve  |

---

### Authentication

Users are Authelia's: the service doesn't handle login, sessions, or
passwords. Nginx verifies the session (`auth_request`) and passes the
identity to the app in the `Remote-User`, `Remote-Groups` and
`Remote-Email` headers — see `nginx_time_telescope.conf`.

Approving or rejecting a request requires membership in the
`REVIEWERS_GROUP` group; the other endpoints are open to anyone
authenticated.

> Headers are trustworthy **only** if the container isn't reachable by
> bypassing Nginx. Port 8010 must not be exposed externally.

#### In development

`AUTH_MODE=dev` synthesizes those headers, so an Authelia instance isn't
needed:

```bash
AUTH_MODE=dev uv run uvicorn main:app --reload --port 8010
```

You're authenticated as `DEV_USER` with `DEV_GROUPS`'s groups. To try a
different user — for example a member who can't approve — just pass
headers, no restart needed:

```bash
curl -X PATCH localhost:8010/telescope-time/requests/1 \
  -H 'Remote-User: mario' -H 'Remote-Groups: soci' \
  -H 'Content-Type: application/json' -d '{"status":"approved"}'
# → 403
```

`GET /telescope-time/me` returns the identity currently in use.

---

### API endpoints

| Method | Path                             | Description                       |
|--------|-----------------------------------|------------------------------------|
| GET    | /telescope-time/research-programs         | List research programs            |
| POST   | /telescope-time/research-programs         | Create a research program (unique name) |
| GET    | /telescope-time/research-programs/{id}    | Research program detail           |
| GET    | /telescope-time/requests        | List requests (filterable)        |
| POST   | /telescope-time/requests        | Submit a request                  |
| GET    | /telescope-time/requests/{id}   | Request detail                    |
| PATCH  | /telescope-time/requests/{id}   | Approve / reject                  |
| PATCH  | /telescope-time/requests/{id}/schedule | Reschedule date and times (reviewers) |
| GET    | /telescope-time/calendar        | Monthly calendar (?year=&month=)  |
| GET    | /telescope-time/requests/{id}/history | Decisions and reschedules on the request |
| GET    | /telescope-time/me               | Identity of the connected user    |
| GET    | /telescope-time/statistics      | Aggregate statistics              |

Interactive docs: /docs (Swagger UI)

---

### Time slots

A request occupies a range, not a day: `start` and `end` are two full
instants (`2026-09-12T22:00:00`), so a session crossing midnight needs no
special cases.

`requested_night` remains the **reference night** and is the date of
`start`: a session starting on the 12th at 23:00 belongs to the night of the
12th even if it ends on the 13th. That's what the calendar groups on.

Instants are **observatory local time**, with no timezone: a value with an
offset is rejected with `422`, because it would make stored slots no longer
comparable to each other. Hence the `TZ` variable in the compose file — the
server compares the requested start against its own clock to reject
observations in the past.

Two requests can contest the same slot while pending: that's normal, and
it's what the calendar calls `contested`. The constraint kicks in on
approval.

---

### Operational flow

1. The observer opens request.html: their name is already known, it
   comes from Authelia and isn't typed
2. They select an existing research program or create a new one
3. They enter co-observers and the time slot — from when to when, not just
   which night — → submit
4. The reviewer receives an email and opens dashboard.html
5. They approve or reject with optional notes
6. The calendar reflects the status of the nights in real time:

   | Night's situation                              | night_status |
   |--------------------------------------------------|--------------|
   | no requests                                      | `free` (the day doesn't appear in the map) |
   | pending requests that don't overlap               | `pending`    |
   | two or more pending with overlapping slots         | `contested`  |
   | at least one approved                              | `booked`     |

7. The telescope can host several programs in the same night: nights with
   more than one approved observation are flagged in the grid. What two
   programs can't share is the same instant: approving a request whose slot
   intersects an already-approved one returns `409`, naming the conflicting
   request
8. The reviewer can reschedule a request, in any status, instead of
   rejecting it: that's how a contested slot gets resolved, or a night lost
   to weather or maintenance gets recovered. Rescheduling is allowed to any
   date, including the past, because it's also used to log an observation
   that already happened; if the date is in the past, the interface states
   so before confirming. A rejected request can be rescheduled too:
   recovering it means rescheduling it first and then re-approving it, not
   the other way around — re-approving first would leave it on the original
   slot, which may in the meantime be taken
9. A decision can be reversed: on an approved request the dashboard offers
   rejection and vice versa, never the command that would leave it
   unchanged. The observer gets a second notice, and the confirmation says so
10. Decisions and reschedules are tracked: both end up in `decision_log`,
    told apart by `type`, with who and when, and that's what the dashboard
    shows when opening a request's detail
11. The outcome is emailed to whoever made the request, at the address
    Authelia provides; if it's missing, the notice goes to the reviewer

---

<a id="italiano"></a>
## Italiano

Servizio per la gestione delle richieste di tempo telescopio.
Accessibile su `time_telescope.ara.roma.it`.

### Struttura

```
time-telescope/
├── main.py                         ← FastAPI app
├── router.py                       ← endpoint API
├── pyproject.toml                  ← dipendenze (uv)
├── uv.lock                         ← versioni bloccate
├── .python-version                 ← interprete richiesto
├── seed.sql                        ← dati di esempio
├── tests/                          ← suite pytest
├── Dockerfile
├── docker-compose.yml
├── nginx_time_telescope.conf       ← blocco Nginx da copiare
├── static/                         ← pagine HTML
│   ├── request.html
│   ├── dashboard.html
│   └── calendar.html
└── README.md
```

> Le tre pagine HTML vanno nella sottocartella `static/`.

---

### Installazione locale (test)

Le dipendenze sono gestite con [uv](https://docs.astral.sh/uv/), che si
occupa anche di scaricare l'interprete indicato in `.python-version`
(Python 3.14): non serve installarlo a parte.

```bash
cd time-telescope

uv sync                             # crea .venv e installa dal lockfile
uv run uvicorn main:app --reload --port 8010
```

Per aggiungere una dipendenza — `pyproject.toml` e `uv.lock` restano
allineati da soli:

```bash
uv add <pacchetto>
uv add --dev <pacchetto>            # solo per i test
```

Apri il browser su:
- http://localhost:8010/request.html
- http://localhost:8010/dashboard.html
- http://localhost:8010/calendar.html

> La root `/` risponde `404`: non esiste un `index.html`, le pagine vanno
> chiamate con il loro nome.

Il database SQLite viene creato in `./telescope_time.db`
se TELESCOPE_DB_PATH non è impostata.

#### Raggiungerlo dalla LAN

Di default uvicorn ascolta solo su localhost. Per provarlo da un altro
dispositivo — tablet in cupola, telefono — serve `--host 0.0.0.0`:

```bash
AUTH_MODE=dev uv run uvicorn main:app --reload --host 0.0.0.0 --port 8010
```

Poi `http://<ip-della-macchina>:8010/request.html`.
Serve `AUTH_MODE=dev`: senza Nginx e Authelia davanti, ogni richiesta
senza header di identità riceve `401`.

Se la porta non risponde da fuori mentre risponde in locale, è il
firewall:

```bash
sudo ufw allow 8010/tcp
```

#### Dati di esempio

Il database non è versionato: parte vuoto. Per popolarlo con qualche
ricerca e richiesta di prova — date relative a oggi, così il calendario
mostra sempre un giorno bloccato e uno conteso:

```bash
sqlite3 telescope_time.db < seed.sql
```

Le tabelle le crea l'app al primo avvio, quindi il seed va applicato dopo
averla avviata almeno una volta. Se `sqlite3` non è installato:

```bash
uv run python -c "import sqlite3; d=sqlite3.connect('telescope_time.db'); \
  d.executescript(open('seed.sql').read()); d.commit()"
```

---

### Test

```bash
uv run pytest                              # tutta la suite
uv run pytest tests/test_calendar.py       # un singolo file
uv run pytest -k rejected                  # un singolo test per nome
```

`uv run` installa da solo quel che manca, incluse le dipendenze del
gruppo `dev`: non serve un passo di installazione separato.

Ogni test gira su un database temporaneo creato da zero: la suite non
tocca `telescope_time.db`.

---

### Deploy con Docker

```bash
cd telescope_time
docker compose up -d --build

# Log
docker compose logs -f

# Ispezione DB — l'immagine non contiene sqlite3, si passa da Python
docker compose exec telescope_time python -c \
  "import sqlite3; d=sqlite3.connect('/data/telescope_time.db'); \
   print(d.execute('SELECT id, requested_night, start, end, status FROM time_requests').fetchall())"

# Dati di esempio
docker compose exec -T telescope_time python -c \
  "import sqlite3,sys; d=sqlite3.connect('/data/telescope_time.db'); \
   d.executescript(sys.stdin.read()); d.commit()" < seed.sql
```

Il database è persistente nel volume Docker `telescope_db`.

---

### Configurazione Nginx

```bash
cp nginx_time_telescope.conf /etc/nginx/sites-available/time_telescope
ln -s /etc/nginx/sites-available/time_telescope /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

Aggiornare i percorsi SSL nel .conf in modo coerente
con gli altri servizi *.ara.roma.it.

---

### Variabili d'ambiente

| Variabile            | Default                        | Descrizione             |
|----------------------|--------------------------------|-------------------------|
| TELESCOPE_DB_PATH    | ./telescope_time.db            | Percorso database       |
| SMTP_HOST            | (disabilitato)                 | Server SMTP             |
| SMTP_PORT            | 587                            | Porta SMTP              |
| SMTP_USER            |                                | Utente SMTP             |
| SMTP_PASSWORD        |                                | Password SMTP           |
| EMAIL_FROM           | crac@osservatorio.it           | Mittente email          |
| REVIEWER_EMAIL       | responsabile@osservatorio.it   | Destinatario notifiche  |
| TZ                   | (fuso del sistema)             | Fuso dell'osservatorio: le fasce orarie sono ora locale |
| AUTH_MODE            | forward-auth                   | `forward-auth` o `dev`  |
| DEV_USER             | sviluppo                       | Utente simulato (solo AUTH_MODE=dev) |
| DEV_GROUPS           | telescope-responsabili         | Gruppi simulati (solo AUTH_MODE=dev) |
| REVIEWERS_GROUP      | telescope-responsabili         | Gruppo che può approvare |

---

### Autenticazione

Gli utenti sono quelli di Authelia: il servizio non gestisce login, sessioni
né password. Nginx verifica la sessione (`auth_request`) e passa l'identità
all'applicazione negli header `Remote-User`, `Remote-Groups` e `Remote-Email`
— vedi `nginx_time_telescope.conf`.

Approvare o rifiutare una richiesta richiede l'appartenenza al gruppo
`REVIEWERS_GROUP`; gli altri endpoint sono aperti a tutti gli
autenticati.

> Gli header sono attendibili **solo** se il container non è raggiungibile
> scavalcando Nginx. La porta 8010 non va esposta all'esterno.

#### In sviluppo

`AUTH_MODE=dev` sintetizza quegli header, così non serve un'istanza Authelia:

```bash
AUTH_MODE=dev uv run uvicorn main:app --reload --port 8010
```

Si è autenticati come `DEV_USER` con i gruppi di `DEV_GROUPS`. Per provare
un utente diverso — per esempio un socio che non può approvare — bastano gli
header, senza riavviare nulla:

```bash
curl -X PATCH localhost:8010/telescope-time/requests/1 \
  -H 'Remote-User: mario' -H 'Remote-Groups: soci' \
  -H 'Content-Type: application/json' -d '{"status":"approved"}'
# → 403
```

`GET /telescope-time/me` restituisce l'identità con cui si sta operando.

---

### Endpoint API

| Metodo | Path                             | Descrizione                       |
|--------|----------------------------------|-----------------------------------|
| GET    | /telescope-time/research-programs         | Lista ricerche                    |
| POST   | /telescope-time/research-programs         | Crea ricerca (nome univoco)       |
| GET    | /telescope-time/research-programs/{id}    | Dettaglio ricerca                 |
| GET    | /telescope-time/requests        | Lista richieste (filtrabile)      |
| POST   | /telescope-time/requests        | Invia richiesta                   |
| GET    | /telescope-time/requests/{id}   | Dettaglio richiesta               |
| PATCH  | /telescope-time/requests/{id}   | Approva / rifiuta                 |
| PATCH  | /telescope-time/requests/{id}/schedule | Sposta data e orari (responsabili) |
| GET    | /telescope-time/calendar        | Calendario mensile (?year=&month=) |
| GET    | /telescope-time/requests/{id}/history | Decisioni e spostamenti sulla richiesta |
| GET    | /telescope-time/me               | Identità dell'utente collegato    |
| GET    | /telescope-time/statistics      | Statistiche aggregate             |

Documentazione interattiva: /docs (Swagger UI)

---

### Fasce orarie

Una richiesta occupa un intervallo, non una giornata: `start` e `end` sono
due istanti completi (`2026-09-12T22:00:00`), così una sessione che attraversa
la mezzanotte non ha bisogno di casi speciali.

`requested_night` resta come **notte di riferimento** ed è la data di
`start`: una sessione cominciata il 12 alle 23:00 appartiene alla notte del
12 anche se finisce il 13. È su questo che il calendario raggruppa.

Gli istanti sono **ora locale dell'osservatorio**, senza fuso: un valore con
offset viene rifiutato con `422`, perché renderebbe le fasce salvate non più
confrontabili fra loro. Da qui la variabile `TZ` nel compose — il server
confronta l'inizio richiesto con il proprio orologio per rifiutare le
osservazioni nel passato.

Due richieste possono contendersi la stessa fascia finché sono in attesa: è
normale, ed è quello che il calendario chiama `contested`. Il vincolo scatta
all'approvazione.

---

### Flusso operativo

1. Osservatore apre request.html: il suo nome è già noto,
   arriva da Authelia e non si digita
2. Seleziona ricerca esistente o ne crea una nuova
3. Indica co-osservatori e la fascia oraria — da quando a quando, non solo
   in che notte — → invia
4. Responsabile riceve email e apre dashboard.html
5. Approva o rifiuta con note opzionali
6. Il calendario riflette in tempo reale lo stato delle notti:

   | Situazione della notte                         | night_status |
   |------------------------------------------------|--------------|
   | nessuna richiesta                              | `free` (il giorno non compare nella mappa) |
   | richieste in attesa che non si sovrappongono   | `pending`    |
   | due o più in attesa con fasce sovrapposte      | `contested`  |
   | almeno una approvata                           | `booked`     |

7. Il telescopio può ospitare più programmi nella stessa notte: i giorni
   con più di un'osservazione approvata sono segnalati nella griglia.
   Quello che due programmi non possono condividere è lo stesso istante:
   approvare una richiesta la cui fascia interseca quella di una già
   approvata dà `409`, con il numero della richiesta in conflitto
8. Il responsabile può riprogrammare una richiesta, in qualunque stato si
   trovi, invece di rifiutarla: è così che si sblocca una fascia
   contesa, o si recupera una notte persa per meteo o manutenzione.
   Lo spostamento è consentito su qualsiasi data, passato incluso, perché
   serve anche a registrare a posteriori un'osservazione fatta; se la data
   è trascorsa l'interfaccia lo dichiara prima di confermare. Anche una
   rifiutata si sposta: recuperarla significa prima riprogrammarla e poi
   riapprovarla, non il contrario — riapprovare per prima la lascerebbe sulla
   fascia originale, che nel frattempo può essere occupata
9. Una decisione si può ribaltare: su una richiesta approvata la dashboard
   offre il rifiuto e viceversa, non il comando che la lascerebbe com'è.
   L'osservatore riceve un secondo avviso, e la conferma lo dichiara
10. Decisioni e spostamenti sono tracciati: entrambi finiscono in
    `decision_log`, distinti da `type`, con chi e quando, ed è quello
    che la dashboard mostra aprendo il dettaglio di una richiesta
11. L'esito arriva per email a chi ha fatto la richiesta, all'indirizzo che
   Authelia fornisce; se manca, l'avviso va al responsabile
