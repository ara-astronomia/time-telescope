"""A time request records a start and an end instant: overlap becomes
measurable instead of assumed — two research programs can share a night,
not the same instant on the same instrument.
"""

from datetime import timedelta

import pytest

from conftest import future_night, review, submit_time_request, time_slot


def submit_slot(client, research_program_id, start, end):
    return client.post(
        "/telescope-time/requests",
        json={"research_program_id": research_program_id, "start": start, "end": end},
    )


# ─── The time slot ─────────────────────────────────────────────────────────────

def test_the_request_records_start_and_end(client, research_program):
    night = future_night()
    res = submit_time_request(client, research_program["id"], night, hour=22, duration=3)

    assert res.status_code == 201
    body = res.json()
    assert body["start"] == f"{night}T22:00:00"
    assert body["end"] == f"{night + timedelta(days=1)}T01:00:00"


def test_the_reference_night_is_the_start_night(client, research_program):
    """A session that crosses midnight belongs to the night it started in:
    that's the astronomical convention, and it's what the calendar groups
    by."""
    night = future_night()
    body = submit_time_request(client, research_program["id"], night, hour=23, duration=4).json()

    assert body["requested_night"] == night.isoformat()


def test_a_session_after_midnight_belongs_to_the_previous_night(client, research_program):
    """A start at 01:00 still belongs to the night that began the evening
    before: the threshold is noon, not the calendar's midnight."""
    night = future_night()
    body = submit_time_request(client, research_program["id"], night, hour=1, duration=3).json()

    assert body["requested_night"] == (night - timedelta(days=1)).isoformat()


def test_the_slot_is_exposed_on_read(client, research_program):
    created = submit_time_request(client, research_program["id"], future_night()).json()
    read = client.get(f"/telescope-time/requests/{created['id']}").json()

    assert (read["start"], read["end"]) == (created["start"], created["end"])


# ─── Validation ─────────────────────────────────────────────────────────────────

def test_end_before_start_is_rejected(client, research_program):
    start, end = time_slot(future_night())
    res = submit_slot(client, research_program["id"], end, start)

    assert res.status_code == 422
    assert res.headers["content-type"] == "application/json"
    assert res.json()["detail"][0]["loc"] == ["body", "end"]


def test_zero_duration_slot_is_rejected(client, research_program):
    start, _ = time_slot(future_night())
    res = submit_slot(client, research_program["id"], start, start)

    assert res.status_code == 422
    assert res.json()["detail"][0]["loc"] == ["body", "end"]


def test_start_in_the_past_is_rejected(client, research_program):
    start, end = time_slot(future_night(-1))
    res = submit_slot(client, research_program["id"], start, end)

    assert res.status_code == 422
    assert res.json()["detail"][0]["loc"] == ["body", "start"]


@pytest.mark.parametrize("value", ["tomorrow", "2026-13-45T22:00", "", "22:00"])
def test_invalid_datetime_is_rejected(client, research_program, value):
    _, end = time_slot(future_night())
    res = submit_slot(client, research_program["id"], value, end)

    assert res.status_code == 422
    assert res.json()["detail"][0]["loc"] == ["body", "start"]


def test_timezone_offset_is_rejected(client, research_program):
    """Instants are the observatory's local time: storing one with an
    offset would make the stored slots no longer comparable with each
    other."""
    night = future_night()
    res = submit_slot(
        client, research_program["id"], f"{night}T22:00:00+02:00", f"{night}T23:00:00"
    )

    assert res.status_code == 422
    assert res.json()["detail"][0]["loc"] == ["body", "start"]


def test_invalid_slot_does_not_write_to_the_database(client, research_program):
    start, end = time_slot(future_night())
    submit_slot(client, research_program["id"], end, start)

    assert client.get("/telescope-time/requests").json() == []


# ─── One night per request ──────────────────────────────────────────────────────

def test_slot_within_the_night_is_accepted(client, research_program):
    start, end = time_slot(future_night(), hour=22, duration=4)
    res = submit_slot(client, research_program["id"], start, end)

    assert res.status_code == 201


def test_slot_in_the_second_half_of_the_night_is_accepted(client, research_program):
    """A session that starts after midnight belongs to the previous night
    and is accepted."""
    start, end = time_slot(future_night(), hour=1, duration=3)
    res = submit_slot(client, research_program["id"], start, end)

    assert res.status_code == 201


def test_slot_of_exactly_24_hours_is_accepted(client, research_program):
    start, end = time_slot(future_night(), hour=12, duration=24)
    res = submit_slot(client, research_program["id"], start, end)

    assert res.status_code == 201


def test_slot_spanning_two_nights_is_rejected(client, research_program):
    start, end = time_slot(future_night(), hour=22, duration=27)
    res = submit_slot(client, research_program["id"], start, end)

    assert res.status_code == 422
    assert res.json()["detail"][0]["loc"] == ["body", "end"]


def test_slot_past_the_threshold_is_rejected_even_under_24_hours(client, research_program):
    """17 hours of duration, well under the limit — but it ends past 12:00
    the next day: the constraint is on the window, not on the total
    duration."""
    start, end = time_slot(future_night(), hour=20, duration=17)
    res = submit_slot(client, research_program["id"], start, end)

    assert res.status_code == 422


# ─── Overlap: allowed while pending, blocked on approval ───────────────────────

def test_two_requests_can_compete_for_the_same_slot(client, research_program):
    other = client.post("/telescope-time/research-programs", json={"name": "Comete"}).json()
    night = future_night()

    assert submit_time_request(client, research_program["id"], night).status_code == 201
    assert submit_time_request(client, other["id"], night).status_code == 201


def test_disjoint_slots_in_the_same_night_both_get_approved(client, research_program):
    other = client.post("/telescope-time/research-programs", json={"name": "Comete"}).json()
    night = future_night()
    first = submit_time_request(client, research_program["id"], night, hour=21, duration=2).json()
    second = submit_time_request(client, other["id"], night, hour=23, duration=2).json()

    assert review(client, first["id"]).status_code == 200
    assert review(client, second["id"]).status_code == 200


def test_approving_an_overlapping_slot_returns_409(client, research_program):
    other = client.post("/telescope-time/research-programs", json={"name": "Comete"}).json()
    night = future_night()
    first = submit_time_request(client, research_program["id"], night, hour=21, duration=3).json()
    second = submit_time_request(client, other["id"], night, hour=23, duration=2).json()
    review(client, first["id"])

    res = review(client, second["id"])

    assert res.status_code == 409
    assert res.headers["content-type"] == "application/json"
    detail = res.json()["detail"]
    assert f"#{first['id']}" in detail, detail
    assert research_program["name"] in detail, detail


def test_the_conflicting_request_stays_pending(client, research_program):
    other = client.post("/telescope-time/research-programs", json={"name": "Comete"}).json()
    night = future_night()
    first = submit_time_request(client, research_program["id"], night, hour=21, duration=3).json()
    second = submit_time_request(client, other["id"], night, hour=23, duration=2).json()
    review(client, first["id"])
    review(client, second["id"])

    after = client.get(f"/telescope-time/requests/{second['id']}").json()
    assert after["status"] == "pending"
    assert after["updated_at"] is None
    assert client.get(f"/telescope-time/requests/{second['id']}/history").json() == []


def test_rejecting_is_not_blocked_by_overlap(client, research_program):
    other = client.post("/telescope-time/research-programs", json={"name": "Comete"}).json()
    night = future_night()
    first = submit_time_request(client, research_program["id"], night, hour=21, duration=3).json()
    second = submit_time_request(client, other["id"], night, hour=23, duration=2).json()
    review(client, first["id"])

    assert review(client, second["id"], status="rejected").status_code == 200


def test_a_rejected_request_does_not_occupy_the_slot(client, research_program):
    other = client.post("/telescope-time/research-programs", json={"name": "Comete"}).json()
    night = future_night()
    first = submit_time_request(client, research_program["id"], night, hour=21, duration=3).json()
    second = submit_time_request(client, other["id"], night, hour=23, duration=2).json()
    review(client, first["id"], status="rejected")

    assert review(client, second["id"]).status_code == 200


def test_reapproving_the_same_request_is_not_a_conflict(client, research_program):
    request = submit_time_request(client, research_program["id"], future_night()).json()
    review(client, request["id"])

    assert review(client, request["id"]).status_code == 200


def test_a_slot_straddling_the_threshold_is_rejected(client, research_program):
    """Two nights are disjoint 12:00-to-12:00 windows: a slot within its
    own night can never touch the next one."""
    start, end = time_slot(future_night(), hour=11, duration=2)
    res = submit_slot(client, research_program["id"], start, end)

    assert res.status_code == 422


def test_contiguous_slots_do_not_overlap(client, research_program):
    """One's end and the other's start coincide: that's not overlap."""
    other = client.post("/telescope-time/research-programs", json={"name": "Comete"}).json()
    night = future_night()
    first = submit_time_request(client, research_program["id"], night, hour=22, duration=2).json()
    second = submit_time_request(
        client, other["id"], night + timedelta(days=1), hour=0, duration=2
    ).json()
    assert first["end"] == second["start"]
    review(client, first["id"])

    assert review(client, second["id"]).status_code == 200


def test_only_reviewers_hit_the_constraint(client_authelia, research_program_authelia):
    """The 409 must not become a way to discover someone else's status:
    someone who can't approve still gets 403 regardless."""
    from conftest import MEMBER, REVIEWER

    night = future_night()
    start, end = time_slot(night, hour=21, duration=3)
    first = client_authelia.post(
        "/telescope-time/requests",
        json={"research_program_id": research_program_authelia["id"], "start": start, "end": end},
        headers=REVIEWER,
    ).json()
    client_authelia.patch(
        f"/telescope-time/requests/{first['id']}",
        json={"status": "approved"}, headers=REVIEWER,
    )

    other = client_authelia.post(
        "/telescope-time/research-programs", json={"name": "Comete"}, headers=REVIEWER
    ).json()
    start, end = time_slot(night, hour=23, duration=2)
    second = client_authelia.post(
        "/telescope-time/requests",
        json={"research_program_id": other["id"], "start": start, "end": end},
        headers=MEMBER,
    ).json()

    res = client_authelia.patch(
        f"/telescope-time/requests/{second['id']}",
        json={"status": "approved"}, headers=MEMBER,
    )
    assert res.status_code == 403
