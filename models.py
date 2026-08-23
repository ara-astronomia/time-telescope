"""ORM schema (SQLAlchemy) and engine/session lifecycle — kept separate from
`auth.py`'s Pydantic `Identity` and `schemas.py`'s request/response models so
a mapped `User`/`Request` can't collide with an identically-named API
model."""

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import ForeignKey, String, Text, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from config import database_url


def now_utc_string() -> str:
    """Same format already used for `created_at`/`updated_at`/`decided_at`
    before the ORM: Python-side default instead of a per-dialect SQL
    function (`strftime` on SQLite, `DATE_FORMAT`/`NOW()` on MariaDB) — one
    implementation instead of one per engine.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Base(DeclarativeBase):
    # MariaDB requires an explicit length on VARCHAR (SQLite doesn't care):
    # one default for every plain `Mapped[str]` column instead of repeating
    # a length on each. Columns that hold genuinely free-form text
    # (descriptions, notes) opt out with an explicit `Text` below.
    type_annotation_map = {str: String(255)}


class Research(Base):
    __tablename__ = "researches"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    description: Mapped[Optional[str]] = mapped_column(Text, default=None)
    specs: Mapped[Optional[str]] = mapped_column(Text, default=None)
    created_at: Mapped[str] = mapped_column(default=now_utc_string)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    # non-null username = identity verified by Authelia.
    # NULL for someone known only by name (co-observers, #40).
    username: Mapped[Optional[str]] = mapped_column(unique=True, default=None)
    name: Mapped[str]
    # key used to recognize a person already in the registry (#40);
    # multiple rows can have it NULL.
    email: Mapped[Optional[str]] = mapped_column(unique=True, default=None)
    created_at: Mapped[str] = mapped_column(default=now_utc_string)
    updated_at: Mapped[Optional[str]] = mapped_column(default=None, onupdate=now_utc_string)


class Request(Base):
    __tablename__ = "requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    research_program_id: Mapped[int] = mapped_column(ForeignKey("researches.id"))
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    co_observers: Mapped[Optional[str]] = mapped_column(Text, default=None)
    # reference night, derived from the date of `start`: the small hours
    # belong to the previous night. It's the key the calendar groups on.
    requested_night: Mapped[date]
    # time slot, UTC: '2026-09-12T20:00:00Z'.
    start: Mapped[str]
    end: Mapped[str]
    status: Mapped[str] = mapped_column(default="pending")
    reviewer_notes: Mapped[Optional[str]] = mapped_column(Text, default=None)
    created_at: Mapped[str] = mapped_column(default=now_utc_string)
    updated_at: Mapped[Optional[str]] = mapped_column(default=None, onupdate=now_utc_string)

    # Every response that includes a request also needs the research
    # program's name and the requester's name/email (see `request_as_dict`):
    # eager by default, not per-query, so no call site has to remember it.
    research_program: Mapped["Research"] = relationship(lazy="joined")
    requester: Mapped["User"] = relationship(lazy="joined")


class DecisionLog(Base):
    """Two kinds of event in the same table, told apart by `type`: a single
    ordered log is what someone reading a request's history needs. The
    columns of the other kind stay NULL."""
    __tablename__ = "decision_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id"))
    type: Mapped[str] = mapped_column(default="decision")
    previous_status: Mapped[Optional[str]] = mapped_column(default=None)
    new_status: Mapped[Optional[str]] = mapped_column(default=None)
    previous_start: Mapped[Optional[str]] = mapped_column(default=None)
    previous_end: Mapped[Optional[str]] = mapped_column(default=None)
    new_start: Mapped[Optional[str]] = mapped_column(default=None)
    new_end: Mapped[Optional[str]] = mapped_column(default=None)
    notes: Mapped[Optional[str]] = mapped_column(Text, default=None)
    decided_by: Mapped[Optional[str]] = mapped_column(default=None)
    decided_at: Mapped[str] = mapped_column(default=now_utc_string)


engine = None
SessionLocal = None
_engine_url = None


def _ensure_engine():
    """(Re)builds the engine when `DATABASE_URL` has changed since the last
    call — otherwise a no-op, reusing the existing engine/pool.

    Rebuilding only on change, rather than once at startup, matters for
    more than tidiness: the engine is a module-level global, shared by
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
    before a plain read, which is exactly why `lock_for_write` (router.py)
    can issue `BEGIN IMMEDIATE` itself as a plain statement — nothing has
    opened a transaction yet at that point in the request. Overriding this
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
