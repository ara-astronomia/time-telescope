FROM python:3.14-slim

# uv come installer: stesse versioni del lockfile, nessuna risoluzione a build time
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

# Dipendenze: layer separato, invalidato solo se cambiano pyproject o lockfile
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Codice applicazione
COPY main.py .
COPY router.py .

# Pagine HTML statiche
COPY static/ ./static/

# Utente non privilegiato. L'UID 1000 coincide con quello dell'utente sulle
# macchine di sviluppo: con il bind mount del compose, i file che il container
# scrive in /app restano di proprietà dell'utente host invece che di root.
# /data va reso scrivibile qui: Docker inizializza un volume vuoto copiando
# permessi e proprietario dal mountpoint dell'immagine.
RUN useradd --create-home --uid 1000 app \
 && mkdir -p /data \
 && chown -R app:app /app /data

USER app

# Volume per il database SQLite
VOLUME ["/data"]

ENV TELESCOPE_DB_PATH=/data/telescope_time.db
# Senza questo i print dell'applicazione restano nel buffer di stdout e non
# compaiono in `docker compose logs` finché il processo non termina.
ENV PYTHONUNBUFFERED=1
# uv installa in /app/.venv: metterlo sul PATH evita di dover usare `uv run`
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8010

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8010"]
