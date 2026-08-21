"""
Telescope Time Request — FastAPI Router
To include in the main CRaC server with:
    from telescope_time.router import router as telescope_router
    app.include_router(telescope_router)
"""

from fastapi import APIRouter, HTTPException, Depends, Header, Cookie
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, NaiveDatetime, ValidationInfo, computed_field, field_validator
from typing import Literal, Optional, List
from calendar import monthrange
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
import sqlite3
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ─── Config ──────────────────────────────────────────────────────────────────

def db_path() -> str:
    """Database path, read on every call.

    Reading it here instead of at module level lets tests point to a
    temporary file without having to manipulate the environment before
    import.
    """
    return os.environ.get("TELESCOPE_DB_PATH", "telescope_time.db")

def auth_mode() -> str:
    """'forward-auth' (default) or 'dev'.

    In production the identity comes from the headers Nginx receives from
    Authelia (ForwardAuth): the app doesn't handle login or sessions. In
    development 'dev' synthesizes those headers, so Authelia isn't needed.

    Like db_path(), read on every call: tests can switch mode without
    depending on import order.
    """
    return os.environ.get("AUTH_MODE", "forward-auth")

def dev_user() -> str:
    return os.environ.get("DEV_USER", "sviluppo")

def dev_groups() -> str:
    return os.environ.get("DEV_GROUPS", "telescope-responsabili")

def observatory_tz() -> str:
    return os.environ.get("TZ", "Europe/Rome")

def now_at_observatory() -> datetime:
    """'Now' as a naive instant in the observatory's local time, comparable
    with the naive `start`/`end` a `TimeSlot` validates: those have no
    tzinfo by design, so a bare `datetime.now()` here would depend on the
    OS timezone the process happened to pick up at startup, correct only by
    accident when it matches the observatory's. `ZoneInfo(TZ)` resolves it
    explicitly on every call instead.
    """
    return datetime.now(ZoneInfo(observatory_tz())).replace(tzinfo=None)


def to_utc(instant: datetime) -> str:
    """Converts an observatory-local instant to UTC, in the same format
    already used for `created_at`/`updated_at`/`decided_at`.

    Raises `ValueError` if the instant falls in a DST gap (the last Sunday
    of March, when the observatory's clock jumps from 02:00 to 03:00): such
    an instant never happens locally, and converting it round-trips back to
    a different local time — that mismatch is the detection.
    """
    tz = ZoneInfo(observatory_tz())
    aware = instant.replace(tzinfo=tz)
    if aware.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) != instant:
        raise ValueError(
            "Quest'ora non esiste nel fuso dell'osservatorio: "
            "cade nel cambio d'ora di primavera."
        )
    return aware.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_local(instant: str) -> datetime:
    """Converts a UTC instant read from the database back to the
    observatory's local time, naive — the semantics `night_of`, the
    readable time slot and the frontend all expect."""
    return datetime.fromisoformat(instant).astimezone(ZoneInfo(observatory_tz())).replace(tzinfo=None)

def reviewers_group() -> str:
    return os.environ.get("REVIEWERS_GROUP", "telescope-responsabili")

SMTP_HOST     = os.environ.get("SMTP_HOST", "")
SMTP_PORT     = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER     = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
EMAIL_FROM    = os.environ.get("EMAIL_FROM", "crac@osservatorio.it")
REVIEWER_EMAIL = os.environ.get("REVIEWER_EMAIL", "responsabile@osservatorio.it")

# ─── Database ─────────────────────────────────────────────────────────────────

def get_db():
    """SQLite connection private to a single HTTP request.

    timeout=15: how long to wait if another connection is writing, before
    raising "database is locked". With WAL readers never wait, but writes
    stay serialized.

    check_same_thread=False: FastAPI runs the dependency and the handler in
    the threadpool without guaranteeing it's the same thread, and with two
    concurrent requests it sometimes isn't. The connection stays private to
    the single request regardless, so it's never shared across concurrent
    threads.
    """
    conn = sqlite3.connect(db_path(), timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()

# SQLite writes `datetime('now')` as '2026-08-17 06:30:00': UTC, but without
# saying so, and with a space instead of the T. It isn't valid ISO 8601, so
# browsers interpret it as local time and show a time that's off by one hour
# in winter and two in summer.
NOW_UTC = "strftime('%Y-%m-%dT%H:%M:%SZ','now')"

def init_db():
    """Creates the schema if missing and enables WAL journaling.

    WAL lets readers and the writer proceed in parallel instead of blocking
    each other, and it's persisted to the file: it needs enabling only once
    here, unlike `PRAGMA foreign_keys` in get_db(), which isn't.
    """
    conn = sqlite3.connect(db_path())
    conn.execute("PRAGMA journal_mode = WAL")
    cursor = conn.cursor()
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS research_programs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL UNIQUE,
            description TEXT,
            specs       TEXT,
            created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );

        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            -- non-null username = identity verified by Authelia.
            -- NULL for someone known only by name (co-observers, #40).
            username    TEXT    UNIQUE,
            name        TEXT    NOT NULL,
            -- key used to recognize a person already in the registry (#40);
            -- multiple rows can have it NULL.
            email       TEXT    UNIQUE,
            created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );

        CREATE TABLE IF NOT EXISTS time_requests (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            research_program_id INTEGER NOT NULL REFERENCES research_programs(id),
            requester_id        INTEGER NOT NULL REFERENCES users(id),
            co_observers        TEXT,
            -- reference night, derived from the date of `start`: the small
            -- hours belong to the previous night. It's the key the
            -- calendar groups on.
            requested_night     TEXT    NOT NULL,
            -- time slot, UTC: '2026-09-12T20:00:00Z'.
            start               TEXT    NOT NULL,
            end                 TEXT    NOT NULL,
            status              TEXT    NOT NULL DEFAULT 'pending',
            reviewer_notes      TEXT,
            created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            updated_at          TEXT
        );

        -- Two kinds of event in the same table, told apart by `type`: a
        -- single ordered log is what someone reading a request's history
        -- needs. The columns of the other kind stay NULL.
        CREATE TABLE IF NOT EXISTS decision_log (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id        INTEGER NOT NULL REFERENCES time_requests(id),
            type              TEXT    NOT NULL DEFAULT 'decision',
            previous_status   TEXT,
            new_status        TEXT,
            previous_start    TEXT,
            previous_end      TEXT,
            new_start         TEXT,
            new_end           TEXT,
            notes             TEXT,
            decided_by        TEXT,
            decided_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        );
    """)
    conn.commit()
    conn.close()

# ─── Authentication ───────────────────────────────────────────────────────────

class User(BaseModel):
    username: str = Field(description="Authelia username.")
    groups: List[str] = []
    email: Optional[str] = None
    full_name: Optional[str] = Field(None, description="Remote-Name, if Authelia sends it.")
    id: Optional[int] = Field(None, description="Set by registered_user.")

    @property
    def display_name(self) -> str:
        """Name to show: the display name if there is one, otherwise the
        username, which is readable regardless."""
        return self.full_name or self.username

    @computed_field
    @property
    def is_reviewer(self) -> bool:
        return reviewers_group() in self.groups

    @computed_field
    @property
    def is_dev_mode(self) -> bool:
        return auth_mode() == "dev"


def current_user(
    remote_user:   Optional[str] = Header(None, alias="Remote-User"),
    remote_groups: str           = Header("",   alias="Remote-Groups"),
    remote_email:  Optional[str] = Header(None, alias="Remote-Email"),
    remote_name:   Optional[str] = Header(None, alias="Remote-Name"),
    dev_role:      Optional[str] = Cookie(None),
) -> User:
    """User identity, from the headers Nginx sets via Authelia.

    The headers are trustworthy only if the service isn't reachable by
    bypassing Nginx: whoever hits port 8010 directly can claim whatever
    they want. The container must therefore not expose the port externally.
    """
    if auth_mode() == "dev":
        if not remote_user and dev_role == "socio":
            remote_user, remote_groups = "socio-dev", "soci"
            remote_name = remote_name or "Luca Bertani"
        elif not remote_user:
            remote_name = remote_name or "Marta Conti"
        remote_user   = remote_user   or dev_user()
        remote_groups = remote_groups or dev_groups()
        remote_email  = remote_email  or f"{remote_user}@example.test"

    if not remote_user:
        raise HTTPException(status_code=401, detail="Autenticazione richiesta.")

    return User(
        username=remote_user,
        groups=[g.strip() for g in remote_groups.split(",") if g.strip()],
        email=remote_email,
        full_name=remote_name,
    )


def register_user(db: sqlite3.Connection, user: "User") -> int:
    """Aligns the registry with the identity coming from Authelia and
    returns its id.

    Writes only if the record is missing or name/email changed: ordinary
    requests cost a SELECT, not a write.
    """
    row = db.execute(
        "SELECT id, name, email FROM users WHERE username = ?", (user.username,)
    ).fetchone()

    if row is None:
        return _insert_or_reconcile_user(db, user)

    if (row["name"], row["email"]) != (user.display_name, user.email):
        _update_name_and_email(db, row["id"], user)
    return row["id"]


def _insert_or_reconcile_user(db: sqlite3.Connection, user: "User") -> int:
    try:
        cursor = db.execute(
            "INSERT INTO users (username, name, email) VALUES (?, ?, ?)",
            (user.username, user.display_name, user.email),
        )
        db.commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        db.rollback()
        return _reconcile_after_registry_conflict(db, user)


def _reconcile_after_registry_conflict(db: sqlite3.Connection, user: "User") -> int:
    """Another INSERT violated UNIQUE between the initial SELECT and this one:
    username or email is already in the registry for a different reason, to
    be told apart case by case."""
    row = db.execute(
        "SELECT id, name, email FROM users WHERE username = ?", (user.username,)
    ).fetchone()
    if row is not None:
        return row["id"]  # another request from the same user won the race

    co_observer_to_promote = db.execute(
        "SELECT id, username FROM users WHERE email = ? AND username IS NULL", (user.email,)
    ).fetchone()
    if co_observer_to_promote is not None:
        return _promote_co_observer(db, co_observer_to_promote["id"], user)

    return _register_without_email(db, user)


def _promote_co_observer(db: sqlite3.Connection, user_id: int, user: "User") -> int:
    """A co-observer entered by hand (#40), now recognized by email: the
    record gets updated instead of duplicated, so the observations they
    already took part in stay linked to them."""
    db.execute(
        "UPDATE users SET username = ?, name = ? WHERE id = ?",
        (user.username, user.display_name, user_id),
    )
    db.commit()
    return user_id


def _register_without_email(db: sqlite3.Connection, user: "User") -> int:
    """The email already belongs to another verified account — Authelia
    requires them unique, so this is a pathological case. Register without
    an address instead of denying access."""
    print(
        f"[registry] '{user.username}': email {user.email!r} already "
        f"assigned to another verified user, registered without an address",
        flush=True,
    )
    cursor = db.execute(
        "INSERT INTO users (username, name, email) VALUES (?, ?, NULL)",
        (user.username, user.display_name),
    )
    db.commit()
    return cursor.lastrowid


def _update_name_and_email(db: sqlite3.Connection, user_id: int, user: "User") -> None:
    try:
        db.execute(
            "UPDATE users SET name = ?, email = ? WHERE id = ?",
            (user.display_name, user.email, user_id),
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        _update_name_only(db, user_id, user)


def _update_name_only(db: sqlite3.Connection, user_id: int, user: "User") -> None:
    """The email moved to another account: only the name gets aligned here."""
    db.execute(
        "UPDATE users SET name = ? WHERE id = ?",
        (user.display_name, user_id),
    )
    db.commit()


def registered_user(
    user: User = Depends(current_user),
    db: sqlite3.Connection = Depends(get_db),
) -> User:
    """Current user, with the id of their record in the registry."""
    user.id = register_user(db, user)
    return user


def reviewers_only(user: User = Depends(current_user)) -> User:
    if not user.is_reviewer:
        raise HTTPException(
            status_code=403,
            detail=f"Operazione riservata al gruppo '{reviewers_group()}'.",
        )
    return user


router = APIRouter(
    prefix="/telescope-time",
    tags=["Telescope Time"],
    dependencies=[Depends(registered_user)],
)

# ─── Pydantic models ──────────────────────────────────────────────────────────

class ResearchProgramCreate(BaseModel):
    name: str
    description: Optional[str] = None
    specs: Optional[str] = None

class ResearchProgramOut(BaseModel):
    id: int
    name: str
    description: Optional[str]
    specs: Optional[str]
    created_at: str

# Noon is the conventional threshold in astronomy — it's also where the
# Julian day cuts over — and it's far from any real observation time, so no
# session ever lands on it by chance.
NIGHT_THRESHOLD = time(12, 0)

def night_of(instant: datetime) -> date:
    day = instant.date()
    if instant.time() < NIGHT_THRESHOLD:
        day -= timedelta(days=1)
    return day

class TimeSlot(BaseModel):
    """start/end as NaiveDatetime: rejects instants with a timezone, because
    they're observatory local time and an offset would make the stored
    slots no longer comparable with each other."""
    start: NaiveDatetime
    end: NaiveDatetime

    @field_validator("start", "end")
    @classmethod
    def to_the_second(cls, instant: datetime) -> datetime:
        """A single format is what makes it legitimate to compare slots as
        strings, in SQL as in Python."""
        return instant.replace(microsecond=0)

    @field_validator("start", "end")
    @classmethod
    def exists_at_the_observatory(cls, instant: datetime) -> datetime:
        """Rejects an instant the DST gap skips (the last Sunday of March):
        `to_utc` would otherwise silently shift it by an hour."""
        to_utc(instant)
        return instant

    @field_validator("end")
    @classmethod
    def after_start(cls, instant: datetime, info: ValidationInfo) -> datetime:
        start = info.data.get("start")
        if start is not None and instant <= start:
            raise ValueError("La fine deve essere successiva all'inizio.")
        return instant

    @field_validator("end")
    @classmethod
    def within_one_night(cls, instant: datetime, info: ValidationInfo) -> datetime:
        start = info.data.get("start")
        if start is None:
            return instant
        window_end = datetime.combine(night_of(start) + timedelta(days=1), NIGHT_THRESHOLD)
        if instant > window_end:
            raise ValueError(
                "La fine deve stare nella stessa notte dell'inizio: "
                "non oltre le 12:00 del giorno successivo."
            )
        return instant

    @property
    def night(self) -> str:
        return night_of(self.start).isoformat()


class TimeRequestCreate(TimeSlot):
    """No `observer` field: identity comes from Authelia, not the body, so
    it can't be forged. A field with that name sent in the body is
    ignored."""
    research_program_id: int
    co_observers: Optional[str] = None

    @field_validator("start")
    @classmethod
    def in_the_future(cls, instant: datetime) -> datetime:
        if instant <= now_at_observatory():
            raise ValueError("L'osservazione deve cominciare nel futuro.")
        return instant


class RescheduleRequest(TimeSlot):
    """No future constraint: the reviewer can also log an observation that
    already happened, after the fact. That the date is in the past gets
    stated, not prevented."""
    reason: Optional[str] = None

class StatusUpdate(BaseModel):
    status: Literal["approved", "rejected"]
    reviewer_notes: Optional[str] = None

class LogEntryOut(BaseModel):
    """A history entry: either a decision or a reschedule. The fields of
    the other kind are null."""
    id: int
    request_id: int
    type: str
    previous_status: Optional[str]
    new_status: Optional[str]
    previous_start: Optional[str]
    previous_end: Optional[str]
    new_start: Optional[str]
    new_end: Optional[str]
    notes: Optional[str]
    decided_by: Optional[str]
    decided_at: str

class TimeRequestOut(BaseModel):
    id: int
    research_program_id: int
    requester_id: int
    research_program_name: str
    observer: str
    co_observers: Optional[str]
    requested_night: str
    start: str
    end: str
    status: str
    reviewer_notes: Optional[str]
    created_at: str
    updated_at: Optional[str]

class ObservatoryOut(BaseModel):
    timezone: str

# ─── Email utility ────────────────────────────────────────────────────────────

def send_message(recipient: str, subject: str, body: str):
    """Single sending point. Without SMTP configured, it just logs.

    Isolating it here makes it possible to verify *to whom* a message is
    sent without a mail server, and it's the spot to touch when sending
    moves to BackgroundTasks (#8).
    """
    if not SMTP_HOST or not SMTP_USER:
        print(f"[SMTP non configurato] a {recipient}: {subject}", flush=True)
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = EMAIL_FROM
    msg["To"]      = recipient
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(EMAIL_FROM, recipient, msg.as_string())
    except Exception as e:
        print(f"[Errore invio email] a {recipient}: {e}", flush=True)


def readable_time_slot(request: dict) -> str:
    """'12/09/2026 22:00 → 13/09/2026 01:00', without repeating the date
    when the session doesn't cross midnight. `start`/`end` are UTC on the
    row; this is the observatory-local rendering a human reads."""
    start = to_local(request["start"])
    end = to_local(request["end"])
    end_format = "%H:%M" if start.date() == end.date() else "%d/%m/%Y %H:%M"
    return f"{start:%d/%m/%Y %H:%M} → {end:{end_format}}"


def send_notification_email(request: dict, research_program: dict):
    body = f"""
Nuova richiesta tempo telescopio ricevuta.

Osservatore:      {request['observer']}
Co-osservatori:   {request['co_observers'] or '—'}
Ricerca:          {research_program['name']}
Fascia oraria:    {readable_time_slot(request)}

Descrizione ricerca:
{research_program['description'] or '—'}

Specifiche:
{research_program['specs'] or '—'}

Accedi alla dashboard CRaC per approvare o rifiutare la richiesta.
    """.strip()

    send_message(
        REVIEWER_EMAIL,
        f"[CRaC] Nuova richiesta tempo telescopio — {research_program['name']}",
        body,
    )


def send_outcome_email(request: dict):
    """The address comes from the registry, which takes it from Authelia.
    If it's missing, the notice goes to the reviewer, who at least knows
    they need to relay it in person."""
    outcome = "✅ APPROVATA" if request["status"] == "approved" else "❌ RIFIUTATA"
    body = f"""
La tua richiesta di tempo telescopio è stata: {outcome}

Osservatore:       {request['observer']}
Ricerca:           {request['research_program_name']}
Fascia oraria:     {readable_time_slot(request)}
Note responsabile: {request['reviewer_notes'] or '—'}
    """.strip()

    send_message(
        request["observer_email"] or REVIEWER_EMAIL,
        f"[CRaC] Richiesta {outcome} — {request['research_program_name']}",
        body,
    )

def send_reschedule_email(request: dict, previous: dict, reason: Optional[str]):
    """The observer got assigned a different time than requested: not
    something they should stumble on by chance opening the calendar."""
    warning = ""
    if to_local(request["start"]) < now_at_observatory():
        warning = "\n\nAttenzione: la nuova fascia cade in una data passata."

    body = f"""
La tua osservazione è stata riprogrammata dal responsabile.

Osservatore:  {request['observer']}
Ricerca:      {request['research_program_name']}
Prima:        {readable_time_slot(previous)}
Adesso:       {readable_time_slot(request)}
Motivo:       {reason or '—'}{warning}
    """.strip()

    send_message(
        request["observer_email"] or REVIEWER_EMAIL,
        f"[CRaC] Osservazione riprogrammata — {request['research_program_name']}",
        body,
    )

# ─── User endpoint ─────────────────────────────────────────────────────────────

@router.get("/me", response_model=User)
def me(user: User = Depends(registered_user)):
    """Identity of the connected user: lets the pages know whether to show
    the review commands."""
    return user

@router.get("/observatory", response_model=ObservatoryOut)
def observatory():
    """The observatory's timezone, so the frontend can compute "now" the
    same way the server does instead of using the visiting browser's own
    timezone."""
    return ObservatoryOut(timezone=observatory_tz())

# ─── Research programs endpoints ───────────────────────────────────────────────

@router.get("/research-programs", response_model=List[ResearchProgramOut])
def list_research_programs(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute("SELECT * FROM research_programs ORDER BY name").fetchall()
    return [dict(r) for r in rows]


@router.post("/research-programs", response_model=ResearchProgramOut, status_code=201)
def create_research_program(body: ResearchProgramCreate, db: sqlite3.Connection = Depends(get_db)):
    try:
        cursor = db.execute(
            "INSERT INTO research_programs (name, description, specs) VALUES (?, ?, ?)",
            (body.name.strip(), body.description, body.specs)
        )
        db.commit()
        return read_research_program(db, cursor.lastrowid)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=f"Ricerca '{body.name}' già esistente.")


@router.get("/research-programs/{research_program_id}", response_model=ResearchProgramOut)
def research_program_detail(research_program_id: int, db: sqlite3.Connection = Depends(get_db)):
    return read_research_program(db, research_program_id)

# ─── Time requests endpoints ────────────────────────────────────────────────────

def month_bounds(year: int, month: int) -> tuple[str, str]:
    last_day = monthrange(year, month)[1]
    return f"{year}-{month:02d}-01", f"{year}-{month:02d}-{last_day:02d}"


FULL_TIME_REQUESTS = """
    SELECT r.*, rp.name as research_program_name,
           u.name as observer, u.email as observer_email
    FROM time_requests r
    JOIN research_programs rp ON rp.id = r.research_program_id
    JOIN users             u  ON u.id  = r.requester_id
"""


REQUEST_NOT_FOUND = "Richiesta non trovata."


def read_request(db: sqlite3.Connection, request_id: int) -> dict:
    row = db.execute(f"{FULL_TIME_REQUESTS} WHERE r.id = ?", (request_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=REQUEST_NOT_FOUND)
    return dict(row)


def localized(request: dict) -> dict:
    """The request as the API exposes it: `start`/`end` in observatory
    local time, not the UTC stored on the row. Kept out of `read_request`
    itself, whose UTC values still feed `time_slot_conflict`."""
    return {**request, "start": to_local(request["start"]).isoformat(),
            "end": to_local(request["end"]).isoformat()}


def verify_request_exists(db: sqlite3.Connection, request_id: int) -> None:
    if db.execute("SELECT 1 FROM time_requests WHERE id = ?", (request_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail=REQUEST_NOT_FOUND)


def already_approved_at_same_time(
    db: sqlite3.Connection, request_id: int, start: str, end: str
) -> Optional[dict]:
    """Another approved request occupying the instrument at the same
    instants. Two programs can share the night, not the instant."""
    row = db.execute(
        f"""{FULL_TIME_REQUESTS}
            WHERE r.status = 'approved' AND r.id != ?
              AND r.start < ? AND ? < r.end""",
        (request_id, end, start),
    ).fetchone()
    return dict(row) if row else None


def lock_for_write(db: sqlite3.Connection) -> None:
    """Opens an exclusive transaction right away, instead of waiting for
    the first write like SQLite would do on its own.

    Between the conflict check and the UPDATE there's a window: without
    this, two simultaneous approvals both cross it and create exactly the
    overlap the constraint exists to prevent.
    """
    db.execute("BEGIN IMMEDIATE")


def time_slot_conflict(db: sqlite3.Connection, request_id: int, start: str, end: str):
    occupied = already_approved_at_same_time(db, request_id, start, end)
    if occupied:
        raise HTTPException(
            status_code=409,
            detail=f"La fascia si sovrappone alla richiesta #{occupied['id']} "
                   f"({occupied['research_program_name']}, {readable_time_slot(occupied)}), "
                   f"già approvata.",
        )


def log_event(db: sqlite3.Connection, request_id: int, type: str,
              author: str, notes: Optional[str], **values) -> None:
    columns = ", ".join(values)
    placeholders = ", ".join("?" * len(values))
    db.execute(
        f"""INSERT INTO decision_log
                (request_id, type, decided_by, notes, {columns})
            VALUES (?, ?, ?, ?, {placeholders})""",
        (request_id, type, author, notes, *values.values()),
    )


def read_research_program(db: sqlite3.Connection, research_program_id: int) -> dict:
    row = db.execute("SELECT * FROM research_programs WHERE id = ?", (research_program_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Ricerca non trovata.")
    return dict(row)


@router.get("/requests", response_model=List[TimeRequestOut])
def list_requests(
    status: Optional[str] = None,
    db: sqlite3.Connection = Depends(get_db)
):
    filter_clause = " WHERE r.status = ?" if status else ""
    rows = db.execute(
        f"{FULL_TIME_REQUESTS}{filter_clause} ORDER BY r.start DESC",
        [status] if status else [],
    ).fetchall()
    return [localized(dict(row)) for row in rows]


@router.get("/requests/{request_id}", response_model=TimeRequestOut)
def request_detail(request_id: int, db: sqlite3.Connection = Depends(get_db)):
    return localized(read_request(db, request_id))


@router.post("/requests", response_model=TimeRequestOut, status_code=201)
def submit_request(
    body: TimeRequestCreate,
    db: sqlite3.Connection = Depends(get_db),
    user: User = Depends(registered_user),
):
    research_program = read_research_program(db, body.research_program_id)

    if db.execute(
        "SELECT 1 FROM time_requests WHERE research_program_id = ? AND requested_night = ? AND status != 'rejected'",
        (body.research_program_id, body.night),
    ).fetchone():
        raise HTTPException(
            status_code=409,
            detail="Esiste già una richiesta per questa ricerca in quella notte.",
        )

    cursor = db.execute(
        """INSERT INTO time_requests
               (research_program_id, requester_id, co_observers, requested_night, start, end)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (body.research_program_id, user.id, body.co_observers, body.night,
         to_utc(body.start), to_utc(body.end)),
    )
    db.commit()

    request = read_request(db, cursor.lastrowid)
    send_notification_email(request, research_program)
    return localized(request)


def notes_or_existing(body: StatusUpdate, request: dict) -> Optional[str]:
    """Notes already written must not get lost when the PATCH doesn't
    include them."""
    return body.reviewer_notes if body.reviewer_notes is not None else request["reviewer_notes"]


@router.patch("/requests/{request_id}", response_model=TimeRequestOut)
def update_status(
    request_id: int,
    body: StatusUpdate,
    db: sqlite3.Connection = Depends(get_db),
    user: User = Depends(reviewers_only),
):
    lock_for_write(db)
    request = read_request(db, request_id)
    previous_status = request["status"]
    status_changes = body.status != previous_status

    if body.status == "approved" and status_changes:
        time_slot_conflict(db, request_id, request["start"], request["end"])

    notes = notes_or_existing(body, request)

    if status_changes:
        log_event(
            db, request_id, "decision", user.username, body.reviewer_notes,
            previous_status=previous_status, new_status=body.status,
        )

    db.execute(
        f"""UPDATE time_requests SET status = ?, reviewer_notes = ?, updated_at = {NOW_UTC}
            WHERE id = ?""",
        (body.status, notes, request_id)
    )
    db.commit()

    updated = read_request(db, request_id)
    if status_changes:
        send_outcome_email(updated)
    return localized(updated)


@router.patch("/requests/{request_id}/schedule", response_model=TimeRequestOut)
def reschedule_request(
    request_id: int,
    body: RescheduleRequest,
    db: sqlite3.Connection = Depends(get_db),
    user: User = Depends(registered_user),
):
    """Reschedules a request, pending or already approved.

    Separate from the status PATCH because they're two distinct actions:
    one decides, the other reschedules, and keeping them together would
    make it ambiguous what to log in the history.

    The reviewer reschedules without restrictions; whoever created it can
    reschedule only while it's pending and only into the future.
    """
    lock_for_write(db)
    request = read_request(db, request_id)

    if not user.is_reviewer:
        if request["requester_id"] != user.id:
            raise HTTPException(
                status_code=403,
                detail="Solo il responsabile o chi ha creato la richiesta può spostarla.",
            )
        if request["status"] != "pending":
            raise HTTPException(
                status_code=409,
                detail="Solo le richieste in attesa possono essere spostate da chi le ha create.",
            )
        if body.start <= now_at_observatory():
            raise HTTPException(status_code=422, detail="Il nuovo inizio deve essere nel futuro.")

    start, end = to_utc(body.start), to_utc(body.end)
    if (start, end) == (request["start"], request["end"]):
        return localized(request)

    if request["status"] == "approved":
        time_slot_conflict(db, request_id, start, end)

    log_event(
        db, request_id, "reschedule", user.username, body.reason,
        previous_start=request["start"], previous_end=request["end"],
        new_start=start, new_end=end,
    )
    db.execute(
        f"""UPDATE time_requests
               SET requested_night = ?, start = ?, end = ?, updated_at = {NOW_UTC}
             WHERE id = ?""",
        (body.night, start, end, request_id),
    )
    db.commit()

    rescheduled = read_request(db, request_id)
    send_reschedule_email(rescheduled, request, body.reason)
    return localized(rescheduled)


def localized_log_entry(entry: dict) -> dict:
    """`previous_start`/`previous_end`/`new_start`/`new_end` are UTC on a
    reschedule row, NULL on a decision row."""
    reschedule_fields = ("previous_start", "previous_end", "new_start", "new_end")
    return {
        **entry,
        **{field: to_local(entry[field]).isoformat()
           for field in reschedule_fields if entry[field] is not None},
    }


@router.get("/requests/{request_id}/history", response_model=List[LogEntryOut])
def request_history(request_id: int, db: sqlite3.Connection = Depends(get_db)):
    """Decisions made on a request, from oldest to most recent."""
    verify_request_exists(db, request_id)
    rows = db.execute(
        "SELECT * FROM decision_log WHERE request_id = ? ORDER BY id",
        (request_id,)
    ).fetchall()
    return [localized_log_entry(dict(r)) for r in rows]


def record_overlaps(requests: List[dict], nights: dict) -> set:
    """Annotates on each night the pairs of requests whose slots intersect
    and returns the nights where the overlap involves only 'pending'
    requests (contested).

    Requests arrive ordered by `start`: as soon as one starts after `a`'s
    end, every one that follows does too, and the comparison for that `a`
    can stop.
    """
    contested = set()
    for position, a in enumerate(requests):
        for b in requests[position + 1:]:
            if b["start"] >= a["end"]:
                break
            nights_pair = {a["requested_night"], b["requested_night"]}
            for night in nights_pair:
                nights[night]["overlaps"].append([a["id"], b["id"]])
            if a["status"] == b["status"] == "pending":
                contested |= nights_pair
    return contested


@router.get("/calendar")
def calendar(
    year:  Optional[int] = None,
    month: Optional[int] = None,
    db: sqlite3.Connection = Depends(get_db)
):
    """Approved and pending requests for the month, grouped by night.

    Each night reports `night_status` (`pending`, `contested`, `booked`), the
    counts `approved_count` and `pending_count`, the pairs of requests whose
    slots intersect and the list of requests. Free nights don't appear.

    Contested is the night where two not-yet-approved requests are
    disputing the same instants: two sessions in distinct shifts share the
    night without contesting it.
    """
    today = now_at_observatory()
    year = year or today.year
    month = month or today.month
    first, last = month_bounds(year, month)

    rows = db.execute("""
        SELECT r.id, u.name as observer, r.co_observers, r.requested_night,
               r.start, r.end, r.status, r.reviewer_notes, r.created_at,
               rp.id as research_program_id, rp.name as research_program_name,
               rp.description, rp.specs
        FROM time_requests r
        JOIN research_programs rp ON rp.id = r.research_program_id
        JOIN users             u  ON u.id  = r.requester_id
        WHERE r.requested_night BETWEEN ? AND ?
          AND r.status IN ('approved', 'pending')
        ORDER BY r.start, r.created_at
    """, (first, last)).fetchall()

    requests = [dict(row) for row in rows]
    nights: dict = {}
    for request in requests:
        night = nights.setdefault(
            request["requested_night"],
            {"night_status": "pending", "approved_count": 0, "pending_count": 0,
             "overlaps": [], "requests": []},
        )
        night["requests"].append(request)
        night["approved_count" if request["status"] == "approved" else "pending_count"] += 1

    contested = record_overlaps(requests, nights)

    for request in requests:
        request.update(localized(request))

    for key, night in nights.items():
        if night["approved_count"]:
            night["night_status"] = "booked"
        elif key in contested:
            night["night_status"] = "contested"

    return {"year": year, "month": month, "nights": nights}


@router.get("/statistics")
def statistics(db: sqlite3.Connection = Depends(get_db)):
    """Bonus endpoint for aggregate statistics — useful for future work."""
    totals = db.execute("""
        SELECT status, COUNT(*) as count FROM time_requests GROUP BY status
    """).fetchall()

    by_research_program = db.execute("""
        SELECT rp.name, COUNT(r.id) as request_count,
               SUM(CASE WHEN r.status='approved' THEN 1 ELSE 0 END) as approved_count
        FROM research_programs rp
        LEFT JOIN time_requests r ON r.research_program_id = rp.id
        GROUP BY rp.id ORDER BY request_count DESC
    """).fetchall()

    return {
        "by_status": [dict(r) for r in totals],
        "by_research_program": [dict(r) for r in by_research_program]
    }
