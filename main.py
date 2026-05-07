"""
Telescope Time — FastAPI app autonoma
Avvio locale:   uvicorn main:app --reload --port 8010
Docker:         gestito dal Dockerfile
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from router import router
import os

app = FastAPI(
    title="CRaC — Telescope Time",
    description="Gestione richieste tempo telescopio",
    version="1.0.0"
)

app.include_router(router)

# Serve le pagine HTML statiche dalla stessa directory
app.mount("/", StaticFiles(directory="static", html=True), name="static")
