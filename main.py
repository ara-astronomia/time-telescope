"""
Telescope Time — standalone FastAPI app
Local start:   uvicorn main:app --reload --port 8010
Docker:        managed by the Dockerfile
"""

from contextlib import asynccontextmanager
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from config import auth_mode, auto_seed, dev_user, dev_groups, observatory_tz
from models import init_db, SessionLocal
from router import router
import os
import seed


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    try:
        ZoneInfo(observatory_tz())
    except ZoneInfoNotFoundError as error:
        raise RuntimeError(f"TZ non valido: '{observatory_tz()}'.") from error

    if auth_mode() == "dev":
        print(
            "\n" + "=" * 70 +
            f"\n  WARNING: simulated authentication (AUTH_MODE=dev)."
            f"\n  Every request without headers counts as user '{dev_user()}'"
            f"\n  in groups '{dev_groups()}'. Not for production use.\n" +
            "=" * 70 + "\n",
            flush=True,  # without it, the message stays buffered and never reaches Docker logs
        )

        if auto_seed():
            with SessionLocal() as db:
                if seed.is_empty(db):
                    seed.seed(db)
                    print("[seed] Database vuoto: popolato con dati di esempio.", flush=True)

    yield


app = FastAPI(
    title="CRaC — Telescope Time",
    description="Gestione richieste tempo telescopio",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(router)

STATIC_DIR_ABSOLUTE = Path(__file__).resolve().parent / "static"
app.mount("/", StaticFiles(directory=STATIC_DIR_ABSOLUTE, html=True), name="static")
