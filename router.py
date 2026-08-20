"""
Telescope Time Request — FastAPI Router
Da includere nel server CRaC principale con:
    from telescope_time.router import router as telescope_router
    app.include_router(telescope_router)
"""

from fastapi import APIRouter, HTTPException, Depends, Header, Cookie
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, NaiveDatetime, ValidationInfo, computed_field, field_validator
from typing import Literal, Optional, List
from calendar import monthrange
from datetime import datetime, time, timedelta
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

def auth_mode() -> str:
    """'forward-auth' (default) o 'dev'.

    In produzione l'identità arriva dagli header che Nginx riceve da
    Authelia (ForwardAuth): l'app non gestisce login né sessioni. In
    sviluppo 'dev' sintetizza quegli header, così non serve Authelia.

    Come db_path(), letto a ogni chiamata: i test possono così cambiare
    modalità senza dipendere dall'ordine degli import.
    """
    return os.environ.get("AUTH_MODE", "forward-auth")

def dev_user() -> str:
    return os.environ.get("DEV_USER", "sviluppo")

def dev_groups() -> str:
    return os.environ.get("DEV_GROUPS", "telescope-responsabili")

def gruppo_responsabili() -> str:
    return os.environ.get("GRUPPO_RESPONSABILI", "telescope-responsabili")

SMTP_HOST     = os.environ.get("SMTP_HOST", "")
SMTP_PORT     = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER     = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
EMAIL_FROM    = os.environ.get("EMAIL_FROM", "crac@osservatorio.it")
EMAIL_RESPONSABILE = os.environ.get("EMAIL_RESPONSABILE", "responsabile@osservatorio.it")

# ─── Database ─────────────────────────────────────────────────────────────────

def get_db():
    """Connessione SQLite privata della singola richiesta HTTP.

    timeout=15: quanto attendere se un'altra connessione sta scrivendo, prima
    di sollevare "database is locked". Con WAL i lettori non aspettano mai,
    ma le scritture restano serializzate.

    check_same_thread=False: FastAPI esegue la dependency e l'handler nel
    threadpool senza garantire che sia lo stesso thread, e con due richieste
    simultanee capita che non lo sia. La connessione resta comunque privata
    della singola richiesta, quindi non è condivisa fra thread concorrenti.
    """
    conn = sqlite3.connect(db_path(), timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
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
    """Crea lo schema se assente e attiva il WAL journaling.

    WAL fa procedere lettori e scrittore in parallelo invece di bloccarsi a
    vicenda, ed è persistito sul file: va attivato una sola volta qui, a
    differenza di `PRAGMA foreign_keys` in get_db(), che non lo è.
    """
    conn = sqlite3.connect(db_path())
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
            -- notte di riferimento, derivata dalla data di `inizio`: le ore
            -- piccole appartengono alla notte precedente. È la chiave su cui
            -- il calendario raggruppa.
            giorno_richiesto    TEXT    NOT NULL,
            -- fascia oraria, ora locale dell'osservatorio: '2026-09-12T22:00:00'.
            inizio              TEXT    NOT NULL,
            fine                TEXT    NOT NULL,
            stato               TEXT    NOT NULL DEFAULT 'in_attesa',
            note_responsabile   TEXT,
            creata_il           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            aggiornata_il       TEXT
        );

        -- Due tipi di evento nella stessa tabella, distinti da `tipo`: una
        -- sola cronologia ordinata è ciò che serve a chi legge la storia di
        -- una richiesta. Le colonne dell'altro tipo restano NULL.
        CREATE TABLE IF NOT EXISTS richieste_storico (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            richiesta_id      INTEGER NOT NULL REFERENCES richieste(id),
            tipo              TEXT    NOT NULL DEFAULT 'decisione',
            stato_precedente  TEXT,
            stato_nuovo       TEXT,
            inizio_precedente TEXT,
            fine_precedente   TEXT,
            inizio_nuovo      TEXT,
            fine_nuovo        TEXT,
            note              TEXT,
            deciso_da         TEXT,
            deciso_il         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );
    """)
    conn.commit()
    conn.close()

# ─── Autenticazione ───────────────────────────────────────────────────────────

class Utente(BaseModel):
    nome: str = Field(description="Username di Authelia.")
    gruppi: List[str] = []
    email: Optional[str] = None
    nome_completo: Optional[str] = Field(None, description="Remote-Name, se Authelia lo invia.")
    id: Optional[int] = Field(None, description="Valorizzato da utente_registrato.")

    @property
    def nome_visualizzato(self) -> str:
        """Nome da mostrare: il display name se c'è, altrimenti lo username,
        che è comunque leggibile."""
        return self.nome_completo or self.nome

    @computed_field
    @property
    def e_responsabile(self) -> bool:
        return gruppo_responsabili() in self.gruppi

    @computed_field
    @property
    def modalita_dev(self) -> bool:
        return auth_mode() == "dev"


def utente_corrente(
    remote_user:   Optional[str] = Header(None, alias="Remote-User"),
    remote_groups: str           = Header("",   alias="Remote-Groups"),
    remote_email:  Optional[str] = Header(None, alias="Remote-Email"),
    remote_name:   Optional[str] = Header(None, alias="Remote-Name"),
    dev_ruolo:     Optional[str] = Cookie(None),
) -> Utente:
    """Identità dell'utente, dagli header impostati da Nginx via Authelia.

    Gli header sono attendibili solo se il servizio non è raggiungibile
    scavalcando Nginx: chi arriva diretto sulla porta 8010 può dichiarare
    quel che vuole. Il container non deve quindi esporre la porta all'esterno.
    """
    if auth_mode() == "dev":
        if not remote_user and dev_ruolo == "socio":
            remote_user, remote_groups = "socio-dev", "soci"
            remote_name = remote_name or "Luca Bertani"
        elif not remote_user:
            remote_name = remote_name or "Marta Conti"
        remote_user   = remote_user   or dev_user()
        remote_groups = remote_groups or dev_groups()
        remote_email  = remote_email  or f"{remote_user}@example.test"

    if not remote_user:
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
        return _inserisci_o_concilia_utente(db, utente)

    if (riga["nome"], riga["email"]) != (utente.nome_visualizzato, utente.email):
        _aggiorna_nome_ed_email(db, riga["id"], utente)
    return riga["id"]


def _inserisci_o_concilia_utente(db: sqlite3.Connection, utente: "Utente") -> int:
    try:
        cursore = db.execute(
            "INSERT INTO utenti (username, nome, email) VALUES (?, ?, ?)",
            (utente.nome, utente.nome_visualizzato, utente.email),
        )
        db.commit()
        return cursore.lastrowid
    except sqlite3.IntegrityError:
        db.rollback()
        return _concilia_dopo_conflitto_anagrafica(db, utente)


def _concilia_dopo_conflitto_anagrafica(db: sqlite3.Connection, utente: "Utente") -> int:
    """Un altro INSERT ha violato UNIQUE fra la SELECT iniziale e questa: username o email
    sono già in anagrafica per un motivo diverso, da distinguere caso per caso."""
    riga = db.execute(
        "SELECT id, nome, email FROM utenti WHERE username = ?", (utente.nome,)
    ).fetchone()
    if riga is not None:
        return riga["id"]  # un'altra richiesta dello stesso utente ha vinto la corsa

    co_osservatore_da_promuovere = db.execute(
        "SELECT id, username FROM utenti WHERE email = ? AND username IS NULL", (utente.email,)
    ).fetchone()
    if co_osservatore_da_promuovere is not None:
        return _promuovi_co_osservatore(db, co_osservatore_da_promuovere["id"], utente)

    return _registra_senza_email(db, utente)


def _promuovi_co_osservatore(db: sqlite3.Connection, utente_id: int, utente: "Utente") -> int:
    """Un co-osservatore inserito a mano (#40), riconosciuto ora per email: il record
    viene aggiornato invece che duplicato, così le osservazioni a cui ha già
    partecipato restano collegate a lui."""
    db.execute(
        "UPDATE utenti SET username = ?, nome = ? WHERE id = ?",
        (utente.nome, utente.nome_visualizzato, utente_id),
    )
    db.commit()
    return utente_id


def _registra_senza_email(db: sqlite3.Connection, utente: "Utente") -> int:
    """L'email appartiene già a un altro account verificato — Authelia le richiede
    univoche, quindi è un caso patologico. Si registra senza indirizzo invece di
    negare l'accesso."""
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


def _aggiorna_nome_ed_email(db: sqlite3.Connection, utente_id: int, utente: "Utente") -> None:
    try:
        db.execute(
            "UPDATE utenti SET nome = ?, email = ? WHERE id = ?",
            (utente.nome_visualizzato, utente.email, utente_id),
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        _aggiorna_solo_nome(db, utente_id, utente)


def _aggiorna_solo_nome(db: sqlite3.Connection, utente_id: int, utente: "Utente") -> None:
    """L'email è passata a un altro account: qui si allinea solo il nome."""
    db.execute(
        "UPDATE utenti SET nome = ? WHERE id = ?",
        (utente.nome_visualizzato, utente_id),
    )
    db.commit()


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

# Mezzogiorno è la soglia convenzionale in astronomia — è dove taglia anche
# il giorno giuliano — ed è lontana da qualunque ora di osservazione reale,
# quindi nessuna sessione ci cade sopra per caso.
SOGLIA_NOTTE = time(12, 0)

class FasciaOraria(BaseModel):
    """inizio/fine come NaiveDatetime: rifiuta gli istanti con fuso, perché sono ora
    locale dell'osservatorio e un offset renderebbe le fasce salvate non più
    confrontabili fra loro."""
    inizio: NaiveDatetime
    fine: NaiveDatetime

    @field_validator("inizio", "fine")
    @classmethod
    def al_secondo(cls, istante: datetime) -> datetime:
        """Un formato unico è ciò che rende lecito confrontare le fasce come
        stringhe, in SQL come in Python."""
        return istante.replace(microsecond=0)

    @field_validator("fine")
    @classmethod
    def dopo_l_inizio(cls, istante: datetime, info: ValidationInfo) -> datetime:
        inizio = info.data.get("inizio")
        if inizio is not None and istante <= inizio:
            raise ValueError("La fine deve essere successiva all'inizio.")
        return istante

    @property
    def notte(self) -> str:
        giorno = self.inizio.date()
        if self.inizio.time() < SOGLIA_NOTTE:
            giorno -= timedelta(days=1)
        return giorno.isoformat()


class RichiestaCreate(FasciaOraria):
    """Niente campo `osservatore`: l'identità arriva da Authelia, non dal body,
    quindi non è falsificabile. Un campo omonimo inviato nel body viene ignorato."""
    ricerca_id: int
    co_osservatori: Optional[str] = None

    @field_validator("inizio")
    @classmethod
    def nel_futuro(cls, istante: datetime) -> datetime:
        if istante <= datetime.now():
            raise ValueError("L'osservazione deve cominciare nel futuro.")
        return istante


class SpostamentoOrario(FasciaOraria):
    """Nessun vincolo di futuro: il responsabile registra a posteriori anche
    un'osservazione già fatta. Che la data sia passata viene dichiarato, non
    impedito."""
    motivo: Optional[str] = None

class AggiornamentoStato(BaseModel):
    stato: Literal["approvata", "rifiutata"]
    note_responsabile: Optional[str] = None

class EventoOut(BaseModel):
    """Una voce di storico: una decisione oppure uno spostamento. I campi
    dell'altro tipo sono nulli."""
    id: int
    richiesta_id: int
    tipo: str
    stato_precedente: Optional[str]
    stato_nuovo: Optional[str]
    inizio_precedente: Optional[str]
    fine_precedente: Optional[str]
    inizio_nuovo: Optional[str]
    fine_nuovo: Optional[str]
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
    inizio: str
    fine: str
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


def fascia_leggibile(richiesta: dict) -> str:
    """'12/09/2026 22:00 → 13/09/2026 01:00', senza ripetere la data quando la
    sessione non attraversa la mezzanotte."""
    inizio = datetime.fromisoformat(richiesta["inizio"])
    fine = datetime.fromisoformat(richiesta["fine"])
    formato_fine = "%H:%M" if inizio.date() == fine.date() else "%d/%m/%Y %H:%M"
    return f"{inizio:%d/%m/%Y %H:%M} → {fine:{formato_fine}}"


def send_email_notifica(richiesta: dict, ricerca: dict):
    corpo = f"""
Nuova richiesta tempo telescopio ricevuta.

Osservatore:      {richiesta['osservatore']}
Co-osservatori:   {richiesta['co_osservatori'] or '—'}
Ricerca:          {ricerca['nome']}
Fascia oraria:    {fascia_leggibile(richiesta)}

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
Fascia oraria:     {fascia_leggibile(richiesta)}
Note responsabile: {richiesta['note_responsabile'] or '—'}
    """.strip()

    invia_messaggio(
        richiesta["email_osservatore"] or EMAIL_RESPONSABILE,
        f"[CRaC] Richiesta {esito} — {richiesta['nome_ricerca']}",
        corpo,
    )

def send_email_spostamento(richiesta: dict, precedente: dict, motivo: Optional[str]):
    """L'osservatore si è visto assegnare un orario diverso da quello chiesto:
    non è un'informazione che possa scoprire per caso aprendo il calendario."""
    avviso = ""
    if datetime.fromisoformat(richiesta["inizio"]) < datetime.now():
        avviso = "\n\nAttenzione: la nuova fascia cade in una data passata."

    corpo = f"""
La tua osservazione è stata riprogrammata dal responsabile.

Osservatore:  {richiesta['osservatore']}
Ricerca:      {richiesta['nome_ricerca']}
Prima:        {fascia_leggibile(precedente)}
Adesso:       {fascia_leggibile(richiesta)}
Motivo:       {motivo or '—'}{avviso}
    """.strip()

    invia_messaggio(
        richiesta["email_osservatore"] or EMAIL_RESPONSABILE,
        f"[CRaC] Osservazione riprogrammata — {richiesta['nome_ricerca']}",
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


def gia_approvata_negli_stessi_istanti(
    db: sqlite3.Connection, richiesta_id: int, inizio: str, fine: str
) -> Optional[dict]:
    """Un'altra richiesta approvata che occupa lo strumento negli stessi
    istanti. Due programmi possono condividere la notte, non l'istante."""
    riga = db.execute(
        f"""{RICHIESTE_COMPLETE}
            WHERE r.stato = 'approvata' AND r.id != ?
              AND r.inizio < ? AND ? < r.fine""",
        (richiesta_id, fine, inizio),
    ).fetchone()
    return dict(riga) if riga else None


def blocca_per_scrittura(db: sqlite3.Connection) -> None:
    """Apre subito una transazione esclusiva, invece di aspettare la prima
    scrittura come farebbe SQLite da solo.

    Fra il controllo di conflitto e l'UPDATE c'è una finestra: senza questo,
    due approvazioni simultanee la attraversano entrambe e creano proprio la
    sovrapposizione che il vincolo esiste per impedire.
    """
    db.execute("BEGIN IMMEDIATE")


def conflitto_di_fascia(db: sqlite3.Connection, richiesta_id: int, inizio: str, fine: str):
    occupata = gia_approvata_negli_stessi_istanti(db, richiesta_id, inizio, fine)
    if occupata:
        raise HTTPException(
            status_code=409,
            detail=f"La fascia si sovrappone alla richiesta #{occupata['id']} "
                   f"({occupata['nome_ricerca']}, {fascia_leggibile(occupata)}), "
                   f"già approvata.",
        )


def registra_evento(db: sqlite3.Connection, richiesta_id: int, tipo: str,
                    autore: str, note: Optional[str], **valori) -> None:
    colonne = ", ".join(valori)
    segnaposto = ", ".join("?" * len(valori))
    db.execute(
        f"""INSERT INTO richieste_storico
                (richiesta_id, tipo, deciso_da, note, {colonne})
            VALUES (?, ?, ?, ?, {segnaposto})""",
        (richiesta_id, tipo, autore, note, *valori.values()),
    )


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
        f"{RICHIESTE_COMPLETE}{filtro} ORDER BY r.inizio DESC",
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

    if db.execute(
        "SELECT 1 FROM richieste WHERE ricerca_id = ? AND giorno_richiesto = ? AND stato != 'rifiutata'",
        (body.ricerca_id, body.notte),
    ).fetchone():
        raise HTTPException(
            status_code=409,
            detail="Esiste già una richiesta per questa ricerca in quella notte.",
        )

    cursore = db.execute(
        """INSERT INTO richieste
               (ricerca_id, richiedente_id, co_osservatori, giorno_richiesto, inizio, fine)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (body.ricerca_id, utente.id, body.co_osservatori, body.notte,
         body.inizio.isoformat(), body.fine.isoformat()),
    )
    db.commit()

    richiesta = leggi_richiesta(db, cursore.lastrowid)
    send_email_notifica(richiesta, ricerca)
    return richiesta


def note_o_esistenti(body: AggiornamentoStato, richiesta: dict) -> Optional[str]:
    """Le note già scritte non vanno perse quando il PATCH non le include."""
    return body.note_responsabile if body.note_responsabile is not None else richiesta["note_responsabile"]


@router.patch("/richieste/{richiesta_id}", response_model=RichiestaOut)
def aggiorna_stato(
    richiesta_id: int,
    body: AggiornamentoStato,
    db: sqlite3.Connection = Depends(get_db),
    utente: Utente = Depends(solo_responsabili),
):
    blocca_per_scrittura(db)
    richiesta = leggi_richiesta(db, richiesta_id)
    stato_precedente = richiesta["stato"]
    cambia_stato = body.stato != stato_precedente

    if body.stato == "approvata" and cambia_stato:
        conflitto_di_fascia(db, richiesta_id, richiesta["inizio"], richiesta["fine"])

    note = note_o_esistenti(body, richiesta)

    if cambia_stato:
        registra_evento(
            db, richiesta_id, "decisione", utente.nome, body.note_responsabile,
            stato_precedente=stato_precedente, stato_nuovo=body.stato,
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


@router.patch("/richieste/{richiesta_id}/orario", response_model=RichiestaOut)
def sposta_orario(
    richiesta_id: int,
    body: SpostamentoOrario,
    db: sqlite3.Connection = Depends(get_db),
    utente: Utente = Depends(solo_responsabili),
):
    """Riprogramma una richiesta, in attesa o già approvata.

    Separato dal PATCH dello stato perché sono due azioni distinte: una decide,
    l'altra riprogramma, e tenerle insieme renderebbe ambiguo cosa registrare
    nello storico.
    """
    blocca_per_scrittura(db)
    richiesta = leggi_richiesta(db, richiesta_id)
    inizio, fine = body.inizio.isoformat(), body.fine.isoformat()
    if (inizio, fine) == (richiesta["inizio"], richiesta["fine"]):
        return richiesta

    if richiesta["stato"] == "approvata":
        conflitto_di_fascia(db, richiesta_id, inizio, fine)

    registra_evento(
        db, richiesta_id, "spostamento", utente.nome, body.motivo,
        inizio_precedente=richiesta["inizio"], fine_precedente=richiesta["fine"],
        inizio_nuovo=inizio, fine_nuovo=fine,
    )
    db.execute(
        f"""UPDATE richieste
               SET giorno_richiesto = ?, inizio = ?, fine = ?, aggiornata_il = {ADESSO_UTC}
             WHERE id = ?""",
        (body.notte, inizio, fine, richiesta_id),
    )
    db.commit()

    spostata = leggi_richiesta(db, richiesta_id)
    send_email_spostamento(spostata, richiesta, body.motivo)
    return spostata


@router.get("/richieste/{richiesta_id}/storico", response_model=List[EventoOut])
def storico_richiesta(richiesta_id: int, db: sqlite3.Connection = Depends(get_db)):
    """Decisioni prese su una richiesta, dalla più vecchia alla più recente."""
    verifica_richiesta(db, richiesta_id)
    righe = db.execute(
        "SELECT * FROM richieste_storico WHERE richiesta_id = ? ORDER BY id",
        (richiesta_id,)
    ).fetchall()
    return [dict(r) for r in righe]


def registra_sovrapposizioni(richieste: List[dict], giorni: dict) -> set:
    """Annota su ogni notte le coppie di richieste le cui fasce si intersecano
    e restituisce le notti in cui la sovrapposizione riguarda solo richieste
    'in_attesa' (contesa).

    Le richieste arrivano ordinate per `inizio`: appena una comincia dopo la
    fine di `a`, tutte quelle che seguono fanno lo stesso, e il confronto per
    quell'`a` può fermarsi.
    """
    contese = set()
    for posizione, a in enumerate(richieste):
        for b in richieste[posizione + 1:]:
            if b["inizio"] >= a["fine"]:
                break
            notti = {a["giorno_richiesto"], b["giorno_richiesto"]}
            for chiave in notti:
                giorni[chiave]["sovrapposizioni"].append([a["id"], b["id"]])
            if a["stato"] == b["stato"] == "in_attesa":
                contese |= notti
    return contese


@router.get("/calendario")
def calendario(
    anno:  Optional[int] = None,
    mese:  Optional[int] = None,
    db: sqlite3.Connection = Depends(get_db)
):
    """Richieste approvate e in attesa del mese, raggruppate per notte.

    Ogni notte riporta `stato_giorno` (`richiesta`, `contesa`, `bloccata`), i
    conteggi `approvate` e `in_attesa`, le coppie di richieste le cui fasce si
    intersecano e l'elenco delle richieste. Le notti libere non compaiono.

    Contesa è la notte in cui due richieste non ancora approvate si disputano
    gli stessi istanti: due sessioni in turni distinti condividono la notte
    senza contendersela.
    """
    oggi = datetime.now()
    anno = anno or oggi.year
    mese = mese or oggi.month
    primo, ultimo = estremi_del_mese(anno, mese)

    righe = db.execute("""
        SELECT r.id, u.nome as osservatore, r.co_osservatori, r.giorno_richiesto,
               r.inizio, r.fine, r.stato, r.note_responsabile, r.creata_il,
               rc.id as ricerca_id, rc.nome as nome_ricerca,
               rc.descrizione, rc.specifiche
        FROM richieste r
        JOIN ricerche rc ON rc.id = r.ricerca_id
        JOIN utenti   u  ON u.id  = r.richiedente_id
        WHERE r.giorno_richiesto BETWEEN ? AND ?
          AND r.stato IN ('approvata', 'in_attesa')
        ORDER BY r.inizio, r.creata_il
    """, (primo, ultimo)).fetchall()

    richieste = [dict(riga) for riga in righe]
    giorni: dict = {}
    for richiesta in richieste:
        notte = giorni.setdefault(
            richiesta["giorno_richiesto"],
            {"stato_giorno": "richiesta", "approvate": 0, "in_attesa": 0,
             "sovrapposizioni": [], "richieste": []},
        )
        notte["richieste"].append(richiesta)
        notte["approvate" if richiesta["stato"] == "approvata" else "in_attesa"] += 1

    contese = registra_sovrapposizioni(richieste, giorni)

    for chiave, notte in giorni.items():
        if notte["approvate"]:
            notte["stato_giorno"] = "bloccata"
        elif chiave in contese:
            notte["stato_giorno"] = "contesa"

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
