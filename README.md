# Telescope Time — Modulo CRaC

Servizio per la gestione delle richieste di tempo telescopio.
Accessibile su `time_telescope.ara.roma.it`.

## Struttura

```
telescope_time/
├── main.py                         ← FastAPI app
├── router.py                       ← endpoint API
├── requirements.txt
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

```bash
cd telescope_time

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

uvicorn main:app --reload --port 8010
```

Apri il browser su:
- http://localhost:8010/telescope_time_request.html
- http://localhost:8010/telescope_time_dashboard.html
- http://localhost:8010/telescope_time_calendario.html

Il database SQLite viene creato in `./telescope_time.db`
se TELESCOPE_DB_PATH non è impostata.

---

## Deploy con Docker

```bash
cd telescope_time
docker compose up -d --build

# Log
docker compose logs -f

# Ispezione DB
docker compose exec telescope_time sqlite3 /data/telescope_time.db
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
| EMAIL_RESPONSABILE   | responsabile@osservatorio.it   | Destinatario notifiche  |

---

## Endpoint API

| Metodo | Path                             | Descrizione                       |
|--------|----------------------------------|-----------------------------------|
| GET    | /telescope-time/ricerche         | Lista ricerche                    |
| POST   | /telescope-time/ricerche         | Crea ricerca (nome univoco)       |
| GET    | /telescope-time/ricerche/{id}    | Dettaglio ricerca                 |
| GET    | /telescope-time/richieste        | Lista richieste (filtrabile)      |
| POST   | /telescope-time/richieste        | Invia richiesta                   |
| PATCH  | /telescope-time/richieste/{id}   | Approva / rifiuta                 |
| GET    | /telescope-time/calendario       | Calendario mensile (?anno=&mese=) |
| GET    | /telescope-time/statistiche      | Statistiche aggregate             |

Documentazione interattiva: /docs (Swagger UI)

---

## Flusso operativo

1. Osservatore apre telescope_time_request.html
2. Seleziona ricerca esistente o ne crea una nuova
3. Inserisce osservatore, co-osservatori, data → invia
4. Responsabile riceve email e apre telescope_time_dashboard.html
5. Approva o rifiuta con note opzionali
6. Il calendario riflette in tempo reale lo stato delle date
   (libera / contesa / bloccata)
