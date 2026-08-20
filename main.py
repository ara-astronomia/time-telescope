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
from router import router, init_db, auth_mode, dev_user, dev_groups
import os


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    if auth_mode() == "dev":
        print(
            "\n" + "=" * 70 +
            f"\n  ATTENZIONE: autenticazione simulata (AUTH_MODE=dev)."
            f"\n  Ogni richiesta senza header vale come utente '{dev_user()}'"
            f"\n  nei gruppi '{dev_groups()}'. Da non usare in produzione.\n" +
            "=" * 70 + "\n",
            flush=True,  # senza, il messaggio resta nel buffer e non arriva ai log Docker
        )

    yield


app = FastAPI(
    title="CRaC — Telescope Time",
    description="Gestione richieste tempo telescopio",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(router)

STATIC_DIR_ASSOLUTA = Path(__file__).resolve().parent / "static"
app.mount("/", StaticFiles(directory=STATIC_DIR_ASSOLUTA, html=True), name="static")
