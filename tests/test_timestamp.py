"""Timestamps are written in UTC: they must say so (#7).

SQLite's `datetime('now')` produces '2026-08-17 06:30:00', which isn't valid
ISO 8601 — a space instead of T, no timezone — and browsers interpret it as
local time, off by one hour in winter and two in summer.
"""

from datetime import datetime, timedelta, timezone

from conftest import review, submit_time_request


def parse(value):
    """Fails if the timestamp isn't ISO 8601 with an explicit timezone."""
    instant = datetime.fromisoformat(value)
    assert instant.tzinfo is not None, f"timestamp without timezone: {value!r}"
    return instant


def close_to_now(value, tolerance=timedelta(minutes=2)):
    return abs(parse(value) - datetime.now(timezone.utc)) < tolerance


def test_created_at_of_a_research_program(client):
    research_program = client.post("/telescope-time/research-programs", json={"name": "Supernovae"}).json()
    assert research_program["created_at"].endswith("Z")
    assert close_to_now(research_program["created_at"])


def test_created_at_of_a_request(client, research_program, night):
    request = submit_time_request(client, research_program["id"], night).json()
    assert request["created_at"].endswith("Z")
    assert close_to_now(request["created_at"])


def test_updated_at_after_a_decision(client, research_program, night):
    request = submit_time_request(client, research_program["id"], night).json()
    updated_at = review(client, request["id"]).json()["updated_at"]
    assert updated_at.endswith("Z")
    assert close_to_now(updated_at)


def test_decided_at_in_the_history(client, research_program, night):
    request = submit_time_request(client, research_program["id"], night).json()
    review(client, request["id"])
    entry = client.get(f"/telescope-time/requests/{request['id']}/history").json()[0]
    assert entry["decided_at"].endswith("Z")
    assert close_to_now(entry["decided_at"])


def test_created_at_in_the_calendar(client, research_program, night):
    submit_time_request(client, research_program["id"], night)
    night_data = client.get(
        "/telescope-time/calendar",
        params={"year": night.year, "month": night.month},
    ).json()["nights"][night.isoformat()]
    assert night_data["requests"][0]["created_at"].endswith("Z")


def test_creation_order_stays_consistent(client, research_program, night, other_night):
    """The format changes: the lexicographic ordering the queries rely on
    must keep matching chronological order."""
    first = submit_time_request(client, research_program["id"], night).json()
    second = submit_time_request(client, research_program["id"], other_night).json()
    assert first["created_at"] <= second["created_at"]
    assert parse(first["created_at"]) <= parse(second["created_at"])
