"""I timestamp sono scritti in UTC: devono dirlo (#7).

`datetime('now')` di SQLite produce '2026-08-17 06:30:00', che non è ISO 8601
valido — spazio invece di T, nessun fuso — e i browser lo interpretano come ora
locale, sfalsando di un'ora in inverno e due in estate.
"""

from datetime import datetime, timedelta, timezone

from conftest import review, submit_time_request


def parse(value):
    """Fallisce se il timestamp non è ISO 8601 con fuso esplicito."""
    instant = datetime.fromisoformat(value)
    assert instant.tzinfo is not None, f"timestamp senza fuso: {value!r}"
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
    """Il formato cambia: l'ordinamento lessicografico usato dalle query deve
    continuare a coincidere con quello cronologico."""
    first = submit_time_request(client, research_program["id"], night).json()
    second = submit_time_request(client, research_program["id"], other_night).json()
    assert first["created_at"] <= second["created_at"]
    assert parse(first["created_at"]) <= parse(second["created_at"])
