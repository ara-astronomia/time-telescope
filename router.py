"""
Telescope Time Request — FastAPI Router
Da includere nel server CRaC principale con:
    from telescope_time.router import router as telescope_router
    app.include_router(telescope_router)
"""

from fastapi import APIRouter, HTTPException, Depends, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Literal, Optional, List
from calendar import monthrange
from datetime import date, datetime
import sqlite3
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ─── Config ──────────────────────────────────────────────────────────────────

def db_path() -> str:
    """Percorso del database, letto a ogni chiamata.

    Leggerlo qui e non a livello di modulo permette ai test di puntare a un
    file temporaneo senza dover manipolare l'ambiente prima dell'import.
    """
    return os.environ.get("TELESCOPE_DB_PATH", "telescope_time.db")

# Autenticazione. In produzione l'identità arriva dagli header che Nginx
# riceve da Authelia (ForwardAuth): l'app non gestisce login né sessioni.
# In sviluppo 'dev' sintetizza quegli header, così non serve Authelia.
# Come per db_path(), i valori sono letti a ogni chiamata: i test possono
# così cambiare modalità senza dipendere dall'ordine degli import.

def auth_mode() -> str:
    return os.environ.get("AUTH_MODE", "forward-auth")   # 'forward-auth' | 'dev'

def dev_user() -> str:
    return os.environ.get("DEV_USER", "sviluppo")

def dev_groups() -> str:
    return os.environ.get("DEV_GROUPS", "telescope-responsabili")

def gruppo_responsabili() -> str:
    return os.environ.get("GRUPPO_RESPONSABILI", "telescope-responsabili")

# Config SMTP — da impostare nelle variabili d'ambiente
SMTP_HOST     = os.environ.get("SMTP_HOST", "")
SMTP_PORT     = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER     = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
EMAIL_FROM    = os.environ.get("EMAIL_FROM", "crac@osservatorio.it")
EMAIL_RESPONSABILE = os.environ.get("EMAIL_RESPONSABILE", "responsabile@osservatorio.it")

# ─── Database ─────────────────────────────────────────────────────────────────

def get_db():
    # timeout: quanto attendere se un'altra connessione sta scrivendo, prima
    # di sollevare "database is locked". Con WAL i lettori non aspettano mai,
    # ma le scritture restano serializzate.
    # check_same_thread=False: FastAPI esegue la dependency e l'handler nel
    # threadpool senza garantire che sia lo stesso thread, e con due richieste
    # simultanee capita che non lo sia. La connessione resta comunque privata
    # della singola richiesta, quindi non è condivisa fra thread concorrenti.
    conn = sqlite3.connect(db_path(), timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # va impostato su ogni connessione
    try:
        yield conn
    finally:
        conn.close()

# SQLite scrive `datetime('now')` come '2026-08-17 06:30:00': UTC, ma senza
# dirlo, e con uno spazio al posto della T. Non è ISO 8601 valido, quindi i
# browser lo interpretano come ora locale e mostrano un orario sbagliato di
# un'ora in inverno e due in estate.
ADESSO_UTC = "strftime('%Y-%m-%dT%H:%M:%SZ','now')"

def init_db():
    conn = sqlite3.connect(db_path())
    # WAL: lettori e scrittore procedono in parallelo invece di bloccarsi a
    # vicenda. È persistente sul file, quindi basta impostarlo qui.
    conn.execute("PRAGMA journal_mode = WAL")
    cursor = conn.cursor()
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS ricerche (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            nome        TEXT    NOT NULL UNIQUE,
            descrizione TEXT,
            specifiche  TEXT,
            creata_il   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );

        CREATE TABLE IF NOT EXISTS utenti (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            -- username non nullo = identità verificata da Authelia.
            -- NULL per chi è conosciuto solo per nome (co-osservatori, #40).
            username    TEXT    UNIQUE,
            nome        TEXT    NOT NULL,
            -- chiave con cui si riconosce una persona già in anagrafica (#40);
            -- più righe possono averla NULL.
            email       TEXT    UNIQUE,
            creato_il   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );

        CREATE TABLE IF NOT EXISTS richieste (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            ricerca_id          INTEGER NOT NULL REFERENCES ricerche(id),
            richiedente_id      INTEGER NOT NULL REFERENCES utenti(id),
            co_osservatori      TEXT,
            giorno_richiesto    TEXT    NOT NULL,
            stato               TEXT    NOT NULL DEFAULT 'in_attesa',
            note_responsabile   TEXT,
            creata_il           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            aggiornata_il       TEXT
        );

        CREATE TABLE IF NOT EXISTS richieste_storico (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            richiesta_id      INTEGER NOT NULL REFERENCES richieste(id),
            stato_precedente  TEXT    NOT NULL,
            stato_nuovo       TEXT    NOT NULL,
            note              TEXT,
            deciso_da         TEXT,
            deciso_il         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );
    """)
    conn.commit()
    conn.close()

# ─── Autenticazione ───────────────────────────────────────────────────────────

class Utente(BaseModel):
    nome: str                             # username di Authelia
    gruppi: List[str] = []
    email: Optional[str] = None
    nome_completo: Optional[str] = None   # Remote-Name, se Authelia lo invia
    id: Optional[int] = None              # valorizzato da utente_registrato

    @property
    def nome_visualizzato(self) -> str:
        """Nome da mostrare: il display name se c'è, altrimenti lo username,
        che è comunque leggibile."""
        return self.nome_completo or self.nome

    @property
    def e_responsabile(self) -> bool:
        return gruppo_responsabili() in self.gruppi


def utente_corrente(
    remote_user:   Optional[str] = Header(None, alias="Remote-User"),
    remote_groups: str           = Header("",   alias="Remote-Groups"),
    remote_email:  Optional[str] = Header(None, alias="Remote-Email"),
    remote_name:   Optional[str] = Header(None, alias="Remote-Name"),
) -> Utente:
    """Identità dell'utente, dagli header impostati da Nginx via Authelia.

    Gli header sono attendibili solo se il servizio non è raggiungibile
    scavalcando Nginx: chi arriva diretto sulla porta 8010 può dichiarare
    quel che vuole. Il container non deve quindi esporre la porta all'esterno.
    """
    if auth_mode() == "dev":
        remote_user   = remote_user   or dev_user()
        remote_groups = remote_groups or dev_groups()
        remote_email  = remote_email  or f"{remote_user}@example.test"

    if not remote_user:
        # Nessun fallback in forward-auth: header assente significa che la
        # richiesta non è passata da Authelia.
        raise HTTPException(status_code=401, detail="Autenticazione richiesta.")

    return Utente(
        nome=remote_user,
        gruppi=[g.strip() for g in remote_groups.split(",") if g.strip()],
        email=remote_email,
        nome_completo=remote_name,
    )


def registra_utente(db: sqlite3.Connection, utente: "Utente") -> int:
    """Allinea l'anagrafica all'identità che arriva da Authelia e ne restituisce l'id.

    Scrive solo se il record manca o se nome/email sono cambiati: le richieste
    normali costano una SELECT, non una scrittura.
    """
    riga = db.execute(
        "SELECT id, nome, email FROM utenti WHERE username = ?", (utente.nome,)
    ).fetchone()

    if riga is None:
        try:
            cursore = db.execute(
                "INSERT INTO utenti (username, nome, email) VALUES (?, ?, ?)",
                (utente.nome, utente.nome_visualizzato, utente.email),
            )
            db.commit()
            return cursore.lastrowid
        except sqlite3.IntegrityError:
            db.rollback()
            # Due casi finiscono qui.
            riga = db.execute(
                "SELECT id, nome, email FROM utenti WHERE username = ?", (utente.nome,)
            ).fetchone()
            if riga is not None:
                # 1) un'altra richiesta dello stesso utente è arrivata prima:
                #    la SELECT iniziale non la vedeva ancora.
                return riga["id"]
            # 2) l'email è già in anagrafica. Se appartiene a una persona
            #    conosciuta solo per nome — un co-osservatore inserito a mano
            #    (#40) — è la stessa persona che ora si autentica: il record
            #    viene promosso, non duplicato, così le osservazioni a cui ha
            #    partecipato restano collegate a lei.
            gemello = db.execute(
                "SELECT id, username FROM utenti WHERE email = ?", (utente.email,)
            ).fetchone()

            if gemello is not None and gemello["username"] is None:
                db.execute(
                    "UPDATE utenti SET username = ?, nome = ? WHERE id = ?",
                    (utente.nome, utente.nome_visualizzato, gemello["id"]),
                )
                db.commit()
                return gemello["id"]

            # 3) l'email appartiene a un altro account già verificato: caso
            #    patologico, Authelia chiede indirizzi univoci. Si registra
            #    senza email invece di negare l'accesso.
            print(
                f"[anagrafica] '{utente.nome}': email {utente.email!r} già "
                f"assegnata a un altro utente verificato, registrato senza indirizzo",
                flush=True,
            )
            cursore = db.execute(
                "INSERT INTO utenti (username, nome, email) VALUES (?, ?, NULL)",
                (utente.nome, utente.nome_visualizzato),
            )
            db.commit()
            return cursore.lastrowid

    if (riga["nome"], riga["email"]) != (utente.nome_visualizzato, utente.email):
        try:
            db.execute(
                "UPDATE utenti SET nome = ?, email = ? WHERE id = ?",
                (utente.nome_visualizzato, utente.email, riga["id"]),
            )
            db.commit()
        except sqlite3.IntegrityError:
            # L'email è passata a un altro account: si aggiorna il solo nome.
            db.rollback()
            db.execute(
                "UPDATE utenti SET nome = ? WHERE id = ?",
                (utente.nome_visualizzato, riga["id"]),
            )
            db.commit()
    return riga["id"]


def utente_registrato(
    utente: Utente = Depends(utente_corrente),
    db: sqlite3.Connection = Depends(get_db),
) -> Utente:
    """Utente corrente, con l'id del suo record in anagrafica."""
    utente.id = registra_utente(db, utente)
    return utente


def solo_responsabili(utente: Utente = Depends(utente_corrente)) -> Utente:
    if not utente.e_responsabile:
        raise HTTPException(
            status_code=403,
            detail=f"Operazione riservata al gruppo '{gruppo_responsabili()}'.",
        )
    return utente


# Ogni endpoint richiede un utente autenticato; l'approvazione richiede in più
# l'appartenenza al gruppo dei responsabili.
router = APIRouter(
    prefix="/telescope-time",
    tags=["Telescope Time"],
    dependencies=[Depends(utente_registrato)],
)

# ─── Modelli Pydantic ─────────────────────────────────────────────────────────

class RicercaCreate(BaseModel):
    nome: str
    descrizione: Optional[str] = None
    specifiche: Optional[str] = None

class RicercaOut(BaseModel):
    id: int
    nome: str
    descrizione: Optional[str]
    specifiche: Optional[str]
    creata_il: str

class RichiestaCreate(BaseModel):
    # `osservatore` non c'è: l'identità arriva da Authelia, non dal body,
    # quindi non è falsificabile. Un campo omonimo inviato viene ignorato.
    ricerca_id: int
    co_osservatori: Optional[str] = None
    # `date` valida e normalizza il formato ISO: una stringa non conforme
    # produce 422 prima che l'handler venga eseguito.
    giorno_richiesto: date

class AggiornamentoStato(BaseModel):
    stato: Literal["approvata", "rifiutata"]
    note_responsabile: Optional[str] = None

class DecisioneOut(BaseModel):
    id: int
    richiesta_id: int
    stato_precedente: str
    stato_nuovo: str
    note: Optional[str]
    deciso_da: Optional[str]
    deciso_il: str

class RichiestaOut(BaseModel):
    id: int
    ricerca_id: int
    nome_ricerca: str
    osservatore: str
    co_osservatori: Optional[str]
    giorno_richiesto: str
    stato: str
    note_responsabile: Optional[str]
    creata_il: str
    aggiornata_il: Optional[str]

# ─── Utility Email ────────────────────────────────────────────────────────────

def invia_messaggio(destinatario: str, oggetto: str, corpo: str):
    """Unico punto di invio. Senza SMTP configurato logga e basta.

    Isolarlo qui rende verificabile *a chi* viene mandato un messaggio senza
    un server di posta, ed è il posto da toccare quando l'invio passerà su
    BackgroundTasks (#8).
    """
    if not SMTP_HOST or not SMTP_USER:
        print(f"[SMTP non configurato] a {destinatario}: {oggetto}", flush=True)
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = oggetto
    msg["From"]    = EMAIL_FROM
    msg["To"]      = destinatario
    msg.attach(MIMEText(corpo, "plain"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(EMAIL_FROM, destinatario, msg.as_string())
    except Exception as e:
        print(f"[Errore invio email] a {destinatario}: {e}", flush=True)


def send_email_notifica(richiesta: dict, ricerca: dict):
    corpo = f"""
Nuova richiesta tempo telescopio ricevuta.

Osservatore:      {richiesta['osservatore']}
Co-osservatori:   {richiesta['co_osservatori'] or '—'}
Ricerca:          {ricerca['nome']}
Giorno richiesto: {richiesta['giorno_richiesto']}

Descrizione ricerca:
{ricerca['descrizione'] or '—'}

Specifiche:
{ricerca['specifiche'] or '—'}

Accedi alla dashboard CRaC per approvare o rifiutare la richiesta.
    """.strip()

    invia_messaggio(
        EMAIL_RESPONSABILE,
        f"[CRaC] Nuova richiesta tempo telescopio — {ricerca['nome']}",
        corpo,
    )


def send_email_esito(richiesta: dict):
    """L'indirizzo arriva dall'anagrafica, che lo prende da Authelia. Se manca,
    l'avviso va al responsabile, che almeno sa di doverlo riferire a voce."""
    esito = "✅ APPROVATA" if richiesta["stato"] == "approvata" else "❌ RIFIUTATA"
    corpo = f"""
La tua richiesta di tempo telescopio è stata: {esito}

Osservatore:       {richiesta['osservatore']}
Ricerca:           {richiesta['nome_ricerca']}
Giorno richiesto:  {richiesta['giorno_richiesto']}
Note responsabile: {richiesta['note_responsabile'] or '—'}
    """.strip()

    invia_messaggio(
        richiesta["email_osservatore"] or EMAIL_RESPONSABILE,
        f"[CRaC] Richiesta {esito} — {richiesta['nome_ricerca']}",
        corpo,
    )

# ─── Endpoint Utente ──────────────────────────────────────────────────────────

@router.get("/me", response_model=Utente, response_model_exclude={"id"})
def me(utente: Utente = Depends(utente_corrente)):
    """Identità dell'utente collegato: serve alle pagine per sapere se
    mostrare i comandi di approvazione."""
    return utente

# ─── Endpoint Ricerche ────────────────────────────────────────────────────────

@router.get("/ricerche", response_model=List[RicercaOut])
def lista_ricerche(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute("SELECT * FROM ricerche ORDER BY nome").fetchall()
    return [dict(r) for r in rows]


@router.post("/ricerche", response_model=RicercaOut, status_code=201)
def crea_ricerca(body: RicercaCreate, db: sqlite3.Connection = Depends(get_db)):
    try:
        cursor = db.execute(
            "INSERT INTO ricerche (nome, descrizione, specifiche) VALUES (?, ?, ?)",
            (body.nome.strip(), body.descrizione, body.specifiche)
        )
        db.commit()
        return leggi_ricerca(db, cursor.lastrowid)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=f"Ricerca '{body.nome}' già esistente.")


@router.get("/ricerche/{ricerca_id}", response_model=RicercaOut)
def dettaglio_ricerca(ricerca_id: int, db: sqlite3.Connection = Depends(get_db)):
    return leggi_ricerca(db, ricerca_id)

# ─── Endpoint Richieste ───────────────────────────────────────────────────────

def estremi_del_mese(anno: int, mese: int) -> tuple[str, str]:
    ultimo_giorno = monthrange(anno, mese)[1]
    return f"{anno}-{mese:02d}-01", f"{anno}-{mese:02d}-{ultimo_giorno:02d}"


RICHIESTE_COMPLETE = """
    SELECT r.*, rc.nome as nome_ricerca,
           u.nome as osservatore, u.email as email_osservatore
    FROM richieste r
    JOIN ricerche rc ON rc.id = r.ricerca_id
    JOIN utenti   u  ON u.id  = r.richiedente_id
"""


RICHIESTA_NON_TROVATA = "Richiesta non trovata."


def leggi_richiesta(db: sqlite3.Connection, richiesta_id: int) -> dict:
    riga = db.execute(f"{RICHIESTE_COMPLETE} WHERE r.id = ?", (richiesta_id,)).fetchone()
    if riga is None:
        raise HTTPException(status_code=404, detail=RICHIESTA_NON_TROVATA)
    return dict(riga)


def verifica_richiesta(db: sqlite3.Connection, richiesta_id: int) -> None:
    if db.execute("SELECT 1 FROM richieste WHERE id = ?", (richiesta_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail=RICHIESTA_NON_TROVATA)


def leggi_ricerca(db: sqlite3.Connection, ricerca_id: int) -> dict:
    riga = db.execute("SELECT * FROM ricerche WHERE id = ?", (ricerca_id,)).fetchone()
    if riga is None:
        raise HTTPException(status_code=404, detail="Ricerca non trovata.")
    return dict(riga)


@router.get("/richieste", response_model=List[RichiestaOut])
def lista_richieste(
    stato: Optional[str] = None,
    db: sqlite3.Connection = Depends(get_db)
):
    filtro = " WHERE r.stato = ?" if stato else ""
    righe = db.execute(
        f"{RICHIESTE_COMPLETE}{filtro} ORDER BY r.giorno_richiesto DESC",
        [stato] if stato else [],
    ).fetchall()
    return [dict(riga) for riga in righe]


@router.get("/richieste/{richiesta_id}", response_model=RichiestaOut)
def dettaglio_richiesta(richiesta_id: int, db: sqlite3.Connection = Depends(get_db)):
    return leggi_richiesta(db, richiesta_id)


@router.post("/richieste", response_model=RichiestaOut, status_code=201)
def invia_richiesta(
    body: RichiestaCreate,
    db: sqlite3.Connection = Depends(get_db),
    utente: Utente = Depends(utente_registrato),
):
    ricerca = leggi_ricerca(db, body.ricerca_id)
    giorno = body.giorno_richiesto.isoformat()

    if db.execute(
        "SELECT 1 FROM richieste WHERE ricerca_id = ? AND giorno_richiesto = ? AND stato != 'rifiutata'",
        (body.ricerca_id, giorno),
    ).fetchone():
        raise HTTPException(
            status_code=409,
            detail="Esiste già una richiesta per questa ricerca in quella data.",
        )

    cursore = db.execute(
        """INSERT INTO richieste (ricerca_id, richiedente_id, co_osservatori, giorno_richiesto)
           VALUES (?, ?, ?, ?)""",
        (body.ricerca_id, utente.id, body.co_osservatori, giorno),
    )
    db.commit()

    richiesta = leggi_richiesta(db, cursore.lastrowid)
    send_email_notifica(richiesta, ricerca)
    return richiesta


@router.patch("/richieste/{richiesta_id}", response_model=RichiestaOut)
def aggiorna_stato(
    richiesta_id: int,
    body: AggiornamentoStato,
    db: sqlite3.Connection = Depends(get_db),
    utente: Utente = Depends(solo_responsabili),
):
    richiesta = leggi_richiesta(db, richiesta_id)
    stato_precedente = richiesta["stato"]
    # Ribaltare una decisione è legittimo — il meteo cambia — ma va tracciato.
    # Se invece lo stato non cambia (doppio click sul pulsante), non si
    # registra nulla e non si rimanda l'email.
    cambia_stato = body.stato != stato_precedente

    # Le note già scritte non vanno perse quando il PATCH non le include.
    note = body.note_responsabile if body.note_responsabile is not None else richiesta["note_responsabile"]

    if cambia_stato:
        db.execute(
            """INSERT INTO richieste_storico
                   (richiesta_id, stato_precedente, stato_nuovo, note, deciso_da)
               VALUES (?, ?, ?, ?, ?)""",
            (richiesta_id, stato_precedente, body.stato, body.note_responsabile, utente.nome)
        )

    db.execute(
        f"""UPDATE richieste SET stato = ?, note_responsabile = ?, aggiornata_il = {ADESSO_UTC}
            WHERE id = ?""",
        (body.stato, note, richiesta_id)
    )
    db.commit()

    aggiornata = leggi_richiesta(db, richiesta_id)
    if cambia_stato:
        send_email_esito(aggiornata)
    return aggiornata


@router.get("/richieste/{richiesta_id}/storico", response_model=List[DecisioneOut])
def storico_richiesta(richiesta_id: int, db: sqlite3.Connection = Depends(get_db)):
    """Decisioni prese su una richiesta, dalla più vecchia alla più recente."""
    verifica_richiesta(db, richiesta_id)
    righe = db.execute(
        "SELECT * FROM richieste_storico WHERE richiesta_id = ? ORDER BY id",
        (richiesta_id,)
    ).fetchall()
    return [dict(r) for r in righe]


@router.get("/calendario")
def calendario(
    anno:  Optional[int] = None,
    mese:  Optional[int] = None,
    db: sqlite3.Connection = Depends(get_db)
):
    """Richieste approvate e in attesa del mese, raggruppate per giorno.

    Ogni giorno riporta `stato_giorno` (`libera`, `contesa`, `bloccata`),
    `approvate` — quante osservazioni condividono la notte — e l'elenco delle
    richieste. I giorni senza richieste non compaiono.
    """
    oggi = datetime.now()
    anno = anno or oggi.year
    mese = mese or oggi.month
    primo, ultimo = estremi_del_mese(anno, mese)

    righe = db.execute("""
        SELECT r.id, u.nome as osservatore, r.co_osservatori, r.giorno_richiesto,
               r.stato, r.note_responsabile, r.creata_il,
               rc.id as ricerca_id, rc.nome as nome_ricerca,
               rc.descrizione, rc.specifiche
        FROM richieste r
        JOIN ricerche rc ON rc.id = r.ricerca_id
        JOIN utenti   u  ON u.id  = r.richiedente_id
        WHERE r.giorno_richiesto BETWEEN ? AND ?
          AND r.stato IN ('approvata', 'in_attesa')
        ORDER BY r.giorno_richiesto, r.stato DESC, r.creata_il
    """, (primo, ultimo)).fetchall()

    giorni: dict = {}
    for riga in righe:
        richiesta = dict(riga)
        giorno = giorni.setdefault(
            richiesta.pop("giorno_richiesto"),
            {"stato_giorno": "libera", "approvate": 0, "richieste": []},
        )
        giorno["richieste"].append(richiesta)
        if richiesta["stato"] == "approvata":
            giorno["approvate"] += 1
            giorno["stato_giorno"] = "bloccata"
        elif giorno["stato_giorno"] == "libera":
            giorno["stato_giorno"] = "contesa"

    return {"anno": anno, "mese": mese, "giorni": giorni}


@router.get("/statistiche")
def statistiche(db: sqlite3.Connection = Depends(get_db)):
    """Endpoint bonus per statistiche aggregate — utile per sviluppi futuri."""
    totali = db.execute("""
        SELECT stato, COUNT(*) as conteggio FROM richieste GROUP BY stato
    """).fetchall()

    per_ricerca = db.execute("""
        SELECT rc.nome, COUNT(r.id) as richieste, 
               SUM(CASE WHEN r.stato='approvata' THEN 1 ELSE 0 END) as approvate
        FROM ricerche rc
        LEFT JOIN richieste r ON r.ricerca_id = rc.id
        GROUP BY rc.id ORDER BY richieste DESC
    """).fetchall()

    return {
        "per_stato": [dict(r) for r in totali],
        "per_ricerca": [dict(r) for r in per_ricerca]
    }
