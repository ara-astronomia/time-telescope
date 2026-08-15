"""
Telescope Time Request — FastAPI Router
Da includere nel server CRaC principale con:
    from telescope_time.router import router as telescope_router
    app.include_router(telescope_router)
"""

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, validator
from typing import Optional, List
from datetime import date, datetime
import sqlite3
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

router = APIRouter(prefix="/telescope-time", tags=["Telescope Time"])

# ─── Config ──────────────────────────────────────────────────────────────────

DB_PATH = os.environ.get("TELESCOPE_DB_PATH", "telescope_time.db")

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
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # va impostato su ogni connessione
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    conn = sqlite3.connect(DB_PATH)
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
            creata_il   TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS richieste (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            ricerca_id          INTEGER NOT NULL REFERENCES ricerche(id),
            osservatore         TEXT    NOT NULL,
            co_osservatori      TEXT,
            giorno_richiesto    TEXT    NOT NULL,
            stato               TEXT    NOT NULL DEFAULT 'in_attesa',
            note_responsabile   TEXT,
            creata_il           TEXT    NOT NULL DEFAULT (datetime('now')),
            aggiornata_il       TEXT
        );
    """)
    conn.commit()
    conn.close()

# Inizializza DB al caricamento del modulo
init_db()

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
    ricerca_id: int
    osservatore: str
    co_osservatori: Optional[str] = None
    giorno_richiesto: str  # formato ISO: YYYY-MM-DD

class AggiornamentoStato(BaseModel):
    stato: str  # 'approvata' | 'rifiutata'
    note_responsabile: Optional[str] = None

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

def send_email_notifica(richiesta: dict, ricerca: dict):
    """Invia notifica email al responsabile. Se SMTP non configurato, logga solo."""
    if not SMTP_HOST or not SMTP_USER:
        print(f"[SMTP non configurato] Nuova richiesta: {richiesta['osservatore']} — {ricerca['nome']} per il {richiesta['giorno_richiesto']}")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[CRaC] Nuova richiesta tempo telescopio — {ricerca['nome']}"
    msg["From"]    = EMAIL_FROM
    msg["To"]      = EMAIL_RESPONSABILE

    corpo = f"""
Nuova richiesta tempo telescopio ricevuta.

Osservatore:     {richiesta['osservatore']}
Co-osservatori:  {richiesta.get('co_osservatori') or '—'}
Ricerca:         {ricerca['nome']}
Giorno richiesto: {richiesta['giorno_richiesto']}

Descrizione ricerca:
{ricerca.get('descrizione') or '—'}

Specifiche:
{ricerca.get('specifiche') or '—'}

Accedi alla dashboard CRaC per approvare o rifiutare la richiesta.
    """.strip()

    msg.attach(MIMEText(corpo, "plain"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(EMAIL_FROM, EMAIL_RESPONSABILE, msg.as_string())
    except Exception as e:
        print(f"[Errore invio email] {e}")


def send_email_esito(richiesta: dict, ricerca: dict):
    """Invia email all'osservatore con l'esito della richiesta."""
    if not SMTP_HOST or not SMTP_USER:
        print(f"[SMTP non configurato] Esito richiesta {richiesta['id']}: {richiesta['stato']}")
        return

    stato_label = "✅ APPROVATA" if richiesta['stato'] == 'approvata' else "❌ RIFIUTATA"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[CRaC] Richiesta {stato_label} — {ricerca['nome']}"
    msg["From"]    = EMAIL_FROM
    msg["To"]      = EMAIL_RESPONSABILE  # sostituire con email osservatore quando disponibile

    corpo = f"""
La tua richiesta di tempo telescopio è stata: {stato_label}

Ricerca:         {ricerca['nome']}
Giorno richiesto: {richiesta['giorno_richiesto']}
Note responsabile: {richiesta.get('note_responsabile') or '—'}
    """.strip()

    msg.attach(MIMEText(corpo, "plain"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(EMAIL_FROM, EMAIL_RESPONSABILE, msg.as_string())
    except Exception as e:
        print(f"[Errore invio email esito] {e}")

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
        row = db.execute("SELECT * FROM ricerche WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=f"Ricerca '{body.nome}' già esistente.")


@router.get("/ricerche/{ricerca_id}", response_model=RicercaOut)
def dettaglio_ricerca(ricerca_id: int, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT * FROM ricerche WHERE id = ?", (ricerca_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Ricerca non trovata.")
    return dict(row)

# ─── Endpoint Richieste ───────────────────────────────────────────────────────

@router.get("/richieste", response_model=List[RichiestaOut])
def lista_richieste(
    stato: Optional[str] = None,
    db: sqlite3.Connection = Depends(get_db)
):
    query = """
        SELECT r.*, rc.nome as nome_ricerca
        FROM richieste r
        JOIN ricerche rc ON rc.id = r.ricerca_id
    """
    params = []
    if stato:
        query += " WHERE r.stato = ?"
        params.append(stato)
    query += " ORDER BY r.giorno_richiesto DESC"
    rows = db.execute(query, params).fetchall()
    return [dict(row) for row in rows]


@router.post("/richieste", response_model=RichiestaOut, status_code=201)
def invia_richiesta(body: RichiestaCreate, db: sqlite3.Connection = Depends(get_db)):
    # Verifica che la ricerca esista
    ricerca = db.execute("SELECT * FROM ricerche WHERE id = ?", (body.ricerca_id,)).fetchone()
    if not ricerca:
        raise HTTPException(status_code=404, detail="Ricerca non trovata.")

    # Verifica che non ci sia già una richiesta per quella ricerca in quel giorno
    esistente = db.execute(
        "SELECT id FROM richieste WHERE ricerca_id = ? AND giorno_richiesto = ? AND stato != 'rifiutata'",
        (body.ricerca_id, body.giorno_richiesto)
    ).fetchone()
    if esistente:
        raise HTTPException(status_code=409, detail="Esiste già una richiesta per questa ricerca in quella data.")

    cursor = db.execute(
        """INSERT INTO richieste (ricerca_id, osservatore, co_osservatori, giorno_richiesto)
           VALUES (?, ?, ?, ?)""",
        (body.ricerca_id, body.osservatore.strip(), body.co_osservatori, body.giorno_richiesto)
    )
    db.commit()

    row = db.execute("""
        SELECT r.*, rc.nome as nome_ricerca
        FROM richieste r JOIN ricerche rc ON rc.id = r.ricerca_id
        WHERE r.id = ?
    """, (cursor.lastrowid,)).fetchone()

    richiesta_dict = dict(row)
    ricerca_dict   = dict(ricerca)

    # Notifica email responsabile (non-blocking)
    send_email_notifica(richiesta_dict, ricerca_dict)

    return richiesta_dict


@router.patch("/richieste/{richiesta_id}", response_model=RichiestaOut)
def aggiorna_stato(
    richiesta_id: int,
    body: AggiornamentoStato,
    db: sqlite3.Connection = Depends(get_db)
):
    if body.stato not in ("approvata", "rifiutata"):
        raise HTTPException(status_code=400, detail="Stato non valido. Usare 'approvata' o 'rifiutata'.")

    richiesta = db.execute("SELECT * FROM richieste WHERE id = ?", (richiesta_id,)).fetchone()
    if not richiesta:
        raise HTTPException(status_code=404, detail="Richiesta non trovata.")

    db.execute(
        """UPDATE richieste SET stato = ?, note_responsabile = ?, aggiornata_il = datetime('now')
           WHERE id = ?""",
        (body.stato, body.note_responsabile, richiesta_id)
    )
    db.commit()

    row = db.execute("""
        SELECT r.*, rc.nome as nome_ricerca
        FROM richieste r JOIN ricerche rc ON rc.id = r.ricerca_id
        WHERE r.id = ?
    """, (richiesta_id,)).fetchone()

    richiesta_dict = dict(row)
    ricerca = db.execute("SELECT * FROM ricerche WHERE id = ?", (richiesta_dict['ricerca_id'],)).fetchone()
    send_email_esito(richiesta_dict, dict(ricerca))

    return richiesta_dict


@router.get("/calendario")
def calendario(
    anno:  Optional[int] = None,
    mese:  Optional[int] = None,
    db: sqlite3.Connection = Depends(get_db)
):
    """
    Restituisce tutte le richieste (approvate + in_attesa) per il mese richiesto.
    Se anno/mese non specificati, usa il mese corrente.

    Logica disponibilità date:
      - data con almeno una richiesta 'approvata'  → BLOCCATA
      - data con sole richieste 'in_attesa'         → CONTESA (prenotabile ma con conflitti)
      - data senza richieste                        → LIBERA

    Struttura risposta:
    {
      "anno": 2025, "mese": 6,
      "giorni": {
        "2025-06-12": {
          "stato_giorno": "bloccata" | "contesa" | "libera",
          "richieste": [ { id, osservatore, nome_ricerca, stato, ... } ]
        },
        ...
      }
    }
    """
    oggi = datetime.now()
    anno = anno or oggi.year
    mese = mese or oggi.month

    # Costruisce range del mese
    from calendar import monthrange
    _, giorni_nel_mese = monthrange(anno, mese)
    mese_str  = f"{anno}-{mese:02d}"
    data_inizio = f"{mese_str}-01"
    data_fine   = f"{mese_str}-{giorni_nel_mese:02d}"

    rows = db.execute("""
        SELECT r.id, r.osservatore, r.co_osservatori, r.giorno_richiesto,
               r.stato, r.note_responsabile, r.creata_il,
               rc.id as ricerca_id, rc.nome as nome_ricerca,
               rc.descrizione, rc.specifiche
        FROM richieste r
        JOIN ricerche rc ON rc.id = r.ricerca_id
        WHERE r.giorno_richiesto BETWEEN ? AND ?
          AND r.stato IN ('approvata', 'in_attesa')
        ORDER BY r.giorno_richiesto, r.stato DESC, r.creata_il
    """, (data_inizio, data_fine)).fetchall()

    # Raggruppa per giorno
    giorni: dict = {}
    for row in rows:
        d = row["giorno_richiesto"]
        if d not in giorni:
            giorni[d] = {"stato_giorno": "libera", "richieste": []}
        giorni[d]["richieste"].append({
            "id":              row["id"],
            "osservatore":     row["osservatore"],
            "co_osservatori":  row["co_osservatori"],
            "stato":           row["stato"],
            "nome_ricerca":    row["nome_ricerca"],
            "descrizione":     row["descrizione"],
            "specifiche":      row["specifiche"],
            "note_responsabile": row["note_responsabile"],
            "creata_il":       row["creata_il"],
        })

    # Calcola stato_giorno
    for data, info in giorni.items():
        stati = {r["stato"] for r in info["richieste"]}
        if "approvata" in stati:
            info["stato_giorno"] = "bloccata"
        elif "in_attesa" in stati:
            info["stato_giorno"] = "contesa"

    return {
        "anno":   anno,
        "mese":   mese,
        "giorni": giorni
    }


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
