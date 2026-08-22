"""ORM schema (SQLAlchemy) — kept separate from router.py so a mapped
`User`/`Request` can't collide with the identically-named Pydantic models
`router.py` uses for the API layer (the authenticated identity, the
request bodies)."""

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


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
