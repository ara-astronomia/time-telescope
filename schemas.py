"""Pydantic request/response models for the API."""

from datetime import date, datetime, time, timedelta
from typing import List, Literal, Optional

from pydantic import BaseModel, NaiveDatetime, ValidationInfo, field_validator

from config import now_at_observatory, to_utc


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
