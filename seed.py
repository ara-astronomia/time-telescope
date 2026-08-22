"""Sample data for local development, portable across SQLite and MariaDB.

    uv run python seed.py
    docker compose exec telescope_time python seed.py

Replaces the old seed.sql: raw SQL date/UTC-arithmetic functions
(`strftime`, `date('now', ...)`) aren't portable across engines, going
through the same ORM models the app itself uses is. Rerunning it doesn't
duplicate users/research programs, but does duplicate requests.

Also runs automatically at startup (see `main.py`'s `lifespan`) when
`AUTH_MODE=dev` and the database is empty — never against a database that
already has any data, and never outside dev mode.

Names, research programs and usernames are made up: none of these
observers really exist in Authelia. Dates are relative to today, so the
calendar always has something to show in the current month and the next
one without needing to update this file.
"""

from datetime import date, datetime, time, timedelta

import router

USERS = [
    ("gvernier",  "Giulia Vernier",  "giulia.vernier@example.test"),
    ("efabbri",   "Elena Fabbri",    "elena.fabbri@example.test"),
    ("dmanzoni",  "Davide Manzoni",  "davide.manzoni@example.test"),
    ("pranieri",  "Paolo Ranieri",   "paolo.ranieri@example.test"),
    ("cbellandi", "Chiara Bellandi", "chiara.bellandi@example.test"),
    ("socio-dev", "Luca Bertani",    "socio-dev@example.test"),
]

RESEARCH_PROGRAMS = [
    ("Survey exoplanet",        "Ricerca di transiti su nane rosse vicine", "Filtri BVRI, pose da 120s"),
    ("Monitoraggio comete",     "Curve di luce di comete periodiche",       "Filtro R, binning 2x2"),
    ("Curve di luce asteroidi", "Determinazione periodi di rotazione",      "Senza filtro, cadenza 60s"),
]


def local(days_ahead: int, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(date.today() + timedelta(days=days_ahead), time(hour, minute))


def requests():
    """Built by a function, not a module-level constant: `local()` reads
    `date.today()`, which must reflect the day the seed actually runs, not
    the day this module was first imported."""
    # (research program, requester, co-observers, start, end, status, reviewer notes)
    return [
        # Booked night.
        ("Survey exoplanet", "Giulia Vernier", "Marco Silvestri",
         local(3, 22), local(4, 1), "approved", "Meteo previsto stabile."),
        # Contested night: two different research programs request overlapping slots.
        ("Monitoraggio comete", "Elena Fabbri", None,
         local(7, 21), local(8, 0, 30), "pending", None),
        ("Curve di luce asteroidi", "Davide Manzoni", "Sara Ferretti",
         local(7, 23), local(8, 3), "pending", None),
        # Pending-only night: distinct shifts, no overlap.
        ("Monitoraggio comete", "Chiara Bellandi", "Luca Toselli",
         local(12, 20, 30), local(12, 23), "pending", None),
        ("Curve di luce asteroidi", "Elena Fabbri", None,
         local(12, 23), local(13, 2), "pending", None),
        # Rejected request: doesn't show up on the calendar and frees the night again.
        ("Survey exoplanet", "Paolo Ranieri", None,
         local(10, 22), local(11, 2), "rejected", "Strumentazione in manutenzione."),
        # Starts after midnight: the previous night to the day of `start`.
        ("Curve di luce asteroidi", "Paolo Ranieri", None,
         local(9, 2), local(9, 4, 30), "pending", None),
        # ─── Next month ──────────────────────────────────────────────────
        # Booked night.
        ("Survey exoplanet", "Paolo Ranieri", None,
         local(15, 21), local(15, 23, 30), "approved", "Confermato."),
        # Contested night.
        ("Monitoraggio comete", "Chiara Bellandi", "Luca Toselli",
         local(20, 22), local(21, 0, 30), "pending", None),
        ("Curve di luce asteroidi", "Davide Manzoni", None,
         local(20, 23), local(21, 1, 30), "pending", None),
        # Rejected request.
        ("Survey exoplanet", "Elena Fabbri", None,
         local(30, 20), local(30, 22), "rejected", "Strumento non disponibile."),
        # Starts after midnight, like above but later.
        ("Survey exoplanet", "Chiara Bellandi", "Luca Toselli",
         local(26, 3, 30), local(26, 5, 30), "approved", "Ok."),
        # Starts after midnight: belongs to the previous night, not the one for
        # the day of `start`.
        ("Curve di luce asteroidi", "Giulia Vernier", "Marco Silvestri",
         local(36, 1), local(36, 3, 30), "pending", None),
        # Booked night, end of month.
        ("Monitoraggio comete", "Davide Manzoni", None,
         local(40, 22, 30), local(41, 1), "approved", "Ok."),
        # Pending-only night, last day of the month.
        ("Survey exoplanet", "Chiara Bellandi", "Sara Ferretti",
         local(42, 21, 30), local(42, 23), "pending", None),
        # Belongs to the member synthesized by the dev switcher: always
        # pending, to immediately test whether the owner can edit it themselves.
        ("Survey exoplanet", "Luca Bertani", None,
         local(18, 21), local(18, 23), "pending", None),
    ]


def is_empty(db) -> bool:
    """No users, no research programs, no requests at all — a database
    that has never been used, not just one with nothing left after
    everything got rejected/deleted."""
    return (
        db.scalar(router.select(router.User.id).limit(1)) is None
        and db.scalar(router.select(router.Research.id).limit(1)) is None
        and db.scalar(router.select(router.Request.id).limit(1)) is None
    )


def seed(db) -> None:
    def user_id(name: str) -> int:
        return db.scalar(router.select(router.User.id).where(router.User.name == name))

    def program_id(name: str) -> int:
        return db.scalar(router.select(router.Research.id).where(router.Research.name == name))

    for username, name, email in USERS:
        exists = db.scalar(router.select(router.User).where(router.User.username == username))
        if not exists:
            db.add(router.User(username=username, name=name, email=email))
    db.commit()

    for name, description, specs in RESEARCH_PROGRAMS:
        exists = db.scalar(router.select(router.Research).where(router.Research.name == name))
        if not exists:
            db.add(router.Research(name=name, description=description, specs=specs))
    db.commit()

    for program_name, requester_name, co_observers, start, end, status, reviewer_notes in requests():
        request = router.Request(
            research_program_id=program_id(program_name),
            requester_id=user_id(requester_name),
            co_observers=co_observers,
            requested_night=router.night_of(start),
            start=router.to_utc(start),
            end=router.to_utc(end),
            status=status,
            reviewer_notes=reviewer_notes,
        )
        if status != "pending":
            request.updated_at = router.now_utc_string()
        db.add(request)
    db.commit()


if __name__ == "__main__":
    router.init_db()
    with router.SessionLocal() as db:
        seed(db)
    print(f"Seed completato: {len(USERS)} utenti, {len(RESEARCH_PROGRAMS)} ricerche, {len(requests())} richieste.")
