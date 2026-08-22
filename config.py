"""Environment-based configuration — every function reads `os.environ`
fresh on every call, never cached at import time, so tests can switch
behavior (a different DB path, a different auth mode, a different
observatory timezone) without depending on import order."""

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


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
