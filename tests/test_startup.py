"""The app's `lifespan` (main.py): auto-seeding an empty database in
AUTH_MODE=dev, the one path none of the other fixtures exercise, since
`client` forces AUTO_SEED=false and `client_authelia` runs in forward-auth.
"""

import os

from fastapi.testclient import TestClient


def test_dev_mode_seeds_an_empty_database_on_startup(tmp_path, monkeypatch, isolated_database):
    monkeypatch.setenv("TELESCOPE_DB_PATH", str(tmp_path / "telescope_test.db"))
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.delenv("AUTO_SEED", raising=False)
    import main

    with TestClient(main.app) as client:
        res = client.get("/telescope-time/requests")
        assert res.status_code == 200
        assert len(res.json()) > 0
