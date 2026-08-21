"""Decision history (#9) and multiple concurrent observations (#4)."""

import pytest

from conftest import review, request_body, submit_time_request

HISTORY = "/telescope-time/requests/{}/history"


def calendar_of(client, night):
    res = client.get(
        "/telescope-time/calendar",
        params={"year": night.year, "month": night.month},
    )
    return res.json()["nights"][night.isoformat()]


# ─── #9 — history and idempotency ──────────────────────────────────────────────

def test_the_first_decision_ends_up_in_the_history(client, research_program, night):
    request = submit_time_request(client, research_program["id"], night).json()
    review(client, request["id"], notes="Meteo stabile")

    entries = client.get(HISTORY.format(request["id"])).json()
    assert len(entries) == 1
    assert entries[0]["previous_status"] == "pending"
    assert entries[0]["new_status"] == "approved"
    assert entries[0]["notes"] == "Meteo stabile"
    assert entries[0]["decided_at"] is not None


def test_reversal_is_allowed_and_tracked(client, research_program, night):
    request = submit_time_request(client, research_program["id"], night).json()
    review(client, request["id"], notes="Meteo stabile")
    res = review(client, request["id"], status="rejected", notes="Previsioni peggiorate")

    assert res.status_code == 200
    assert res.json()["status"] == "rejected"
    entries = client.get(HISTORY.format(request["id"])).json()
    assert [(v["previous_status"], v["new_status"]) for v in entries] == [
        ("pending", "approved"),
        ("approved", "rejected"),
    ]


def test_the_history_records_who_decided(client_authelia, research_program_authelia):
    from conftest import REVIEWER
    c = client_authelia
    c.post("/telescope-time/requests",
           json=request_body(observer="Mario"),
           headers=REVIEWER)
    c.patch("/telescope-time/requests/1", json={"status": "approved"}, headers=REVIEWER)

    entries = c.get(HISTORY.format(1), headers=REVIEWER).json()
    assert entries[0]["decided_by"] == "anna"


def test_notes_are_not_cleared_when_not_passed(client, research_program, night):
    request = submit_time_request(client, research_program["id"], night).json()
    review(client, request["id"], notes="Meteo stabile")

    res = client.patch(f"/telescope-time/requests/{request['id']}", json={"status": "rejected"})
    assert res.json()["reviewer_notes"] == "Meteo stabile"


def test_unchanged_status_does_not_duplicate_the_history(client, research_program, night):
    request = submit_time_request(client, research_program["id"], night).json()
    review(client, request["id"])
    review(client, request["id"])          # double click on the button

    assert len(client.get(HISTORY.format(request["id"])).json()) == 1


def test_history_of_nonexistent_request(client):
    assert client.get(HISTORY.format(999)).status_code == 404


def test_empty_history_for_never_decided_request(client, research_program, night):
    request = submit_time_request(client, research_program["id"], night).json()
    assert client.get(HISTORY.format(request["id"])).json() == []


# ─── #4 — multiple observations in the same night ─────────────────────────────

def test_two_approvals_in_the_same_night_are_allowed(client, research_program, night):
    other = client.post("/telescope-time/research-programs", json={"name": "Comete"}).json()
    first = submit_time_request(client, research_program["id"], night, hour=21, duration=2).json()
    second = submit_time_request(client, other["id"], night, hour=23, duration=2).json()

    assert review(client, first["id"]).status_code == 200
    assert review(client, second["id"]).status_code == 200


def test_the_calendar_counts_approved_requests(client, research_program, night):
    other = client.post("/telescope-time/research-programs", json={"name": "Comete"}).json()
    first = submit_time_request(client, research_program["id"], night, hour=21, duration=2).json()
    second = submit_time_request(client, other["id"], night, hour=23, duration=2).json()
    review(client, first["id"])
    review(client, second["id"])

    booked_night = calendar_of(client, night)
    assert booked_night["approved_count"] == 2
    assert booked_night["night_status"] == "booked"


def test_night_with_a_single_approved_request(client, research_program, night):
    request = submit_time_request(client, research_program["id"], night).json()
    review(client, request["id"])

    assert calendar_of(client, night)["approved_count"] == 1


def test_pending_only_night_has_no_approvals(client, research_program, night):
    submit_time_request(client, research_program["id"], night)

    pending_night = calendar_of(client, night)
    assert pending_night["approved_count"] == 0
    assert pending_night["night_status"] == "pending"
