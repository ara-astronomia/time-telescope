from conftest import approva, crea_richiesta


def test_invia_richiesta(client, ricerca):
    res = crea_richiesta(client, ricerca["id"], "2026-09-12")
    assert res.status_code == 201
    body = res.json()
    assert body["stato"] == "in_attesa"
    assert body["nome_ricerca"] == ricerca["nome"]
    assert body["aggiornata_il"] is None


def test_ricerca_inesistente_da_404(client):
    assert crea_richiesta(client, 999, "2026-09-12").status_code == 404


def test_doppia_richiesta_stessa_ricerca_e_data_da_409(client, ricerca):
    crea_richiesta(client, ricerca["id"], "2026-09-12")
    res = crea_richiesta(client, ricerca["id"], "2026-09-12", osservatore="Luigi Bianchi")
    assert res.status_code == 409


def test_ricerche_diverse_possono_chiedere_la_stessa_data(client, ricerca):
    altra = client.post("/telescope-time/ricerche", json={"nome": "Comete"}).json()
    assert crea_richiesta(client, ricerca["id"], "2026-09-12").status_code == 201
    assert crea_richiesta(client, altra["id"], "2026-09-12").status_code == 201


def test_dopo_un_rifiuto_la_data_torna_richiedibile(client, ricerca):
    prima = crea_richiesta(client, ricerca["id"], "2026-09-12").json()
    approva(client, prima["id"], stato="rifiutata")
    assert crea_richiesta(client, ricerca["id"], "2026-09-12").status_code == 201


def test_approvazione(client, ricerca):
    richiesta = crea_richiesta(client, ricerca["id"], "2026-09-12").json()
    res = approva(client, richiesta["id"], note="Meteo previsto sereno")
    assert res.status_code == 200
    body = res.json()
    assert body["stato"] == "approvata"
    assert body["note_responsabile"] == "Meteo previsto sereno"
    assert body["aggiornata_il"] is not None


def test_stato_non_valido_rifiutato(client, ricerca):
    richiesta = crea_richiesta(client, ricerca["id"], "2026-09-12").json()
    assert approva(client, richiesta["id"], stato="forse").status_code == 400


def test_patch_su_richiesta_inesistente_da_404(client):
    assert approva(client, 999).status_code == 404


def test_filtro_per_stato(client, ricerca):
    approvata = crea_richiesta(client, ricerca["id"], "2026-09-12").json()
    crea_richiesta(client, ricerca["id"], "2026-09-13")
    approva(client, approvata["id"])

    in_attesa = client.get("/telescope-time/richieste", params={"stato": "in_attesa"}).json()
    assert [r["giorno_richiesto"] for r in in_attesa] == ["2026-09-13"]
    assert len(client.get("/telescope-time/richieste").json()) == 2


def test_statistiche(client, ricerca):
    richiesta = crea_richiesta(client, ricerca["id"], "2026-09-12").json()
    crea_richiesta(client, ricerca["id"], "2026-09-13")
    approva(client, richiesta["id"])

    stats = client.get("/telescope-time/statistiche").json()
    assert {r["stato"]: r["conteggio"] for r in stats["per_stato"]} == {
        "approvata": 1,
        "in_attesa": 1,
    }
    assert stats["per_ricerca"][0] == {"nome": "Supernovae", "richieste": 2, "approvate": 1}
