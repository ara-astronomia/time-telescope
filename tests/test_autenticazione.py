"""L'identità arriva dagli header che Nginx riceve da Authelia; in sviluppo
AUTH_MODE=dev li sintetizza."""

from conftest import RESPONSABILE, SOCIO


# ─── Modalità forward-auth (produzione) ───────────────────────────────────────

def test_senza_header_e_401(client_authelia):
    res = client_authelia.get("/telescope-time/ricerche")
    assert res.status_code == 401
    assert res.json()["detail"] == "Autenticazione richiesta."


def test_socio_autenticato_legge(client_authelia):
    assert client_authelia.get("/telescope-time/ricerche", headers=SOCIO).status_code == 200


def test_socio_non_puo_approvare(client_authelia):
    client_authelia.post("/telescope-time/ricerche", json={"nome": "Supernovae"}, headers=RESPONSABILE)
    client_authelia.post(
        "/telescope-time/richieste",
        json={"ricerca_id": 1, "osservatore": "Mario", "giorno_richiesto": "2026-09-12"},
        headers=SOCIO,
    )
    res = client_authelia.patch(
        "/telescope-time/richieste/1", json={"stato": "approvata"}, headers=SOCIO
    )
    assert res.status_code == 403
    assert "telescope-responsabili" in res.json()["detail"]


def test_responsabile_puo_approvare(client_authelia):
    client_authelia.post("/telescope-time/ricerche", json={"nome": "Supernovae"}, headers=RESPONSABILE)
    client_authelia.post(
        "/telescope-time/richieste",
        json={"ricerca_id": 1, "osservatore": "Mario", "giorno_richiesto": "2026-09-12"},
        headers=SOCIO,
    )
    res = client_authelia.patch(
        "/telescope-time/richieste/1", json={"stato": "approvata"}, headers=RESPONSABILE
    )
    assert res.status_code == 200
    assert res.json()["stato"] == "approvata"


def test_gruppi_letti_dall_header(client_authelia):
    res = client_authelia.get("/telescope-time/me", headers=RESPONSABILE)
    assert res.json() == {
        "nome": "anna",
        "gruppi": ["soci", "telescope-responsabili"],
        "email": "anna@example.test",
        "nome_completo": None,          # Authelia non ha inviato Remote-Name
    }


def test_header_senza_gruppi(client_authelia):
    res = client_authelia.get("/telescope-time/me", headers={"Remote-User": "solo"})
    assert res.json()["gruppi"] == []


# ─── Modalità dev ─────────────────────────────────────────────────────────────

def test_dev_sintetizza_l_utente(client):
    res = client.get("/telescope-time/me")
    assert res.status_code == 200
    assert res.json() == {
        "nome": "sviluppo",
        "gruppi": ["telescope-responsabili"],
        "email": "sviluppo@example.test",
        "nome_completo": None,
    }


def test_dev_gli_header_espliciti_vincono(client):
    """Serve a provare un utente diverso senza riavviare l'app."""
    res = client.get("/telescope-time/me", headers=SOCIO)
    assert res.json()["nome"] == "mario"
    assert res.json()["gruppi"] == ["soci"]


def test_dev_utente_forzato_senza_gruppo_riceve_403(client, ricerca):
    client.post(
        "/telescope-time/richieste",
        json={"ricerca_id": ricerca["id"], "osservatore": "Mario", "giorno_richiesto": "2026-09-12"},
    )
    res = client.patch("/telescope-time/richieste/1", json={"stato": "approvata"}, headers=SOCIO)
    assert res.status_code == 403


def test_dev_variabili_personalizzabili(client, monkeypatch):
    monkeypatch.setenv("DEV_USER", "raniero")
    monkeypatch.setenv("DEV_GROUPS", "soci")
    res = client.get("/telescope-time/me")
    assert res.json()["nome"] == "raniero"
    assert res.json()["gruppi"] == ["soci"]
