"""The calendar is the only non-trivial logic of the service: it derives the
status of each night from the time slots of the requests that occupy it.

| Night's situation                              | night_status |
|-------------------------------------------------|--------------|
| no request                                       | (absent)     |
| pending requests that don't overlap              | pending      |
| two or more pending requests with overlapping slots | contested |
| at least one approved                            | booked       |
"""

from calendar import isleap, monthrange
from datetime import date, datetime, timedelta

import pytest

from conftest import review, submit_time_request


@pytest.fixture
def month():
    """Next month: entirely in the future, like the requests it accepts."""
    today = date.today()
    return date(today.year + (today.month == 12), today.month % 12 + 1, 1)


@pytest.fixture
def other_research_program(client):
    return client.post("/telescope-time/research-programs", json={"name": "Comete"}).json()


def nights_of(client, when):
    res = client.get(
        "/telescope-time/calendar", params={"year": when.year, "month": when.month}
    )
    assert res.status_code == 200
    return res.json()["nights"]


def night_of(client, when):
    return nights_of(client, when)[when.isoformat()]


# ─── Night status ──────────────────────────────────────────────────────────────

def test_night_without_requests_does_not_appear(client, research_program, month):
    assert nights_of(client, month) == {}


def test_a_single_pending_request_is_not_contested(client, research_program, month):
    """Whoever submits the only request of the night must not read 'contested'
    and start looking for the others."""
    when = month.replace(day=12)
    submit_time_request(client, research_program["id"], when)

    assert night_of(client, when)["night_status"] == "pending"


def test_two_disjoint_pending_requests_are_not_contested(client, research_program, other_research_program, month):
    when = month.replace(day=12)
    submit_time_request(client, research_program["id"], when, hour=21, duration=2)
    submit_time_request(client, other_research_program["id"], when, hour=23, duration=2)

    night = night_of(client, when)
    assert night["night_status"] == "pending"
    assert night["overlaps"] == []


def test_two_overlapping_pending_requests_are_contested(client, research_program, other_research_program, month):
    when = month.replace(day=12)
    submit_time_request(client, research_program["id"], when, hour=21, duration=3)
    submit_time_request(client, other_research_program["id"], when, hour=23, duration=2)

    assert night_of(client, when)["night_status"] == "contested"


def test_one_approved_request_books_the_night(client, research_program, month):
    when = month.replace(day=12)
    request = submit_time_request(client, research_program["id"], when).json()
    review(client, request["id"])

    assert night_of(client, when)["night_status"] == "booked"


def test_approved_takes_precedence_over_pending(client, research_program, other_research_program, month):
    when = month.replace(day=12)
    approved = submit_time_request(client, research_program["id"], when, hour=21, duration=3).json()
    submit_time_request(client, other_research_program["id"], when, hour=23, duration=2)
    review(client, approved["id"])

    night = night_of(client, when)
    assert night["night_status"] == "booked"
    assert len(night["requests"]) == 2


def test_rejected_requests_are_excluded(client, research_program, month):
    when = month.replace(day=12)
    request = submit_time_request(client, research_program["id"], when).json()
    review(client, request["id"], status="rejected")

    assert when.isoformat() not in nights_of(client, month)


# ─── Counts and conflicts ───────────────────────────────────────────────────────

def test_the_night_exposes_the_counts(client, research_program, other_research_program, month):
    when = month.replace(day=12)
    approved = submit_time_request(client, research_program["id"], when, hour=21, duration=2).json()
    submit_time_request(client, other_research_program["id"], when, hour=23, duration=2)
    review(client, approved["id"])

    night = night_of(client, when)
    assert (night["approved_count"], night["pending_count"]) == (1, 1)


def test_overlapping_pairs_are_exposed(client, research_program, other_research_program, month):
    """The reviewer doesn't just need to know there's a conflict: they need to
    know between which requests, in order to decide what to move."""
    when = month.replace(day=12)
    first = submit_time_request(client, research_program["id"], when, hour=21, duration=3).json()
    second = submit_time_request(client, other_research_program["id"], when, hour=23, duration=2).json()

    assert night_of(client, when)["overlaps"] == [[first["id"], second["id"]]]


def test_a_slot_crossing_the_threshold_is_rejected(client, research_program, month):
    """Two nights are consecutive, disjoint noon-to-noon windows: a slot
    inside its own night can never touch the following one."""
    when = month.replace(day=12)
    res = submit_time_request(client, research_program["id"], when, hour=11, duration=2)

    assert res.status_code == 422


# ─── Month boundaries ────────────────────────────────────────────────────────────

def test_only_the_requested_month(client, research_program, month):
    last_day = month.replace(day=monthrange(month.year, month.month)[1])
    first_of_next = last_day + timedelta(days=1)
    submit_time_request(client, research_program["id"], last_day)
    submit_time_request(client, research_program["id"], first_of_next)

    assert list(nights_of(client, month)) == [last_day.isoformat()]
    assert list(nights_of(client, first_of_next)) == [first_of_next.isoformat()]


def test_month_boundaries_are_included(client, research_program, month):
    last_day = month.replace(day=monthrange(month.year, month.month)[1])
    submit_time_request(client, research_program["id"], month)
    submit_time_request(client, research_program["id"], last_day)

    assert set(nights_of(client, month)) == {month.isoformat(), last_day.isoformat()}


def test_last_day_of_leap_february(client, research_program):
    """The month's range is computed with monthrange: the 29th isn't lost."""
    year = date.today().year + 1
    while not isleap(year):
        year += 1
    leap_day = date(year, 2, 29)
    submit_time_request(client, research_program["id"], leap_day)

    assert leap_day.isoformat() in nights_of(client, leap_day)


# ─── Night content ───────────────────────────────────────────────────────────────

def test_request_detail_within_the_night(client, research_program, month):
    """The observer isn't a typed-in name: it's the authenticated identity."""
    when = month.replace(day=12)
    submit_time_request(client, research_program["id"], when, hour=22, duration=3)

    request = night_of(client, when)["requests"][0]
    assert request["observer"] == "Marta Conti"   # full name of the default DEV_USER
    assert request["research_program_name"] == "Supernovae"
    assert request["status"] == "pending"
    assert request["start"] == f"{when}T22:00:00"
    assert request["end"] == f"{when + timedelta(days=1)}T01:00:00"


def test_without_parameters_uses_the_current_month(client):
    res = client.get("/telescope-time/calendar")
    assert res.status_code == 200

    today = date.today()
    assert (res.json()["year"], res.json()["month"]) == (today.year, today.month)


def test_the_default_month_is_the_observatorys_not_the_systems(client, monkeypatch):
    """"Today" for the default year/month must be the observatory's calendar
    day, not the process's OS timezone — otherwise a container without
    OBSERVATORY_TZ set can default to the wrong month right around a day
    boundary."""
    import router
    monkeypatch.setattr(router, "now_at_observatory", lambda: datetime(2027, 1, 5, 3, 0))

    res = client.get("/telescope-time/calendar")

    assert (res.json()["year"], res.json()["month"]) == (2027, 1)
