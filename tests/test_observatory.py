"""The observatory's timezone must be readable by the frontend, to compute
the "start" field's minimum and state the observatory-local convention on
the form the same way the server does, instead of the visiting browser's own
timezone.
"""

from conftest import MEMBER

OBSERVATORY = "/telescope-time/observatory"


def test_no_header_returns_401(client_authelia):
    """Same as every other endpoint under the router: Nginx/Authelia gate
    every real request before it reaches the app, so a request with no
    identity means the container is being hit directly, bypassing them."""
    res = client_authelia.get(OBSERVATORY)

    assert res.status_code == 401


def test_an_authenticated_member_can_read_it(client_authelia):
    res = client_authelia.get(OBSERVATORY, headers=MEMBER)

    assert res.status_code == 200


def test_the_default_timezone_is_europe_rome(client):
    res = client.get(OBSERVATORY)

    assert res.status_code == 200
    assert res.json() == {"timezone": "Europe/Rome"}


def test_the_timezone_reflects_the_environment_variable(client, monkeypatch):
    monkeypatch.setenv("OBSERVATORY_TZ", "Pacific/Kiritimati")

    res = client.get(OBSERVATORY)

    assert res.json() == {"timezone": "Pacific/Kiritimati"}
