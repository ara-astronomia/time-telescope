"""The observatory's timezone must be readable by the frontend, to compute
the "start" field's minimum and state the observatory-local convention on
the form the same way the server does, instead of the visiting browser's own
timezone.
"""

import pytest
from fastapi.testclient import TestClient

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
    monkeypatch.setenv("TZ", "Pacific/Kiritimati")

    res = client.get(OBSERVATORY)

    assert res.json() == {"timezone": "Pacific/Kiritimati"}


def test_an_invalid_timezone_fails_the_app_at_startup(tmp_path, monkeypatch):
    """A typo'd TZ must be caught once at boot, not on the first request
    that happens to touch it."""
    monkeypatch.setenv("TELESCOPE_DB_PATH", str(tmp_path / "telescope_test.db"))
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("TZ", "Not/AValidZone")
    import main

    with pytest.raises(RuntimeError, match="TZ"):
        with TestClient(main.app):
            pass
