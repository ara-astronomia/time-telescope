"""GET /telescope-time/ephemeris: sun/moon times for a given night, for the
frontend to show while the observer is still picking a time slot (#35, #60).
"""

from conftest import MEMBER

EPHEMERIS = "/telescope-time/ephemeris"


def test_no_header_returns_401(client_authelia):
    res = client_authelia.get(EPHEMERIS, params={"night": "2026-08-17"})
    assert res.status_code == 401


def test_returns_sun_and_moon_times(client):
    res = client.get(EPHEMERIS, params={"night": "2026-08-17"})

    assert res.status_code == 200
    body = res.json()
    assert body["night"] == "2026-08-17"
    assert body["sun"]["sunset"].startswith("2026-08-17T20:07")
    assert body["sun"]["dusk"].startswith("2026-08-17T21:52")
    assert body["sun"]["dawn"].startswith("2026-08-18T04:36")
    assert body["sun"]["sunrise"].startswith("2026-08-18T06:21")
    assert 4 < body["moon"]["phase"] < 5
    assert body["moon"]["moonrise"].startswith("2026-08-17T11:37")
    assert body["moon"]["moonset"].startswith("2026-08-17T22:09")
    assert 33 < body["moon"]["culmination_altitude"] < 35


def test_missing_night_is_a_validation_error(client):
    res = client.get(EPHEMERIS)
    assert res.status_code == 422


def test_malformed_night_is_a_validation_error(client):
    res = client.get(EPHEMERIS, params={"night": "not-a-date"})
    assert res.status_code == 422


def test_a_member_can_read_it(client_authelia):
    res = client_authelia.get(EPHEMERIS, params={"night": "2026-08-17"}, headers=MEMBER)
    assert res.status_code == 200
