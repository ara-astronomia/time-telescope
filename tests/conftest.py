import os
import sys
import time as time_module
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


REVIEWER = {
    "Remote-User": "anna",
    "Remote-Groups": "soci,telescope-responsabili",
    "Remote-Email": "anna@example.test",
}
MEMBER = {"Remote-User": "mario", "Remote-Groups": "soci",
          "Remote-Email": "mario@example.test"}


@pytest.fixture
def observatory_far_ahead_of_the_system_clock():
    """Fixes what the OS actually believes "now" is to UTC, then changes
    `TZ` again to Pacific/Kiritimati (UTC+14, the furthest-ahead timezone
    that exists) without a matching `time.tzset()` — so `os.environ["TZ"]`
    reads Kiritimati while `datetime.now()` (which only picks up a `TZ`
    change through `tzset()`) keeps answering as UTC. A test can then build
    an instant that's unambiguously in the future for one and in the past
    for the other, deterministic regardless of the host machine's own
    timezone: exactly the gap between reading `TZ` explicitly and trusting
    the process's own idea of the clock.
    """
    previous_tz = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time_module.tzset()
    os.environ["TZ"] = "Pacific/Kiritimati"
    try:
        yield
    finally:
        if previous_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous_tz
        time_module.tzset()


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Client on a temporary database, recreated from scratch for every test.

    The `with` is necessary: it runs the app's lifespan, which is where
    init_db() creates the tables.

    AUTH_MODE=dev synthesizes the identity, so tests that aren't about
    authorization don't need to pass headers on every call; the ones that
    are use `client_authelia`.
    """
    monkeypatch.setenv("TELESCOPE_DB_PATH", str(tmp_path / "telescope_test.db"))
    monkeypatch.setenv("AUTH_MODE", "dev")
    import main

    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def client_authelia(tmp_path, monkeypatch):
    """Client in production mode: identity comes only from the headers."""
    monkeypatch.setenv("TELESCOPE_DB_PATH", str(tmp_path / "telescope_test.db"))
    monkeypatch.setenv("AUTH_MODE", "forward-auth")
    import main

    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def research_program(client):
    """An already-created research program, the starting point of almost
    every test."""
    res = client.post("/telescope-time/research-programs", json={"name": "Supernovae"})
    assert res.status_code == 201
    return res.json()


@pytest.fixture
def research_program_authelia(client_authelia):
    """Like `research_program`, but on the forward-auth client."""
    res = client_authelia.post(
        "/telescope-time/research-programs", json={"name": "Supernovae"}, headers=REVIEWER
    )
    assert res.status_code == 201
    return res.json()


def future_night(days_ahead: int = 30) -> date:
    """A future night: observations in the past can't be booked."""
    return date.today() + timedelta(days=days_ahead)


def time_slot(night, hour: int = 22, duration: int = 3) -> tuple[str, str]:
    """A night's time slot: starts at `hour`, lasts `duration` hours.

    The default starts at 22 and ends at one, so every test request
    crosses midnight like real observations do.
    """
    if isinstance(night, str):
        night = date.fromisoformat(night)
    start = datetime.combine(night, time(hour))
    return start.isoformat(), (start + timedelta(hours=duration)).isoformat()


@pytest.fixture
def night():
    """The night most tests work on."""
    return future_night()


@pytest.fixture
def other_night():
    return future_night(31)


def request_body(research_program_id=1, night=None, hour=22, duration=3, **extra) -> dict:
    """Body of a valid POST /requests, to be enriched with `extra`."""
    start, end = time_slot(night or future_night(), hour, duration)
    return {"research_program_id": research_program_id, "start": start, "end": end, **extra}


def submit_time_request(client, research_program_id, night, hour=22, duration=3, observer="Mario Rossi"):
    return client.post(
        "/telescope-time/requests",
        json=request_body(research_program_id, night, hour, duration, observer=observer),
    )


def review(client, request_id, status="approved", notes=None):
    return client.patch(
        f"/telescope-time/requests/{request_id}",
        json={"status": status, "reviewer_notes": notes},
    )


# ─── Frontend: the app served to a real browser ────────────────────────────────

@pytest.fixture(scope="session")
def app_url(tmp_path_factory):
    """Starts the app on a free port: the browser makes real HTTP requests,
    so TestClient isn't enough."""
    import os, socket, threading, time
    import uvicorn

    os.environ["TELESCOPE_DB_PATH"] = str(tmp_path_factory.mktemp("db") / "frontend.db")
    os.environ["AUTH_MODE"] = "dev"
    import main

    socket_ = socket.socket()
    socket_.bind(("127.0.0.1", 0))
    port = socket_.getsockname()[1]
    socket_.close()

    server = uvicorn.Server(uvicorn.Config(main.app, port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), 0.1).close()
            break
        except OSError:
            time.sleep(0.1)

    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


def as_member(page):
    """From now on the page speaks as a member, not as the reviewer
    AUTH_MODE=dev synthesizes by default."""
    page.context.set_extra_http_headers({"Remote-User": "mario", "Remote-Groups": "soci"})
