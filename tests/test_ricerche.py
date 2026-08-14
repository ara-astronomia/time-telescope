def test_lista_vuota_allavvio(client):
    res = client.get("/telescope-time/ricerche")
    assert res.status_code == 200
    assert res.json() == []


def test_crea_ricerca(client):
    res = client.post(
        "/telescope-time/ricerche",
        json={"nome": "Comete", "descrizione": "Monitoraggio chiome", "specifiche": "Filtro R"},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["nome"] == "Comete"
    assert body["descrizione"] == "Monitoraggio chiome"
    assert body["id"] > 0


def test_nome_duplicato_da_409(client, ricerca):
    res = client.post("/telescope-time/ricerche", json={"nome": ricerca["nome"]})
    assert res.status_code == 409
    assert ricerca["nome"] in res.json()["detail"]


def test_nome_normalizzato_negli_spazi(client):
    client.post("/telescope-time/ricerche", json={"nome": "  Asteroidi  "})
    assert client.get("/telescope-time/ricerche").json()[0]["nome"] == "Asteroidi"


def test_dettaglio_ricerca_inesistente(client):
    assert client.get("/telescope-time/ricerche/999").status_code == 404


def test_lista_ordinata_per_nome(client):
    for nome in ("Zodiacale", "Asteroidi", "Meteore"):
        client.post("/telescope-time/ricerche", json={"nome": nome})
    nomi = [r["nome"] for r in client.get("/telescope-time/ricerche").json()]
    assert nomi == ["Asteroidi", "Meteore", "Zodiacale"]
