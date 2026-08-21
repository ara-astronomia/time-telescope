def test_empty_list_at_startup(client):
    res = client.get("/telescope-time/research-programs")
    assert res.status_code == 200
    assert res.json() == []


def test_create_research_program(client):
    res = client.post(
        "/telescope-time/research-programs",
        json={"name": "Comete", "description": "Monitoraggio chiome", "specs": "Filtro R"},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["name"] == "Comete"
    assert body["description"] == "Monitoraggio chiome"
    assert body["id"] > 0


def test_duplicate_name_returns_409(client, research_program):
    res = client.post("/telescope-time/research-programs", json={"name": research_program["name"]})
    assert res.status_code == 409
    assert research_program["name"] in res.json()["detail"]


def test_name_normalized_of_spaces(client):
    client.post("/telescope-time/research-programs", json={"name": "  Asteroidi  "})
    assert client.get("/telescope-time/research-programs").json()[0]["name"] == "Asteroidi"


def test_nonexistent_research_program_detail(client):
    assert client.get("/telescope-time/research-programs/999").status_code == 404


def test_list_ordered_by_name(client):
    for name in ("Zodiacale", "Asteroidi", "Meteore"):
        client.post("/telescope-time/research-programs", json={"name": name})
    names = [r["name"] for r in client.get("/telescope-time/research-programs").json()]
    assert names == ["Asteroidi", "Meteore", "Zodiacale"]
