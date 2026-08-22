"""
Telescope Time Request — FastAPI Router
To include in the main CRaC server with:
    from telescope_time.router import router as telescope_router
    app.include_router(telescope_router)
"""

from fastapi import APIRouter, HTTPException, Depends
from typing import Optional, List
from calendar import monthrange
from datetime import date
from sqlalchemy import text, select, func, case
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

import config
from auth import Identity, registered_user, reviewers_only
from models import Research, Request, DecisionLog, get_db
from notifications import (
    readable_time_slot,
    send_notification_email,
    send_outcome_email,
    send_reschedule_email,
)
from schemas import (
    LogEntryOut,
    ObservatoryOut,
    RescheduleRequest,
    ResearchProgramCreate,
    ResearchProgramOut,
    StatusUpdate,
    TimeRequestCreate,
    TimeRequestOut,
)

router = APIRouter(
    prefix="/telescope-time",
    tags=["Telescope Time"],
    dependencies=[Depends(registered_user)],
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
    return ObservatoryOut(timezone=config.observatory_tz())

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
    return {**request, "start": config.to_local(request["start"]).isoformat(),
            "end": config.to_local(request["end"]).isoformat()}


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
    (see `models.init_db`): pysqlite hasn't opened a transaction of its own
    yet at this point, since only reads happened so far this request.

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
        start=config.to_utc(body.start),
        end=config.to_utc(body.end),
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
        if body.start <= config.now_at_observatory():
            raise HTTPException(status_code=422, detail="Il nuovo inizio deve essere nel futuro.")

    start, end = config.to_utc(body.start), config.to_utc(body.end)
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
        **{field: config.to_local(entry[field]).isoformat()
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
    today = config.now_at_observatory()
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
