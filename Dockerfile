FROM python:3.14-slim AS base

# uv as installer: same versions as the lockfile, no resolution at build time
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

# Dependencies: separate layer, invalidated only if pyproject or the lockfile change
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Application code
COPY main.py .
COPY router.py .
COPY seed.py .

# Static HTML pages
COPY static/ ./static/

# Unprivileged user. UID 1000 matches the user on development machines:
# with the compose's bind mount, files the container writes to /app stay
# owned by the host user instead of root.
# /data needs to be made writable here: Docker initializes an empty volume
# by copying permissions and ownership from the image's mountpoint.
RUN useradd --create-home --uid 1000 app \
 && mkdir -p /data \
 && chown -R app:app /app /data

USER app

# Volume for the SQLite database, when DATABASE_URL points at one instead
# of the default MariaDB (see docker-compose.yml).
VOLUME ["/data"]

ENV TELESCOPE_DB_PATH=/data/telescope_time.db
# Without this, the application's prints stay buffered on stdout and don't
# show up in `docker compose logs` until the process exits.
ENV PYTHONUNBUFFERED=1
# uv installs into /app/.venv: putting it on PATH avoids needing `uv run`
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8010

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8010"]

# ─── Dev stage ──────────────────────────────────────────────────────────────
# Adds the `dev` dependency group (pytest, pytest-playwright, httpx2) and a
# Chromium Playwright already knows how to drive — so `uv run pytest`
# (including the *_frontend_* tests) works right after `docker compose up
# --build`, without a manual `playwright install` step to remember every
# time the container gets rebuilt. Selected by docker-compose.yml's `target:
# dev`. Being the last stage in the file, it would also become the default
# with no `target` specified at all — docker-build.yml pins `target: base`
# explicitly so the image it pushes never carries this test-only tooling.
FROM base AS dev

USER root
RUN uv sync --frozen
# System libraries Chromium needs (fonts, X11, audio, ...): root-only, and
# shared regardless of which user later launches the browser.
RUN uv run playwright install-deps chromium

USER app
# The browser binary itself is cached per-user (~/.cache/ms-playwright),
# hence installed as `app`, the user that will actually run the tests.
RUN uv run playwright install chromium
