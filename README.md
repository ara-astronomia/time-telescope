# Telescope Time — Modulo CRaC

Servizio per la gestione delle richieste di tempo telescopio.
Accessibile su `time_telescope.ara.roma.it`.

## Struttura

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
│   ├── telescope_time_request.html
│   ├── telescope_time_dashboard.html
│   └── telescope_time_calendario.html
└── README.md
```

> Le tre pagine HTML vanno nella sottocartella `static/`.

---

## Installazione locale (test)

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
- http://localhost:8010/telescope_time_request.html
- http://localhost:8010/telescope_time_dashboard.html
- http://localhost:8010/telescope_time_calendario.html

> La root `/` risponde `404`: non esiste un `index.html`, le pagine vanno
> chiamate con il loro nome.

Il database SQLite viene creato in `./telescope_time.db`
se TELESCOPE_DB_PATH non è impostata.

### Raggiungerlo dalla LAN

Di default uvicorn ascolta solo su localhost. Per provarlo da un altro
dispositivo — tablet in cupola, telefono — serve `--host 0.0.0.0`:

```bash
AUTH_MODE=dev uv run uvicorn main:app --reload --host 0.0.0.0 --port 8010
```

Poi `http://<ip-della-macchina>:8010/telescope_time_request.html`.
Serve `AUTH_MODE=dev`: senza Nginx e Authelia davanti, ogni richiesta
senza header di identità riceve `401`.

Se la porta non risponde da fuori mentre risponde in locale, è il
firewall:

```bash
sudo ufw allow 8010/tcp
```

### Dati di esempio

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

## Test

```bash
uv run pytest                              # tutta la suite
uv run pytest tests/test_calendario.py     # un singolo file
uv run pytest -k rifiutate                 # un singolo test per nome
```

`uv run` installa da solo quel che manca, incluse le dipendenze del
gruppo `dev`: non serve un passo di installazione separato.

Ogni test gira su un database temporaneo creato da zero: la suite non
tocca `telescope_time.db`.

---

## Deploy con Docker

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

## Configurazione Nginx

```bash
cp nginx_time_telescope.conf /etc/nginx/sites-available/time_telescope
ln -s /etc/nginx/sites-available/time_telescope /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

Aggiornare i percorsi SSL nel .conf in modo coerente
con gli altri servizi *.ara.roma.it.

---

## Variabili d'ambiente

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

## Autenticazione

Gli utenti sono quelli di Authelia: il servizio non gestisce login, sessioni
né password. Nginx verifica la sessione (`auth_request`) e passa l'identità
all'applicazione negli header `Remote-User`, `Remote-Groups` e `Remote-Email`
— vedi `nginx_time_telescope.conf`.

Approvare o rifiutare una richiesta richiede l'appartenenza al gruppo
`REVIEWERS_GROUP`; gli altri endpoint sono aperti a tutti gli
autenticati.

> Gli header sono attendibili **solo** se il container non è raggiungibile
> scavalcando Nginx. La porta 8010 non va esposta all'esterno.

### In sviluppo

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

## Endpoint API

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

## Fasce orarie

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

## Flusso operativo

1. Osservatore apre telescope_time_request.html: il suo nome è già noto,
   arriva da Authelia e non si digita
2. Seleziona ricerca esistente o ne crea una nuova
3. Indica co-osservatori e la fascia oraria — da quando a quando, non solo
   in che notte — → invia
4. Responsabile riceve email e apre telescope_time_dashboard.html
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
