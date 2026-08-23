"""User registry: identity comes from Authelia, not from the request body (#5).

A non-null `username` means a verified identity: only someone who has one
can open a request. Name and email aren't typed in by hand.
"""

from sqlalchemy import select

import models
from conftest import REVIEWER, MEMBER, future_night, request_body
from models import Request, User


def registered_users(client):
    with models.SessionLocal() as db:
        records = db.scalars(select(User).order_by(User.id)).all()
        return [
            {"id": r.id, "username": r.username, "name": r.name, "email": r.email,
             "created_at": r.created_at, "updated_at": r.updated_at}
            for r in records
        ]


def create_request_as(client, headers, night=None, research_program_id=1):
    return client.post(
        "/telescope-time/requests",
        json=request_body(research_program_id, night),
        headers=headers,
    )


# ─── The registry fills itself in ─────────────────────────────────────────────

def test_first_login_registers_the_user(client_authelia):
    client_authelia.get("/telescope-time/me", headers=REVIEWER)
    users = registered_users(client_authelia)
    assert [u["username"] for u in users] == ["anna"]
    assert users[0]["email"] == "anna@example.test"
    assert users[0]["name"]


def test_repeated_logins_do_not_duplicate(client_authelia):
    for _ in range(3):
        client_authelia.get("/telescope-time/me", headers=REVIEWER)
    assert len(registered_users(client_authelia)) == 1


def test_different_users_get_different_records(client_authelia):
    client_authelia.get("/telescope-time/me", headers=REVIEWER)
    client_authelia.get("/telescope-time/me", headers=MEMBER)
    assert sorted(u["username"] for u in registered_users(client_authelia)) == ["anna", "mario"]


def test_email_updates_when_it_changes_in_authelia(client_authelia):
    client_authelia.get("/telescope-time/me", headers=REVIEWER)
    updated = {**REVIEWER, "Remote-Email": "anna.nuova@example.test"}
    client_authelia.get("/telescope-time/me", headers=updated)
    users = registered_users(client_authelia)
    assert len(users) == 1
    assert users[0]["email"] == "anna.nuova@example.test"


def test_updated_at_is_null_until_the_registry_actually_changes(client_authelia):
    for _ in range(3):
        client_authelia.get("/telescope-time/me", headers=REVIEWER)
    assert registered_users(client_authelia)[0]["updated_at"] is None


def test_updated_at_is_set_when_the_registry_syncs(client_authelia):
    client_authelia.get("/telescope-time/me", headers=REVIEWER)
    updated = {**REVIEWER, "Remote-Email": "anna.nuova@example.test"}
    client_authelia.get("/telescope-time/me", headers=updated)
    assert registered_users(client_authelia)[0]["updated_at"] is not None


def test_multiple_users_without_email_are_allowed(client_authelia):
    """Needed for occasional co-observers whose contact info isn't known (#40)."""
    with models.SessionLocal() as db:
        db.add(User(name="Guest One"))
        db.add(User(name="Guest Two"))
        db.commit()
        without_email = db.scalars(
            select(User).where(User.email.is_(None))
        ).all()
        assert len(without_email) == 2


# ─── The request doesn't ask who you are ──────────────────────────────────────

def test_the_request_uses_the_authenticated_user(client_authelia, research_program_authelia):
    res = create_request_as(client_authelia, MEMBER)
    assert res.status_code == 201
    assert res.json()["observer"] == "mario"


def test_observer_field_in_the_body_is_ignored(client_authelia, research_program_authelia):
    """Even if the field is sent, the identity stays the verified one."""
    res = client_authelia.post(
        "/telescope-time/requests",
        json=request_body(observer="Someone Else"),
        headers=MEMBER,
    )
    assert res.status_code == 201
    assert res.json()["observer"] == "mario"


def test_the_requester_is_a_verified_user(client_authelia, research_program_authelia):
    create_request_as(client_authelia, MEMBER)
    with models.SessionLocal() as db:
        request = db.scalars(select(Request)).first()
        assert request.requester.username is not None


# ─── The outcome goes to whoever asked ─────────────────────────────────────────

def test_the_outcome_email_goes_to_the_requester(client_authelia, research_program_authelia, monkeypatch):
    import notifications
    sent = []
    monkeypatch.setattr(notifications, "send_message", lambda recipient, subject, body: sent.append(recipient))

    create_request_as(client_authelia, MEMBER)
    sent.clear()          # creation notifies the reviewer: only the outcome matters here
    client_authelia.patch("/telescope-time/requests/1", json={"status": "approved"},
                          headers=REVIEWER)

    assert sent == ["mario@example.test"]


def test_without_an_email_the_outcome_goes_to_the_reviewer(client_authelia, research_program_authelia, monkeypatch):
    import notifications
    sent = []
    monkeypatch.setattr(notifications, "send_message", lambda recipient, subject, body: sent.append(recipient))

    without_email = {"Remote-User": "guest", "Remote-Groups": "soci"}
    client_authelia.post("/telescope-time/requests",
                         json=request_body(night=future_night(32)),
                         headers=without_email)
    sent.clear()
    client_authelia.patch("/telescope-time/requests/1", json={"status": "approved"},
                          headers=REVIEWER)

    assert sent == [notifications.REVIEWER_EMAIL]


# ─── The name shown is the real one, not the username ─────────────────────────

REVIEWER_WITH_NAME = {**REVIEWER, "Remote-Name": "Anna Rossi"}


def test_remote_name_header_is_stored_in_the_registry(client_authelia):
    client_authelia.get("/telescope-time/me", headers=REVIEWER_WITH_NAME)
    registered = registered_users(client_authelia)[0]
    assert registered["username"] == "anna"
    assert registered["name"] == "Anna Rossi"


def test_without_a_remote_name_header_the_username_is_used(client_authelia):
    """Authelia may not send it: the username is readable regardless."""
    client_authelia.get("/telescope-time/me", headers=REVIEWER)
    assert registered_users(client_authelia)[0]["name"] == "anna"


def test_the_request_shows_the_real_name(client_authelia, research_program_authelia):
    res = client_authelia.post(
        "/telescope-time/requests",
        json=request_body(),
        headers=REVIEWER_WITH_NAME,
    )
    assert res.json()["observer"] == "Anna Rossi"


def test_the_name_updates_when_it_changes_in_authelia(client_authelia):
    client_authelia.get("/telescope-time/me", headers=REVIEWER_WITH_NAME)
    client_authelia.get("/telescope-time/me",
                        headers={**REVIEWER, "Remote-Name": "Anna Rossi Verdi"})
    users = registered_users(client_authelia)
    assert len(users) == 1
    assert users[0]["name"] == "Anna Rossi Verdi"


def test_login_promotes_an_existing_co_observer(client_authelia):
    """Authelia wins: if an email belongs to a person known only by name (a
    co-observer, #40) and that person logs in, the record gets promoted
    instead of a second one being created. It's the same person, and the
    observations they took part in stay theirs."""
    with models.SessionLocal() as db:
        co_observer = User(name="M. Rossi", email="mario.rossi@example.test")
        db.add(co_observer)
        db.commit()
        id_before = co_observer.id

    client_authelia.get("/telescope-time/me", headers={
        "Remote-User": "mrossi",
        "Remote-Groups": "soci",
        "Remote-Email": "mario.rossi@example.test",
        "Remote-Name": "Mario Rossi",
    })

    users = registered_users(client_authelia)
    assert len(users) == 1, "the record was duplicated instead of promoted"
    promoted = users[0]
    assert promoted["id"] == id_before, "the id changed: existing associations would be lost"
    assert promoted["username"] == "mrossi"
    assert promoted["name"] == "Mario Rossi"    # Authelia's name wins


def test_two_authelia_accounts_with_the_same_email(client_authelia):
    """Pathological case: two verified accounts with the same address. The
    second one still gets in, without stealing the email from the first."""
    first = {"Remote-User": "anna", "Remote-Groups": "soci",
             "Remote-Email": "shared@example.test"}
    second = {"Remote-User": "bruno", "Remote-Groups": "soci",
               "Remote-Email": "shared@example.test"}

    assert client_authelia.get("/telescope-time/me", headers=first).status_code == 200
    assert client_authelia.get("/telescope-time/me", headers=second).status_code == 200

    users = {u["username"]: u["email"] for u in registered_users(client_authelia)}
    assert users["anna"] == "shared@example.test"
    assert users["bruno"] is None


def test_creating_a_request_notifies_the_reviewer(client_authelia, research_program_authelia, monkeypatch):
    """Every email goes through send_message: the notification of a new
    request goes to the reviewer, not to whoever submitted it."""
    import notifications
    sent = []
    monkeypatch.setattr(notifications, "send_message",
                        lambda recipient, subject, body: sent.append((recipient, subject)))

    create_request_as(client_authelia, MEMBER)

    assert len(sent) == 1
    recipient, subject = sent[0]
    assert recipient == notifications.REVIEWER_EMAIL
    assert "Nuova richiesta" in subject
