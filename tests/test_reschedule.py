"""Reviewers reschedule a request's date and time.

Without this, a wrong date has no way to be corrected other than rejecting
the request and making the observer start over, losing the original
request. It is also how two requests contending for the same slot get
unblocked: rescheduling one, not rejecting one.
"""

from datetime import datetime, timedelta

import pytest

from conftest import MEMBER, REVIEWER, future_night, review, submit_time_request, time_slot

SCHEDULE = "/telescope-time/requests/{}/schedule"
HISTORY = "/telescope-time/requests/{}/history"

OTHER_MEMBER = {"Remote-User": "luigi", "Remote-Groups": "soci",
                "Remote-Email": "luigi@example.test"}


def reschedule(client, request_id, night, hour=22, duration=3, reason=None, headers=None):
    start, end = time_slot(night, hour, duration)
    body = {"start": start, "end": end}
    if reason is not None:
        body["reason"] = reason
    return client.patch(SCHEDULE.format(request_id), json=body, headers=headers or {})


@pytest.fixture
def time_request(client, research_program, night):
    return submit_time_request(client, research_program["id"], night, hour=22, duration=3).json()


# ─── Rescheduling ───────────────────────────────────────────────────────────

def test_reviewer_reschedules_a_pending_request(client, time_request, other_night):
    res = reschedule(client, time_request["id"], other_night, hour=23, duration=4)

    assert res.status_code == 200
    body = res.json()
    assert body["start"] == f"{other_night}T23:00:00"
    assert body["end"] == f"{other_night + timedelta(days=1)}T03:00:00"


def test_the_reschedule_updates_the_reference_night(client, time_request, other_night):
    """`requested_night` is derived: without recomputing it, the calendar
    keeps showing the request on the night it was moved away from."""
    res = reschedule(client, time_request["id"], other_night, hour=23, duration=4)

    assert res.json()["requested_night"] == other_night.isoformat()


def test_a_reschedule_past_midnight_updates_the_previous_night(client, time_request, other_night):
    """A reschedule to 1am updates `requested_night` to the previous night,
    not to the night of the new calendar day."""
    res = reschedule(client, time_request["id"], other_night, hour=1, duration=3)

    assert res.json()["requested_night"] == (other_night - timedelta(days=1)).isoformat()


def test_an_approved_request_can_also_be_rescheduled(client, time_request, other_night):
    review(client, time_request["id"])

    res = reschedule(client, time_request["id"], other_night)

    assert res.status_code == 200
    assert res.json()["status"] == "approved"


def test_a_rejected_request_can_also_be_rescheduled(client, time_request, other_night):
    """Rescheduling and then re-approving is the only order that works:
    re-approving first would mean doing it on the original slot, which
    might meanwhile be occupied."""
    review(client, time_request["id"], status="rejected")

    res = reschedule(client, time_request["id"], other_night)

    assert res.status_code == 200
    assert res.json()["status"] == "rejected"


def test_the_reviewer_can_reschedule_into_the_past(client, time_request):
    """Used to record after the fact an observation that really happened.
    The constraint is not on the date, it's on clarity: the interface
    states it."""
    yesterday = future_night(-1)

    res = reschedule(client, time_request["id"], yesterday)

    assert res.status_code == 200
    assert res.json()["requested_night"] == yesterday.isoformat()


def test_the_reason_is_optional(client, time_request, other_night):
    assert reschedule(client, time_request["id"], other_night).status_code == 200


# ─── Validation ─────────────────────────────────────────────────────────────

def test_end_before_start_is_rejected(client, time_request, other_night):
    start, end = time_slot(other_night)
    res = client.patch(SCHEDULE.format(time_request["id"]), json={"start": end, "end": start})

    assert res.status_code == 422
    assert res.headers["content-type"] == "application/json"
    assert res.json()["detail"][0]["loc"] == ["body", "end"]


def test_a_timezone_aware_instant_is_rejected(client, time_request, other_night):
    res = client.patch(
        SCHEDULE.format(time_request["id"]),
        json={"start": f"{other_night}T22:00:00+02:00", "end": f"{other_night}T23:00:00"},
    )

    assert res.status_code == 422
    assert res.json()["detail"][0]["loc"] == ["body", "start"]


def test_the_reviewer_cannot_reschedule_to_a_slot_spanning_two_nights(client, time_request, other_night):
    """The time-slot constraint is shared: it also applies to the reviewer,
    who reschedules without any other status or date restriction."""
    res = reschedule(client, time_request["id"], other_night, hour=22, duration=27)

    assert res.status_code == 422


def test_rescheduling_a_nonexistent_request(client, other_night):
    res = reschedule(client, 999, other_night)

    assert res.status_code == 404
    assert res.json()["detail"] == "Richiesta non trovata."


def test_an_invalid_reschedule_does_not_touch_the_request(client, time_request, other_night):
    start, end = time_slot(other_night)
    client.patch(SCHEDULE.format(time_request["id"]), json={"start": end, "end": start})

    after = client.get(f"/telescope-time/requests/{time_request['id']}").json()
    assert after["start"] == time_request["start"]
    assert client.get(HISTORY.format(time_request["id"])).json() == []


# ─── Authorization (including the owner, under their own constraints) ─────────

def submit_request_as(client_authelia, research_program_id, night, headers):
    start, end = time_slot(night)
    return client_authelia.post(
        "/telescope-time/requests",
        json={"research_program_id": research_program_id, "start": start, "end": end},
        headers=headers,
    ).json()


def test_the_owner_reschedules_their_own_pending_request(
    client_authelia, research_program_authelia, night, other_night
):
    own = submit_request_as(client_authelia, research_program_authelia["id"], night, MEMBER)

    res = reschedule(client_authelia, own["id"], other_night, headers=MEMBER)

    assert res.status_code == 200
    assert res.json()["start"] == f"{other_night}T22:00:00"


def test_another_member_cannot_reschedule_a_request_that_is_not_theirs(
    client_authelia, research_program_authelia, night, other_night
):
    own = submit_request_as(client_authelia, research_program_authelia["id"], night, MEMBER)

    res = reschedule(client_authelia, own["id"], other_night, headers=OTHER_MEMBER)

    assert res.status_code == 403
    after = client_authelia.get(
        f"/telescope-time/requests/{own['id']}", headers=MEMBER
    ).json()
    assert after["start"] == own["start"]


def test_the_owner_cannot_reschedule_an_approved_request(
    client_authelia, research_program_authelia, night, other_night
):
    own = submit_request_as(client_authelia, research_program_authelia["id"], night, MEMBER)
    client_authelia.patch(
        f"/telescope-time/requests/{own['id']}",
        json={"status": "approved"}, headers=REVIEWER,
    )

    res = reschedule(client_authelia, own["id"], other_night, headers=MEMBER)

    assert res.status_code == 409


def test_the_owner_cannot_reschedule_a_rejected_request(
    client_authelia, research_program_authelia, night, other_night
):
    own = submit_request_as(client_authelia, research_program_authelia["id"], night, MEMBER)
    client_authelia.patch(
        f"/telescope-time/requests/{own['id']}",
        json={"status": "rejected"}, headers=REVIEWER,
    )

    res = reschedule(client_authelia, own["id"], other_night, headers=MEMBER)

    assert res.status_code == 409


def test_the_owner_cannot_reschedule_into_the_past(
    client_authelia, research_program_authelia, night
):
    own = submit_request_as(client_authelia, research_program_authelia["id"], night, MEMBER)

    res = reschedule(client_authelia, own["id"], future_night(-5), headers=MEMBER)

    assert res.status_code == 422


def test_the_owner_reschedule_future_check_uses_the_observatory_timezone(
    client_authelia, research_program_authelia, night, observatory_far_ahead_of_the_system_clock
):
    """On the owner's reschedule path too, "into the future" must be the
    observatory's clock, not the process's OS timezone."""
    own = submit_request_as(client_authelia, research_program_authelia["id"], night, MEMBER)

    now_system = datetime.fromisoformat(datetime.now().isoformat())
    start = now_system + timedelta(hours=7)
    end = start + timedelta(hours=1)

    res = client_authelia.patch(
        SCHEDULE.format(own["id"]),
        json={"start": start.isoformat(), "end": end.isoformat()},
        headers=MEMBER,
    )

    assert res.status_code == 422


def test_the_owner_cannot_reschedule_to_a_slot_spanning_two_nights(
    client_authelia, research_program_authelia, night, other_night
):
    own = submit_request_as(client_authelia, research_program_authelia["id"], night, MEMBER)

    res = reschedule(client_authelia, own["id"], other_night, hour=22, duration=27, headers=MEMBER)

    assert res.status_code == 422


# ─── Overlap ────────────────────────────────────────────────────────────────

def test_rescheduling_an_approved_request_onto_an_occupied_slot_returns_409(client, research_program, night):
    other = client.post("/telescope-time/research-programs", json={"name": "Comete"}).json()
    occupied = submit_time_request(client, research_program["id"], night, hour=21, duration=3).json()
    movable = submit_time_request(client, other["id"], night + timedelta(days=2), hour=21).json()
    review(client, occupied["id"])
    review(client, movable["id"])

    res = reschedule(client, movable["id"], night, hour=23, duration=2)

    assert res.status_code == 409
    detail = res.json()["detail"]
    assert f"#{occupied['id']}" in detail, detail


def test_rescheduling_a_pending_request_onto_an_occupied_slot_is_allowed(client, research_program, night):
    """Contention is allowed as long as nobody has approved yet: the
    constraint kicks in at approval time, not before."""
    other = client.post("/telescope-time/research-programs", json={"name": "Comete"}).json()
    occupied = submit_time_request(client, research_program["id"], night, hour=21, duration=3).json()
    movable = submit_time_request(client, other["id"], night + timedelta(days=2), hour=21).json()
    review(client, occupied["id"])

    assert reschedule(client, movable["id"], night, hour=23, duration=2).status_code == 200


def test_a_request_does_not_conflict_with_itself(client, time_request, night):
    review(client, time_request["id"])

    assert reschedule(client, time_request["id"], night, hour=22, duration=4).status_code == 200


def test_a_conflicting_reschedule_leaves_no_trace(client, research_program, night):
    other = client.post("/telescope-time/research-programs", json={"name": "Comete"}).json()
    occupied = submit_time_request(client, research_program["id"], night, hour=21, duration=3).json()
    movable = submit_time_request(client, other["id"], night + timedelta(days=2), hour=21).json()
    review(client, occupied["id"])
    review(client, movable["id"])
    before = client.get(f"/telescope-time/requests/{movable['id']}").json()

    reschedule(client, movable["id"], night, hour=23, duration=2)

    after = client.get(f"/telescope-time/requests/{movable['id']}").json()
    assert after["start"] == before["start"]
    assert len(client.get(HISTORY.format(movable["id"])).json()) == 1   # the approval only


def test_rescheduling_unblocks_a_double_approval(client, research_program, night):
    """The scenario the endpoint exists for: two requests contend for the
    same slot, rescheduling one makes both approvable."""
    other = client.post("/telescope-time/research-programs", json={"name": "Comete"}).json()
    first = submit_time_request(client, research_program["id"], night, hour=21, duration=3).json()
    second = submit_time_request(client, other["id"], night, hour=22, duration=3).json()
    review(client, first["id"])
    assert review(client, second["id"]).status_code == 409

    reschedule(client, second["id"], night, hour=0, duration=3)

    assert review(client, second["id"]).status_code == 200


# ─── History ────────────────────────────────────────────────────────────────

def test_the_reschedule_ends_up_in_the_history(client_authelia, research_program_authelia, night, other_night):
    start, end = time_slot(night)
    created = client_authelia.post(
        "/telescope-time/requests",
        json={"research_program_id": research_program_authelia["id"], "start": start, "end": end},
        headers=REVIEWER,
    ).json()

    reschedule(client_authelia, created["id"], other_night, hour=23, duration=4,
               reason="Manutenzione", headers=REVIEWER)

    entry = client_authelia.get(HISTORY.format(created["id"]), headers=REVIEWER).json()[0]
    assert entry["type"] == "reschedule"
    assert entry["previous_start"] == created["start"]
    assert entry["previous_end"] == created["end"]
    assert entry["new_start"] == f"{other_night}T23:00:00"
    assert entry["notes"] == "Manutenzione"
    assert entry["decided_by"] == "anna"
    assert entry["decided_at"].endswith("Z")


def test_decisions_remain_distinguishable_from_reschedules(client, time_request, other_night):
    review(client, time_request["id"], notes="Meteo stabile")
    reschedule(client, time_request["id"], other_night, reason="Manutenzione")

    entries = client.get(HISTORY.format(time_request["id"])).json()
    assert [e["type"] for e in entries] == ["decision", "reschedule"]
    assert entries[0]["new_status"] == "approved"
    assert entries[0]["new_start"] is None
    assert entries[1]["new_status"] is None


def test_a_reschedule_with_unchanged_times_is_not_recorded(client, time_request, night):
    """Double-clicking the button: like for decisions, it isn't an event."""
    reschedule(client, time_request["id"], night, hour=22, duration=3)

    assert client.get(HISTORY.format(time_request["id"])).json() == []


# ─── Email ──────────────────────────────────────────────────────────────────

@pytest.fixture
def email(monkeypatch):
    import router
    sent = []
    monkeypatch.setattr(
        router, "send_message",
        lambda recipient, subject, body: sent.append((recipient, subject, body)),
    )
    return sent


def test_the_observer_is_notified_of_the_reschedule(client, time_request, email, other_night):
    """They got assigned a different time than requested: not something
    they should stumble on by chance opening the calendar."""
    reschedule(client, time_request["id"], other_night, hour=23, duration=4, reason="Manutenzione")

    assert len(email) == 1
    recipient, subject, body = email[0]
    assert recipient == "sviluppo@example.test"
    assert "Manutenzione" in body
    assert "23:00" in body, body


def test_the_notice_reports_the_previous_slot(client, time_request, email, other_night):
    reschedule(client, time_request["id"], other_night, hour=23, duration=4)

    body = email[0][2]
    assert f"{time_request['start'][8:10]}/" in body, body


def test_a_reschedule_into_the_past_is_stated_in_the_notice(client, time_request, email):
    reschedule(client, time_request["id"], future_night(-1))

    body = email[0][2].lower()
    assert "passat" in body, body


def test_no_email_if_the_reschedule_fails(client, research_program, night, email):
    other = client.post("/telescope-time/research-programs", json={"name": "Comete"}).json()
    occupied = submit_time_request(client, research_program["id"], night, hour=21, duration=3).json()
    movable = submit_time_request(client, other["id"], night + timedelta(days=2), hour=21).json()
    review(client, occupied["id"])
    review(client, movable["id"])
    email.clear()

    reschedule(client, movable["id"], night, hour=23, duration=2)

    assert email == []
