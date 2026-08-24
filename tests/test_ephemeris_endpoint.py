"""GET /telescope-time/ephemeris: sun times for a given night, for the
frontend to show while the observer is still picking a time slot.
"""

from conftest import MEMBER

EPHEMERIS = "/telescope-time/ephemeris"


def test_no_header_returns_401(client_authelia):
    res = client_authelia.get(EPHEMERIS, params={"night": "2026-08-17"})
    assert res.status_code == 401


def test_returns_sun_times(client):
    res = client.get(EPHEMERIS, params={"night": "2026-08-17"})

    assert res.status_code == 200
    body = res.json()
    assert body["night"] == "2026-08-17"
    assert body["sun"]["sunset"].startswith("2026-08-17T20:07")
    assert body["sun"]["dusk"].startswith("2026-08-17T21:52")
    assert body["sun"]["dawn"].startswith("2026-08-18T04:36")
    assert body["sun"]["sunrise"].startswith("2026-08-18T06:21")


def test_missing_night_is_a_validation_error(client):
    res = client.get(EPHEMERIS)
    assert res.status_code == 422


def test_malformed_night_is_a_validation_error(client):
    res = client.get(EPHEMERIS, params={"night": "not-a-date"})
    assert res.status_code == 422


def test_a_member_can_read_it(client_authelia):
    res = client_authelia.get(EPHEMERIS, params={"night": "2026-08-17"}, headers=MEMBER)
    assert res.status_code == 200
