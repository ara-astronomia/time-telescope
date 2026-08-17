import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


RESPONSABILE = {
    "Remote-User": "anna",
    "Remote-Groups": "soci,telescope-responsabili",
    "Remote-Email": "anna@example.test",
}
SOCIO = {"Remote-User": "mario", "Remote-Groups": "soci",
         "Remote-Email": "mario@example.test"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Client su un database temporaneo, ricreato da zero per ogni test.

    Il `with` è necessario: fa girare il lifespan dell'app, che è dove
    init_db() crea le tabelle.

    AUTH_MODE=dev sintetizza l'identità, così i test che non riguardano
    l'autorizzazione non devono passare header a ogni chiamata; quelli che
    la riguardano usano `client_authelia`.
    """
    monkeypatch.setenv("TELESCOPE_DB_PATH", str(tmp_path / "telescope_test.db"))
    monkeypatch.setenv("AUTH_MODE", "dev")
    import main

    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def client_authelia(tmp_path, monkeypatch):
    """Client in modalità produzione: l'identità arriva solo dagli header."""
    monkeypatch.setenv("TELESCOPE_DB_PATH", str(tmp_path / "telescope_test.db"))
    monkeypatch.setenv("AUTH_MODE", "forward-auth")
    import main

    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def ricerca(client):
    """Una ricerca già creata, punto di partenza di quasi ogni test."""
    res = client.post("/telescope-time/ricerche", json={"nome": "Supernovae"})
    assert res.status_code == 201
    return res.json()


@pytest.fixture
def ricerca_authelia(client_authelia):
    """Come `ricerca`, ma sul client in modalità forward-auth."""
    res = client_authelia.post(
        "/telescope-time/ricerche", json={"nome": "Supernovae"}, headers=RESPONSABILE
    )
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


# ─── Frontend: l'app servita a un browser vero ────────────────────────────────

@pytest.fixture(scope="session")
def app_url(tmp_path_factory):
    """Avvia l'app su una porta libera: il browser fa richieste HTTP reali,
    quindi TestClient non basta."""
    import os, socket, threading, time
    import uvicorn

    os.environ["TELESCOPE_DB_PATH"] = str(tmp_path_factory.mktemp("db") / "frontend.db")
    os.environ["AUTH_MODE"] = "dev"
    import main

    presa = socket.socket()
    presa.bind(("127.0.0.1", 0))
    porta = presa.getsockname()[1]
    presa.close()

    server = uvicorn.Server(uvicorn.Config(main.app, port=porta, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", porta), 0.1).close()
            break
        except OSError:
            time.sleep(0.1)

    yield f"http://127.0.0.1:{porta}"
    server.should_exit = True
