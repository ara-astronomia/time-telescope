from conftest import review, submit_time_request


def test_submit_request(client, research_program, night):
    res = submit_time_request(client, research_program["id"], night)
    assert res.status_code == 201
    body = res.json()
    assert body["status"] == "pending"
    assert body["research_program_name"] == research_program["name"]
    assert body["updated_at"] is None


def test_nonexistent_research_program_returns_404(client, night):
    assert submit_time_request(client, 999, night).status_code == 404


def test_double_request_same_program_and_date_returns_409(client, research_program, night):
    submit_time_request(client, research_program["id"], night)
    res = submit_time_request(client, research_program["id"], night, observer="Luigi Bianchi")
    assert res.status_code == 409


def test_different_programs_can_request_the_same_date(client, research_program, night):
    other = client.post("/telescope-time/research-programs", json={"name": "Comete"}).json()
    assert submit_time_request(client, research_program["id"], night).status_code == 201
    assert submit_time_request(client, other["id"], night).status_code == 201


def test_after_a_rejection_the_date_is_requestable_again(client, research_program, night):
    first = submit_time_request(client, research_program["id"], night).json()
    review(client, first["id"], status="rejected")
    assert submit_time_request(client, research_program["id"], night).status_code == 201


def test_approval(client, research_program, night):
    request = submit_time_request(client, research_program["id"], night).json()
    res = review(client, request["id"], notes="Meteo previsto sereno")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "approved"
    assert body["reviewer_notes"] == "Meteo previsto sereno"
    assert body["updated_at"] is not None


def test_invalid_status_rejected(client, research_program, night):
    """With a typed status, Pydantic does the validation: 422, not 400."""
    request = submit_time_request(client, research_program["id"], night).json()
    res = review(client, request["id"], status="forse")
    assert res.status_code == 422
    assert res.json()["detail"][0]["loc"] == ["body", "status"]


def test_invalid_status_does_not_change_the_request(client, research_program, night):
    request = submit_time_request(client, research_program["id"], night).json()
    review(client, request["id"], status="forse")
    after = client.get("/telescope-time/requests").json()[0]
    assert after["status"] == "pending"
    assert after["updated_at"] is None


def test_patch_on_nonexistent_request_returns_404(client):
    assert review(client, 999).status_code == 404


def test_filter_by_status(client, research_program, night, other_night):
    approved = submit_time_request(client, research_program["id"], night).json()
    submit_time_request(client, research_program["id"], other_night)
    review(client, approved["id"])

    pending = client.get("/telescope-time/requests", params={"status": "pending"}).json()
    assert [r["requested_night"] for r in pending] == [other_night.isoformat()]
    assert len(client.get("/telescope-time/requests").json()) == 2


def test_statistics(client, research_program, night, other_night):
    request = submit_time_request(client, research_program["id"], night).json()
    submit_time_request(client, research_program["id"], other_night)
    review(client, request["id"])

    stats = client.get("/telescope-time/statistics").json()
    assert {r["status"]: r["count"] for r in stats["by_status"]} == {
        "approved": 1,
        "pending": 1,
    }
    assert stats["by_research_program"][0] == {"name": "Supernovae", "request_count": 2, "approved_count": 1}


# ─── Reading a single request ──────────────────────────────────────────────────

def test_request_detail(client, research_program, night):
    created = submit_time_request(client, research_program["id"], night).json()

    res = client.get(f"/telescope-time/requests/{created['id']}")

    assert res.status_code == 200
    assert res.json() == created


def test_nonexistent_request_detail(client):
    res = client.get("/telescope-time/requests/999")
    assert res.status_code == 404
    assert res.json()["detail"] == "Richiesta non trovata."


def test_the_detail_reflects_decisions(client, research_program, night):
    created = submit_time_request(client, research_program["id"], night).json()
    review(client, created["id"], notes="Meteo stabile")

    detail = client.get(f"/telescope-time/requests/{created['id']}").json()

    assert detail["status"] == "approved"
    assert detail["reviewer_notes"] == "Meteo stabile"
    assert detail["updated_at"] is not None
