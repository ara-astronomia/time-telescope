# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Local development — dependencies are managed with uv (Python 3.14, read from .python-version)
uv sync                             # creates .venv and installs from the lockfile
uv run uvicorn main:app --reload --port 8010

# From another device on the LAN (without Nginx/Authelia in front, dev mode is required)
AUTH_MODE=dev uv run uvicorn main:app --reload --host 0.0.0.0 --port 8010

# Tests (pytest + pytest-playwright on Chromium for frontend tests)
uv run pytest -v --tb=short
uv run pytest tests/test_calendar.py -v        # a single file
uv run playwright install --with-deps chromium # required once for the *_frontend_* tests

# Deploy — MariaDB is the default backend on Docker (SQLite only outside it)
docker compose up -d --build
docker compose logs -f
docker compose exec mariadb mariadb -uroot -pdev telescope_time

# Sample data — seed.py calls init_db() itself, no prior startup needed
uv run python seed.py
```

Pages: `http://localhost:8010/request.html` (also `dashboard.html`, `calendar.html`). Swagger at `/docs`. The root `/` returns 404: there's no `index.html`.

**Must be launched from the repo root**: `main.py` mounts `StaticFiles(directory="static")` with an absolute path derived from `__file__`, and the default DB is `./telescope_time.db`.

CI (`.github/workflows/`): `tests.yml` runs `pytest` on every push/PR to `main`; `docker-build.yml` builds and publishes the image on push to `main`/`v*` tags or on a PR with the `build-docker` label.

## Architecture

Monolithic FastAPI service (3 Python modules + 3 HTML pages) for managing CRaC telescope time requests, exposed at `time_telescope.ara.roma.it` via Nginx (with Authelia in ForwardAuth) → container on :8010.

- `main.py` — the FastAPI app. `init_db()` runs in the `lifespan`, not at import. Includes `router` then mounts `static/` on `/` with `html=True`: order matters, the `/` mount is catch-all and must stay last. Also seeds an empty database automatically in `AUTH_MODE=dev` (see `seed.py`).
- `models.py` — the SQLAlchemy ORM schema (`Research`, `User`, `Request`, `DecisionLog`), kept separate from `router.py` so a mapped `User`/`Request` can't collide with the identically-named Pydantic models the API layer uses.
- `router.py` — everything else: env-based config, the database engine/session, authentication/user registry, Pydantic models, email sending, endpoints under the `/telescope-time` prefix.
- `seed.py` — sample data for local development, portable across SQLite and MariaDB (goes through the same ORM models the app uses, not raw dialect-specific SQL).
- `static/*.html` (`request.html`, `dashboard.html`, `calendar.html`) — three standalone pages (inline HTML+CSS+JS, no build step, no external dependency) that talk to the API via `const API_BASE = '/telescope-time'`. Changing an endpoint means manually updating the `fetch` call in the corresponding page.

### Authentication

No application login: identity comes from the headers Nginx receives from Authelia (`Remote-User`, `Remote-Groups`, `Remote-Email`, `Remote-Name`), read in `current_user()`. The group that counts as reviewer is configurable (`REVIEWERS_GROUP`, default `telescope-responsabili`); `reviewers_only()` guards the decision endpoints.

`AUTH_MODE=dev` (default `forward-auth`) synthesizes those headers when Authelia is absent — used in local development and in the default `docker-compose.yml`. The headers are trustworthy only because the container must never be reachable bypassing Nginx.

Every authenticated request is reconciled against the `users` table by `register_user()` (upsert on `username`, with an `email` fallback for co-observers entered by hand, see #40): this is what fills `TimeRequestOut.requester_id`, not a body parameter — an `observer` field sent in the POST body is ignored.

### Persistence

SQLAlchemy ORM (`models.py`), engine built from `DATABASE_URL` — SQLite by default outside Docker, MariaDB by default on Docker (same engine as production; see `docker-compose.yml`). Four tables: `researches` (`Research`, `name` UNIQUE), `users` (`User`, registry, `username`/`email` UNIQUE but nullable), `requests` (`Request`, FK to `researches` and `users`) plus `decision_log` (`DecisionLog`, log of decisions and reschedules, a single event type per row with the other type's columns left NULL). Session per request via `Depends(get_db)`.

`telescope_time.db` (SQLite only) is not versioned: it starts empty. An empty database is also populated automatically at startup when `AUTH_MODE=dev` (see `seed.py`, `main.py`'s `lifespan`) — never in production, never against a database that already has data. Locally the default path is `./telescope_time.db`; in Docker it's `TELESCOPE_DB_PATH=/data/telescope_time.db` on the `telescope_db` volume, only used if `DATABASE_URL` is overridden away from MariaDB.

Concurrent writes to `requests` (approval, reschedule) open a transaction strong enough to close the window between the conflict check and the `UPDATE`: `BEGIN IMMEDIATE` on SQLite, isolation level `SERIALIZABLE` on MariaDB (its default isolation doesn't lock the *absence* of a row, so two transactions that both see "no overlap yet" could otherwise both proceed).

### Domain model

A *research program* (`Research`) is a reusable observation project; a *request* (`Request`) books a **time slot** (`start`/`end`, `NaiveDatetime` at the API boundary — observatory local time, never with a timezone; stored as UTC) for a program, within a single *night*. The night (`requested_night`) is derived from `start` with a 12:00 threshold (`night_of()` / `NIGHT_THRESHOLD`): the small hours belong to the previous night. The `TimeSlot` model validates that `end` is after `start` and doesn't cross into the next night (no later than 12:00 the following day) — client and server enforce the same constraint, but only the server is the real guard.

Request states: `pending` (default) → `approved` | `rejected`, via `PATCH /telescope-time/requests/{id}` (`reviewers_only`). A status change or a reschedule (`PATCH .../schedule`) logs an event in `decision_log`, exposed via `GET .../history`. Whoever created the request can reschedule it only while it's `pending` and only into the future; the reviewer (`user.is_reviewer`) reschedules without restrictions, even after the fact.

`GET /calendar` derives each night's `night_status` from the `approved`/`pending` requests occupying it: at least one `approved` → `booked`; overlapping slots among `pending` only → `contested`; any other request present → `pending`; no request at all → the night doesn't appear (the frontend treats it as free). `rejected` requests are excluded. The uniqueness constraint is application-level: a second request for the **same** research program on the same night is blocked (`409`), while different programs can share a night as long as their slots don't overlap (checked only on approval, in `time_slot_conflict`).

### Email

`send_notification_email` / `send_outcome_email` / `send_reschedule_email` in `router.py` are synchronous and called inline in the handler: a slow SMTP server slows down the HTTP response. Without `SMTP_HOST`/`SMTP_USER` they just `print`, which is the default configuration. SMTP exceptions are caught and logged, never propagated. `send_outcome_email` sends to the observer if they have an email in the registry, otherwise to `REVIEWER_EMAIL` — never both.

## Conventions

Code, docstrings, identifiers (schema, endpoints, functions, HTML/test file names), and comments everywhere — including infrastructure files (`docker-compose.yml`, `nginx_time_telescope.conf`) — are in **English**. Italian is reserved for the user interface only: HTTP error details (`detail=...`) and the text in the `static/*.html` UI. Don't mix the two: an Italian identifier or comment outside the UI, or an English error message shown to a user, breaks this convention. A proper localization layer is planned for the future; until then, Italian stays hardcoded in those user-facing spots.

**No comments in code** (`#`, `//`). If a line seems to need one, that's a signal to rewrite it instead — a clearer name, an extracted function, a better structure. A docstring is the only accepted fallback, and only when there's truly no other way to convey something (FastAPI route/model docstrings double as Swagger documentation, so those stay); it must describe the current behavior only, never an issue number, a past state, or "before X / after X" narrative — same rule for test docstrings and test names.
