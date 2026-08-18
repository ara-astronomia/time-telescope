from conftest import approva, crea_richiesta


def test_invia_richiesta(client, ricerca, giorno):
    res = crea_richiesta(client, ricerca["id"], giorno)
    assert res.status_code == 201
    body = res.json()
    assert body["stato"] == "in_attesa"
    assert body["nome_ricerca"] == ricerca["nome"]
    assert body["aggiornata_il"] is None


def test_ricerca_inesistente_da_404(client, giorno):
    assert crea_richiesta(client, 999, giorno).status_code == 404


def test_doppia_richiesta_stessa_ricerca_e_data_da_409(client, ricerca, giorno):
    crea_richiesta(client, ricerca["id"], giorno)
    res = crea_richiesta(client, ricerca["id"], giorno, osservatore="Luigi Bianchi")
    assert res.status_code == 409


def test_ricerche_diverse_possono_chiedere_la_stessa_data(client, ricerca, giorno):
    altra = client.post("/telescope-time/ricerche", json={"nome": "Comete"}).json()
    assert crea_richiesta(client, ricerca["id"], giorno).status_code == 201
    assert crea_richiesta(client, altra["id"], giorno).status_code == 201


def test_dopo_un_rifiuto_la_data_torna_richiedibile(client, ricerca, giorno):
    prima = crea_richiesta(client, ricerca["id"], giorno).json()
    approva(client, prima["id"], stato="rifiutata")
    assert crea_richiesta(client, ricerca["id"], giorno).status_code == 201


def test_approvazione(client, ricerca, giorno):
    richiesta = crea_richiesta(client, ricerca["id"], giorno).json()
    res = approva(client, richiesta["id"], note="Meteo previsto sereno")
    assert res.status_code == 200
    body = res.json()
    assert body["stato"] == "approvata"
    assert body["note_responsabile"] == "Meteo previsto sereno"
    assert body["aggiornata_il"] is not None


def test_stato_non_valido_rifiutato(client, ricerca, giorno):
    """Con lo stato tipizzato la validazione la fa Pydantic: 422, non 400."""
    richiesta = crea_richiesta(client, ricerca["id"], giorno).json()
    res = approva(client, richiesta["id"], stato="forse")
    assert res.status_code == 422
    assert res.json()["detail"][0]["loc"] == ["body", "stato"]


def test_stato_non_valido_non_modifica_la_richiesta(client, ricerca, giorno):
    richiesta = crea_richiesta(client, ricerca["id"], giorno).json()
    approva(client, richiesta["id"], stato="forse")
    dopo = client.get("/telescope-time/richieste").json()[0]
    assert dopo["stato"] == "in_attesa"
    assert dopo["aggiornata_il"] is None


def test_patch_su_richiesta_inesistente_da_404(client):
    assert approva(client, 999).status_code == 404


def test_filtro_per_stato(client, ricerca, giorno, altro_giorno):
    approvata = crea_richiesta(client, ricerca["id"], giorno).json()
    crea_richiesta(client, ricerca["id"], altro_giorno)
    approva(client, approvata["id"])

    in_attesa = client.get("/telescope-time/richieste", params={"stato": "in_attesa"}).json()
    assert [r["giorno_richiesto"] for r in in_attesa] == [altro_giorno.isoformat()]
    assert len(client.get("/telescope-time/richieste").json()) == 2


def test_statistiche(client, ricerca, giorno, altro_giorno):
    richiesta = crea_richiesta(client, ricerca["id"], giorno).json()
    crea_richiesta(client, ricerca["id"], altro_giorno)
    approva(client, richiesta["id"])

    stats = client.get("/telescope-time/statistiche").json()
    assert {r["stato"]: r["conteggio"] for r in stats["per_stato"]} == {
        "approvata": 1,
        "in_attesa": 1,
    }
    assert stats["per_ricerca"][0] == {"nome": "Supernovae", "richieste": 2, "approvate": 1}


# ─── Lettura di una singola richiesta ─────────────────────────────────────────

def test_dettaglio_richiesta(client, ricerca, giorno):
    creata = crea_richiesta(client, ricerca["id"], giorno).json()

    res = client.get(f"/telescope-time/richieste/{creata['id']}")

    assert res.status_code == 200
    assert res.json() == creata


def test_dettaglio_richiesta_inesistente(client):
    res = client.get("/telescope-time/richieste/999")
    assert res.status_code == 404
    assert res.json()["detail"] == "Richiesta non trovata."


def test_il_dettaglio_riflette_le_decisioni(client, ricerca, giorno):
    creata = crea_richiesta(client, ricerca["id"], giorno).json()
    approva(client, creata["id"], note="Meteo stabile")

    dettaglio = client.get(f"/telescope-time/richieste/{creata['id']}").json()

    assert dettaglio["stato"] == "approvata"
    assert dettaglio["note_responsabile"] == "Meteo stabile"
    assert dettaglio["aggiornata_il"] is not None
