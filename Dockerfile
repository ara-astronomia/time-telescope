FROM python:3.12-slim

WORKDIR /app

# Dipendenze Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Codice applicazione
COPY main.py .
COPY router.py .

# Pagine HTML statiche
COPY static/ ./static/

# Volume per il database SQLite
VOLUME ["/data"]

ENV TELESCOPE_DB_PATH=/data/telescope_time.db

EXPOSE 8010

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8010"]
