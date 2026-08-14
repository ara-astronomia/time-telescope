import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Client su un database temporaneo, ricreato da zero per ogni test.

    Il `with` è necessario: fa girare il lifespan dell'app, che è dove
    init_db() crea le tabelle.
    """
    monkeypatch.setenv("TELESCOPE_DB_PATH", str(tmp_path / "telescope_test.db"))
    import main

    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def ricerca(client):
    """Una ricerca già creata, punto di partenza di quasi ogni test."""
    res = client.post("/telescope-time/ricerche", json={"nome": "Supernovae"})
    assert res.status_code == 201
    return res.json()


def crea_richiesta(client, ricerca_id, giorno, osservatore="Mario Rossi"):
    return client.post(
        "/telescope-time/richieste",
        json={
            "ricerca_id": ricerca_id,
            "osservatore": osservatore,
            "giorno_richiesto": giorno,
        },
    )


def approva(client, richiesta_id, stato="approvata", note=None):
    return client.patch(
        f"/telescope-time/richieste/{richiesta_id}",
        json={"stato": stato, "note_responsabile": note},
    )
