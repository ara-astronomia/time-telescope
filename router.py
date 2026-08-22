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
from sqlalchemy import create_engine, event, text, select, func, case
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import IntegrityError
from models import Base, Research, User, Request, DecisionLog, now_utc_string
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

def database_url() -> str:
    """SQLAlchemy engine URL. Defaults to SQLite built from `db_path()`, so
    nothing changes for anyone who only ever set `TELESCOPE_DB_PATH`;
    setting `DATABASE_URL` directly points the app at a different engine
    entirely (e.g. `mysql+pymysql://user:pw@host/telescope_time` for
    MariaDB, which speaks the MySQL wire protocol).
    """
    return os.environ.get("DATABASE_URL", f"sqlite:///{db_path()}")

def auth_mode() -> str:
    """'forward-auth' (default) or 'dev'.

    In production the identity comes from the headers Nginx receives from
    Authelia (ForwardAuth): the app doesn't handle login or sessions. In
    development 'dev' synthesizes those headers, so Authelia isn't needed.

    Like db_path(), read on every call: tests can switch mode without
    depending on import order.
    """
    return os.environ.get("AUTH_MODE", "forward-auth")

def auto_seed() -> bool:
    """Whether an empty database gets sample data at startup — see
    `seed.py` and `main.py`'s lifespan. On by default for `AUTH_MODE=dev`;
    the test suite turns it off explicitly (its own fixtures need every
    test to start from a database that's actually empty, not one already
    holding sample data)."""
    return os.environ.get("AUTO_SEED", "true") != "false"

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

engine = None
SessionLocal = None
_engine_url = None


def _ensure_engine():
    """(Re)builds the engine when `DATABASE_URL` has changed since the last
    call — otherwise a no-op, reusing the existing engine/pool.

    Rebuilding only on change, rather than once at startup, matters for
    more than tidiness: `router.engine` is a module-level global, shared by
    every FastAPI app instance in the process — including a long-lived one
    a test suite might keep running in a background thread (`app_url` in
    conftest.py) alongside many short-lived ones on their own throwaway
    databases. Building the engine once at startup and never again left
    that background instance holding a stale engine pointed at a database
    a later, unrelated test had already dropped — the same class of bug
    the old `sqlite3.connect(db_path(), ...)` per-call design in `get_db()`
    never had, because it read the environment fresh on every connection
    instead of caching anything.
    """
    global engine, SessionLocal, _engine_url
    url = database_url()
    if url == _engine_url:
        return

    connect_args = {"check_same_thread": False, "timeout": 15} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args)

    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def _sqlite_connect(dbapi_connection, _):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.close()

    SessionLocal = sessionmaker(bind=engine)
    _engine_url = url


def init_db():
    """Builds the engine from `DATABASE_URL` and creates the schema if
    missing.

    SQLite gets two PRAGMAs no other dialect needs, applied on every new
    physical connection via the `connect` event (harmless to repeat,
    unlike `journal_mode` which only truly needs setting once — simpler to
    fold both into one place than to keep two separate mechanisms):
    - `PRAGMA foreign_keys = ON`, off by default;
    - `PRAGMA journal_mode = WAL`, so readers and the writer proceed in
      parallel instead of blocking each other.

    pysqlite's own implicit-transaction handling is left untouched
    (unlike a common recipe that disables it to control `BEGIN` globally):
    it only auto-opens a transaction before a write statement, never
    before a plain read, which is exactly why `lock_for_write` can issue
    `BEGIN IMMEDIATE` itself as a plain statement — nothing has opened a
    transaction yet at that point in the request. Overriding this
    globally instead once made ordinary concurrent reads (`test_concurrency.py
    ::test_simultaneous_calls_do_not_fail`) fail with "database is locked":
    every read held an open transaction for the request's full duration
    instead of releasing it right after the SELECT.
    """
    _ensure_engine()
    Base.metadata.create_all(engine)


def get_db():
    """SQLAlchemy session private to a single HTTP request — same shape as
    the SQLite connection it replaces: opened fresh, closed in `finally`."""
    _ensure_engine()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ─── Authentication ───────────────────────────────────────────────────────────

class Identity(BaseModel):
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
) -> Identity:
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

    return Identity(
        username=remote_user,
        groups=[g.strip() for g in remote_groups.split(",") if g.strip()],
        email=remote_email,
        full_name=remote_name,
    )


def register_user(db: Session, user: "Identity") -> int:
    """Aligns the registry with the identity coming from Authelia and
    returns its id.

    Writes only if the record is missing or name/email changed: ordinary
    requests cost a SELECT, not a write.
    """
    record = db.scalar(select(User).where(User.username == user.username))

    if record is None:
        return _insert_or_reconcile_user(db, user)

    if (record.name, record.email) != (user.display_name, user.email):
        _update_name_and_email(db, record.id, user)
    return record.id


def _insert_or_reconcile_user(db: Session, user: "Identity") -> int:
    try:
        record = User(username=user.username, name=user.display_name, email=user.email)
        db.add(record)
        db.commit()
        return record.id
    except IntegrityError:
        db.rollback()
        return _reconcile_after_registry_conflict(db, user)


def _reconcile_after_registry_conflict(db: Session, user: "Identity") -> int:
    """Another INSERT violated UNIQUE between the initial SELECT and this one:
    username or email is already in the registry for a different reason, to
    be told apart case by case."""
    record = db.scalar(select(User).where(User.username == user.username))
    if record is not None:
        return record.id  # another request from the same user won the race

    co_observer_to_promote = db.scalar(
        select(User).where(User.email == user.email, User.username.is_(None))
    )
    if co_observer_to_promote is not None:
        return _promote_co_observer(db, co_observer_to_promote.id, user)

    return _register_without_email(db, user)


def _promote_co_observer(db: Session, user_id: int, user: "Identity") -> int:
    """A co-observer entered by hand (#40), now recognized by email: the
    record gets updated instead of duplicated, so the observations they
    already took part in stay linked to them."""
    record = db.get(User, user_id)
    record.username = user.username
    record.name = user.display_name
    db.commit()
    return user_id


def _register_without_email(db: Session, user: "Identity") -> int:
    """The email already belongs to another verified account — Authelia
    requires them unique, so this is a pathological case. Register without
    an address instead of denying access."""
    print(
        f"[registry] '{user.username}': email {user.email!r} already "
        f"assigned to another verified user, registered without an address",
        flush=True,
    )
    record = User(username=user.username, name=user.display_name, email=None)
    db.add(record)
    db.commit()
    return record.id


def _update_name_and_email(db: Session, user_id: int, user: "Identity") -> None:
    try:
        record = db.get(User, user_id)
        record.name = user.display_name
        record.email = user.email
        db.commit()
    except IntegrityError:
        db.rollback()
        _update_name_only(db, user_id, user)


def _update_name_only(db: Session, user_id: int, user: "Identity") -> None:
    """The email moved to another account: only the name gets aligned here."""
    record = db.get(User, user_id)
    record.name = user.display_name
    db.commit()


def registered_user(
    user: Identity = Depends(current_user),
    db: Session = Depends(get_db),
) -> Identity:
    """Current user, with the id of their record in the registry."""
    user.id = register_user(db, user)
    return user


def reviewers_only(user: Identity = Depends(current_user)) -> Identity:
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

@router.get("/me", response_model=Identity)
def me(user: Identity = Depends(registered_user)):
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

def program_as_dict(program: Research) -> dict:
    return {"id": program.id, "name": program.name, "description": program.description,
            "specs": program.specs, "created_at": program.created_at}


def read_research_program(db: Session, research_program_id: int) -> dict:
    program = db.get(Research, research_program_id)
    if program is None:
        raise HTTPException(status_code=404, detail="Ricerca non trovata.")
    return program_as_dict(program)


@router.get("/research-programs", response_model=List[ResearchProgramOut])
def list_research_programs(db: Session = Depends(get_db)):
    programs = db.scalars(select(Research).order_by(Research.name)).all()
    return [program_as_dict(p) for p in programs]


@router.post("/research-programs", response_model=ResearchProgramOut, status_code=201)
def create_research_program(body: ResearchProgramCreate, db: Session = Depends(get_db)):
    try:
        program = Research(name=body.name.strip(), description=body.description, specs=body.specs)
        db.add(program)
        db.commit()
        return program_as_dict(program)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Ricerca '{body.name}' già esistente.")


@router.get("/research-programs/{research_program_id}", response_model=ResearchProgramOut)
def research_program_detail(research_program_id: int, db: Session = Depends(get_db)):
    return read_research_program(db, research_program_id)

# ─── Time requests endpoints ────────────────────────────────────────────────────

def month_bounds(year: int, month: int) -> tuple[str, str]:
    last_day = monthrange(year, month)[1]
    return f"{year}-{month:02d}-01", f"{year}-{month:02d}-{last_day:02d}"


REQUEST_NOT_FOUND = "Richiesta non trovata."


def request_as_dict(request: Request) -> dict:
    """The eager-loaded `research_program`/`requester` relationships take
    the place of the hand-written JOIN this used to be."""
    return {
        "id": request.id,
        "research_program_id": request.research_program_id,
        "requester_id": request.requester_id,
        "co_observers": request.co_observers,
        "requested_night": request.requested_night.isoformat(),
        "start": request.start,
        "end": request.end,
        "status": request.status,
        "reviewer_notes": request.reviewer_notes,
        "created_at": request.created_at,
        "updated_at": request.updated_at,
        "research_program_name": request.research_program.name,
        "observer": request.requester.name,
        "observer_email": request.requester.email,
    }


def get_request_or_404(db: Session, request_id: int) -> Request:
    request = db.get(Request, request_id)
    if request is None:
        raise HTTPException(status_code=404, detail=REQUEST_NOT_FOUND)
    return request


def read_request(db: Session, request_id: int) -> dict:
    return request_as_dict(get_request_or_404(db, request_id))


def localized(request: dict) -> dict:
    """The request as the API exposes it: `start`/`end` in observatory
    local time, not the UTC stored on the row. Kept out of `read_request`
    itself, whose UTC values still feed `time_slot_conflict`."""
    return {**request, "start": to_local(request["start"]).isoformat(),
            "end": to_local(request["end"]).isoformat()}


def verify_request_exists(db: Session, request_id: int) -> None:
    if db.get(Request, request_id) is None:
        raise HTTPException(status_code=404, detail=REQUEST_NOT_FOUND)


def already_approved_at_same_time(
    db: Session, request_id: int, start: str, end: str
) -> Optional[dict]:
    """Another approved request occupying the instrument at the same
    instants. Two programs can share the night, not the instant."""
    request = db.scalar(
        select(Request).where(
            Request.status == "approved",
            Request.id != request_id,
            Request.start < end,
            Request.end > start,
        )
    )
    return request_as_dict(request) if request else None


def lock_for_write(db: Session) -> None:
    """Opens the transaction with an isolation strong enough to close the
    window between the conflict check and the write: without this, two
    simultaneous approvals/reschedules both cross it and create exactly the
    overlap the constraint exists to prevent.

    SQLite: `BEGIN IMMEDIATE` claims the write lock right away instead of
    waiting for the first write, exactly like the raw-sqlite3 code before
    it. Issued as a plain statement, not through a global "begin" override
    (see `init_db`): pysqlite hasn't opened a transaction of its own yet at
    this point, since only reads happened so far this request.

    Other dialects (MariaDB/MySQL): the default isolation (REPEATABLE READ)
    doesn't lock the *absence* of a row, so two transactions that both see
    "no overlap yet" can both proceed — SERIALIZABLE makes InnoDB take the
    gap locks that close that phantom-read window.

    `db.commit()` first, on both branches: the router-level `registered_user`
    dependency already read from `db` before the endpoint body runs, so a
    connection/transaction is already bound to this session — a SQLite
    `BEGIN IMMEDIATE` would error ("already in a transaction"), and
    `execution_options(isolation_level=...)` on an already-bound connection
    is silently ignored rather than applied. Nothing to lose by ending that
    read-only transaction first: it never wrote anything.
    """
    db.commit()
    if db.get_bind().dialect.name == "sqlite":
        db.execute(text("BEGIN IMMEDIATE"))
    else:
        db.connection(execution_options={"isolation_level": "SERIALIZABLE"})


def time_slot_conflict(db: Session, request_id: int, start: str, end: str):
    occupied = already_approved_at_same_time(db, request_id, start, end)
    if occupied:
        raise HTTPException(
            status_code=409,
            detail=f"La fascia si sovrappone alla richiesta #{occupied['id']} "
                   f"({occupied['research_program_name']}, {readable_time_slot(occupied)}), "
                   f"già approvata.",
        )


def log_event(db: Session, request_id: int, type: str,
              author: str, notes: Optional[str], **values) -> None:
    db.add(DecisionLog(request_id=request_id, type=type, decided_by=author, notes=notes, **values))


@router.get("/requests", response_model=List[TimeRequestOut])
def list_requests(
    status: Optional[str] = None,
    db: Session = Depends(get_db)
):
    query = select(Request).order_by(Request.start.desc())
    if status:
        query = query.where(Request.status == status)
    requests = db.scalars(query).all()
    return [localized(request_as_dict(r)) for r in requests]


@router.get("/requests/{request_id}", response_model=TimeRequestOut)
def request_detail(request_id: int, db: Session = Depends(get_db)):
    return localized(read_request(db, request_id))


@router.post("/requests", response_model=TimeRequestOut, status_code=201)
def submit_request(
    body: TimeRequestCreate,
    db: Session = Depends(get_db),
    user: Identity = Depends(registered_user),
):
    research_program = read_research_program(db, body.research_program_id)

    duplicate = db.scalar(
        select(Request).where(
            Request.research_program_id == body.research_program_id,
            Request.requested_night == date.fromisoformat(body.night),
            Request.status != "rejected",
        )
    )
    if duplicate:
        raise HTTPException(
            status_code=409,
            detail="Esiste già una richiesta per questa ricerca in quella notte.",
        )

    time_request = Request(
        research_program_id=body.research_program_id,
        requester_id=user.id,
        co_observers=body.co_observers,
        requested_night=date.fromisoformat(body.night),
        start=to_utc(body.start),
        end=to_utc(body.end),
    )
    db.add(time_request)
    db.commit()

    request = request_as_dict(time_request)
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
    db: Session = Depends(get_db),
    user: Identity = Depends(reviewers_only),
):
    lock_for_write(db)
    time_request = get_request_or_404(db, request_id)
    request = request_as_dict(time_request)
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

    time_request.status = body.status
    time_request.reviewer_notes = notes
    db.commit()

    updated = request_as_dict(time_request)
    if status_changes:
        send_outcome_email(updated)
    return localized(updated)


@router.patch("/requests/{request_id}/schedule", response_model=TimeRequestOut)
def reschedule_request(
    request_id: int,
    body: RescheduleRequest,
    db: Session = Depends(get_db),
    user: Identity = Depends(registered_user),
):
    """Reschedules a request, pending or already approved.

    Separate from the status PATCH because they're two distinct actions:
    one decides, the other reschedules, and keeping them together would
    make it ambiguous what to log in the history.

    The reviewer reschedules without restrictions; whoever created it can
    reschedule only while it's pending and only into the future.
    """
    lock_for_write(db)
    time_request = get_request_or_404(db, request_id)
    request = request_as_dict(time_request)

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
    time_request.requested_night = date.fromisoformat(body.night)
    time_request.start = start
    time_request.end = end
    db.commit()

    rescheduled = request_as_dict(time_request)
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


def log_entry_as_dict(entry: DecisionLog) -> dict:
    return {
        "id": entry.id, "request_id": entry.request_id, "type": entry.type,
        "previous_status": entry.previous_status, "new_status": entry.new_status,
        "previous_start": entry.previous_start, "previous_end": entry.previous_end,
        "new_start": entry.new_start, "new_end": entry.new_end,
        "notes": entry.notes, "decided_by": entry.decided_by, "decided_at": entry.decided_at,
    }


@router.get("/requests/{request_id}/history", response_model=List[LogEntryOut])
def request_history(request_id: int, db: Session = Depends(get_db)):
    """Decisions made on a request, from oldest to most recent."""
    verify_request_exists(db, request_id)
    entries = db.scalars(
        select(DecisionLog).where(DecisionLog.request_id == request_id).order_by(DecisionLog.id)
    ).all()
    return [localized_log_entry(log_entry_as_dict(e)) for e in entries]


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


def calendar_entry_as_dict(request: Request) -> dict:
    return {
        "id": request.id,
        "observer": request.requester.name,
        "co_observers": request.co_observers,
        "requested_night": request.requested_night.isoformat(),
        "start": request.start,
        "end": request.end,
        "status": request.status,
        "reviewer_notes": request.reviewer_notes,
        "created_at": request.created_at,
        "research_program_id": request.research_program_id,
        "research_program_name": request.research_program.name,
        "description": request.research_program.description,
        "specs": request.research_program.specs,
    }


@router.get("/calendar")
def calendar(
    year:  Optional[int] = None,
    month: Optional[int] = None,
    db: Session = Depends(get_db)
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

    matching_requests = db.scalars(
        select(Request)
        .where(
            Request.requested_night.between(date.fromisoformat(first), date.fromisoformat(last)),
            Request.status.in_(("approved", "pending")),
        )
        .order_by(Request.start, Request.created_at)
    ).all()

    requests = [calendar_entry_as_dict(r) for r in matching_requests]
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
def statistics(db: Session = Depends(get_db)):
    """Bonus endpoint for aggregate statistics — useful for future work."""
    totals = db.execute(
        select(Request.status, func.count().label("count")).group_by(Request.status)
    ).mappings().all()

    by_research_program = db.execute(
        select(
            Research.name,
            func.count(Request.id).label("request_count"),
            func.sum(case((Request.status == "approved", 1), else_=0)).label("approved_count"),
        )
        .select_from(Research)
        .outerjoin(Request, Request.research_program_id == Research.id)
        .group_by(Research.id)
        .order_by(func.count(Request.id).desc())
    ).mappings().all()

    return {
        "by_status": [dict(r) for r in totals],
        "by_research_program": [dict(r) for r in by_research_program]
    }
