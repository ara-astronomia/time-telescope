"""L'identità arriva dagli header che Nginx riceve da Authelia; in sviluppo
AUTH_MODE=dev li sintetizza."""

from conftest import RESPONSABILE, SOCIO, corpo_richiesta


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
        json=corpo_richiesta(osservatore="Mario"),
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
        json=corpo_richiesta(osservatore="Mario"),
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
        "id": 1,
        "nome": "anna",
        "gruppi": ["soci", "telescope-responsabili"],
        "email": "anna@example.test",
        "nome_completo": None,          # Authelia non ha inviato Remote-Name
        "e_responsabile": True,
        "modalita_dev": False,
    }


def test_header_senza_gruppi(client_authelia):
    res = client_authelia.get("/telescope-time/me", headers={"Remote-User": "solo"})
    assert res.json()["gruppi"] == []
    assert res.json()["e_responsabile"] is False


def test_socio_non_e_responsabile(client_authelia):
    res = client_authelia.get("/telescope-time/me", headers=SOCIO)
    assert res.json()["e_responsabile"] is False


# ─── Modalità dev ─────────────────────────────────────────────────────────────

def test_dev_sintetizza_l_utente(client):
    res = client.get("/telescope-time/me")
    assert res.status_code == 200
    assert res.json() == {
        "id": 1,
        "nome": "sviluppo",
        "gruppi": ["telescope-responsabili"],
        "email": "sviluppo@example.test",
        "nome_completo": "Marta Conti",
        "e_responsabile": True,
        "modalita_dev": True,
    }


def test_dev_gli_header_espliciti_vincono(client):
    """Serve a provare un utente diverso senza riavviare l'app."""
    res = client.get("/telescope-time/me", headers=SOCIO)
    assert res.json()["nome"] == "mario"
    assert res.json()["gruppi"] == ["soci"]


def test_dev_utente_forzato_senza_gruppo_riceve_403(client, ricerca):
    client.post(
        "/telescope-time/richieste",
        json=corpo_richiesta(ricerca["id"], osservatore="Mario"),
    )
    res = client.patch("/telescope-time/richieste/1", json={"stato": "approvata"}, headers=SOCIO)
    assert res.status_code == 403


def test_dev_variabili_personalizzabili(client, monkeypatch):
    monkeypatch.setenv("DEV_USER", "raniero")
    monkeypatch.setenv("DEV_GROUPS", "soci")
    res = client.get("/telescope-time/me")
    assert res.json()["nome"] == "raniero"
    assert res.json()["gruppi"] == ["soci"]


# ─── Switcher di ruolo in dev (#26) ────────────────────────────────────────────
# Un solo utente sintetizzato costringeva a curl/Playwright per provare la
# dashboard come socio. Il cookie permette di farlo da un browser normale,
# senza riavviare il container.

def test_cookie_dev_ruolo_socio_sintetizza_un_socio(client):
    client.cookies.set("dev_ruolo", "socio")
    res = client.get("/telescope-time/me")
    assert res.json()["nome"] == "socio-dev"
    assert res.json()["nome_completo"] == "Luca Bertani"
    assert res.json()["gruppi"] == ["soci"]
    assert res.json()["e_responsabile"] is False


def test_cookie_dev_ruolo_responsabile_e_il_default(client):
    client.cookies.set("dev_ruolo", "responsabile")
    res = client.get("/telescope-time/me")
    assert res.json()["nome"] == "sviluppo"
    assert res.json()["e_responsabile"] is True


def test_header_esplicito_vince_sul_cookie(client):
    """Il cookie è una comodità per il browser; test e script che passano
    header espliciti (es. SOCIO/RESPONSABILE) non devono vedersene scavalcato
    l'utente."""
    client.cookies.set("dev_ruolo", "socio")
    res = client.get("/telescope-time/me", headers=RESPONSABILE)
    assert res.json()["nome"] == "anna"
    assert res.json()["e_responsabile"] is True


def test_cookie_dev_ruolo_ignorato_fuori_da_dev(client_authelia):
    """Fuori da AUTH_MODE=dev il cookie non ha alcun effetto: senza header
    resta un 401, non un login implicito come socio."""
    client_authelia.cookies.set("dev_ruolo", "socio")
    res = client_authelia.get("/telescope-time/me")
    assert res.status_code == 401
