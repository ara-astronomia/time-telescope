"""
Telescope Time — FastAPI app autonoma
Avvio locale:   uvicorn main:app --reload --port 8010
Docker:         gestito dal Dockerfile
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from router import router, init_db
import os


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Il database viene creato all'avvio dell'app, non all'import di router:
    # così importare il modulo non ha effetti collaterali sul filesystem.
    init_db()
    yield


app = FastAPI(
    title="CRaC — Telescope Time",
    description="Gestione richieste tempo telescopio",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(router)

# Serve le pagine HTML statiche dalla stessa directory.
# Il path è assoluto per non dipendere dalla directory di lavoro.
STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
