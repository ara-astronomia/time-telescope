"""L'identità arriva dagli header che Nginx riceve da Authelia; in sviluppo
AUTH_MODE=dev li sintetizza."""

from conftest import REVIEWER, MEMBER, request_body


# ─── Forward-auth mode (production) ────────────────────────────────────────────

def test_no_header_returns_401(client_authelia):
    res = client_authelia.get("/telescope-time/research-programs")
    assert res.status_code == 401
    assert res.json()["detail"] == "Autenticazione richiesta."


def test_authenticated_member_can_read(client_authelia):
    assert client_authelia.get("/telescope-time/research-programs", headers=MEMBER).status_code == 200


def test_member_cannot_approve(client_authelia):
    client_authelia.post("/telescope-time/research-programs", json={"name": "Supernovae"}, headers=REVIEWER)
    client_authelia.post(
        "/telescope-time/requests",
        json=request_body(observer="Mario"),
        headers=MEMBER,
    )
    res = client_authelia.patch(
        "/telescope-time/requests/1", json={"status": "approved"}, headers=MEMBER
    )
    assert res.status_code == 403
    assert "telescope-responsabili" in res.json()["detail"]


def test_reviewer_can_approve(client_authelia):
    client_authelia.post("/telescope-time/research-programs", json={"name": "Supernovae"}, headers=REVIEWER)
    client_authelia.post(
        "/telescope-time/requests",
        json=request_body(observer="Mario"),
        headers=MEMBER,
    )
    res = client_authelia.patch(
        "/telescope-time/requests/1", json={"status": "approved"}, headers=REVIEWER
    )
    assert res.status_code == 200
    assert res.json()["status"] == "approved"


def test_groups_read_from_header(client_authelia):
    res = client_authelia.get("/telescope-time/me", headers=REVIEWER)
    assert res.json() == {
        "id": 1,
        "username": "anna",
        "groups": ["soci", "telescope-responsabili"],
        "email": "anna@example.test",
        "full_name": None,          # Authelia didn't send Remote-Name
        "is_reviewer": True,
        "is_dev_mode": False,
    }


def test_header_without_groups(client_authelia):
    res = client_authelia.get("/telescope-time/me", headers={"Remote-User": "solo"})
    assert res.json()["groups"] == []
    assert res.json()["is_reviewer"] is False


def test_member_is_not_a_reviewer(client_authelia):
    res = client_authelia.get("/telescope-time/me", headers=MEMBER)
    assert res.json()["is_reviewer"] is False


# ─── Dev mode ───────────────────────────────────────────────────────────────────

def test_dev_synthesizes_the_user(client):
    res = client.get("/telescope-time/me")
    assert res.status_code == 200
    assert res.json() == {
        "id": 1,
        "username": "sviluppo",
        "groups": ["telescope-responsabili"],
        "email": "sviluppo@example.test",
        "full_name": "Marta Conti",
        "is_reviewer": True,
        "is_dev_mode": True,
    }


def test_dev_explicit_headers_win(client):
    """Serve a provare un utente diverso senza riavviare l'app."""
    res = client.get("/telescope-time/me", headers=MEMBER)
    assert res.json()["username"] == "mario"
    assert res.json()["groups"] == ["soci"]


def test_dev_forced_user_without_group_gets_403(client, research_program):
    client.post(
        "/telescope-time/requests",
        json=request_body(research_program["id"], observer="Mario"),
    )
    res = client.patch("/telescope-time/requests/1", json={"status": "approved"}, headers=MEMBER)
    assert res.status_code == 403


def test_dev_variables_are_customizable(client, monkeypatch):
    monkeypatch.setenv("DEV_USER", "raniero")
    monkeypatch.setenv("DEV_GROUPS", "soci")
    res = client.get("/telescope-time/me")
    assert res.json()["username"] == "raniero"
    assert res.json()["groups"] == ["soci"]


# ─── Dev role switcher (#26) ────────────────────────────────────────────────────
# A single synthesized user forced curl/Playwright to try the dashboard as a
# member. The cookie makes that possible from a regular browser, without
# restarting the container.

def test_dev_role_cookie_member_synthesizes_a_member(client):
    client.cookies.set("dev_role", "socio")
    res = client.get("/telescope-time/me")
    assert res.json()["username"] == "socio-dev"
    assert res.json()["full_name"] == "Luca Bertani"
    assert res.json()["groups"] == ["soci"]
    assert res.json()["is_reviewer"] is False


def test_dev_role_cookie_reviewer_is_the_default(client):
    client.cookies.set("dev_role", "responsabile")
    res = client.get("/telescope-time/me")
    assert res.json()["username"] == "sviluppo"
    assert res.json()["is_reviewer"] is True


def test_explicit_header_wins_over_cookie(client):
    """Il cookie è una comodità per il browser; test e script che passano
    header espliciti (es. MEMBER/REVIEWER) non devono vedersene scavalcato
    l'utente."""
    client.cookies.set("dev_role", "socio")
    res = client.get("/telescope-time/me", headers=REVIEWER)
    assert res.json()["username"] == "anna"
    assert res.json()["is_reviewer"] is True


def test_dev_role_cookie_ignored_outside_dev(client_authelia):
    """Fuori da AUTH_MODE=dev il cookie non ha alcun effetto: senza header
    resta un 401, non un login implicito come socio."""
    client_authelia.cookies.set("dev_role", "socio")
    res = client_authelia.get("/telescope-time/me")
    assert res.status_code == 401
