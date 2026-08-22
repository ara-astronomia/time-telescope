FROM python:3.14-slim

# uv as installer: same versions as the lockfile, no resolution at build time
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

# Dependencies: separate layer, invalidated only if pyproject or the lockfile change
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Application code
COPY main.py .
COPY router.py .

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
