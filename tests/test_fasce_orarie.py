"""L'osservatore indica da quando a quando osserva (#33).

Con la fascia oraria la sovrapposizione diventa misurabile invece che assunta:
due programmi possono condividere la notte, non lo stesso istante allo stesso
strumento.
"""

from datetime import timedelta

import pytest

from conftest import approva, crea_richiesta, fascia, notte


def invia(client, ricerca_id, inizio, fine):
    return client.post(
        "/telescope-time/richieste",
        json={"ricerca_id": ricerca_id, "inizio": inizio, "fine": fine},
    )


# ─── La fascia oraria ─────────────────────────────────────────────────────────

def test_la_richiesta_registra_inizio_e_fine(client, ricerca):
    giorno = notte()
    res = crea_richiesta(client, ricerca["id"], giorno, ora=22, durata=3)

    assert res.status_code == 201
    body = res.json()
    assert body["inizio"] == f"{giorno}T22:00:00"
    assert body["fine"] == f"{giorno + timedelta(days=1)}T01:00:00"


def test_la_notte_di_riferimento_e_quella_di_inizio(client, ricerca):
    """Una sessione che scavalca la mezzanotte appartiene alla notte in cui è
    cominciata: è la convenzione astronomica, ed è ciò su cui il calendario
    raggruppa."""
    giorno = notte()
    body = crea_richiesta(client, ricerca["id"], giorno, ora=23, durata=4).json()

    assert body["giorno_richiesto"] == giorno.isoformat()


def test_la_notte_di_una_sessione_dopo_mezzanotte_e_quella_precedente(client, ricerca):
    """Un inizio all'01:00 appartiene ancora alla notte cominciata la sera
    prima (#47): la soglia è mezzogiorno, non la mezzanotte del calendario."""
    giorno = notte()
    body = crea_richiesta(client, ricerca["id"], giorno, ora=1, durata=3).json()

    assert body["giorno_richiesto"] == (giorno - timedelta(days=1)).isoformat()


def test_la_fascia_e_esposta_nella_lettura(client, ricerca):
    creata = crea_richiesta(client, ricerca["id"], notte()).json()
    letta = client.get(f"/telescope-time/richieste/{creata['id']}").json()

    assert (letta["inizio"], letta["fine"]) == (creata["inizio"], creata["fine"])


# ─── Validazione ──────────────────────────────────────────────────────────────

def test_fine_precedente_all_inizio_rifiutata(client, ricerca):
    inizio, fine = fascia(notte())
    res = invia(client, ricerca["id"], fine, inizio)

    assert res.status_code == 422
    assert res.headers["content-type"] == "application/json"
    assert res.json()["detail"][0]["loc"] == ["body", "fine"]


def test_fascia_di_durata_nulla_rifiutata(client, ricerca):
    inizio, _ = fascia(notte())
    res = invia(client, ricerca["id"], inizio, inizio)

    assert res.status_code == 422
    assert res.json()["detail"][0]["loc"] == ["body", "fine"]


def test_inizio_nel_passato_rifiutato(client, ricerca):
    inizio, fine = fascia(notte(-1))
    res = invia(client, ricerca["id"], inizio, fine)

    assert res.status_code == 422
    assert res.json()["detail"][0]["loc"] == ["body", "inizio"]


@pytest.mark.parametrize("valore", ["domani", "2026-13-45T22:00", "", "22:00"])
def test_orario_non_valido_rifiutato(client, ricerca, valore):
    _, fine = fascia(notte())
    res = invia(client, ricerca["id"], valore, fine)

    assert res.status_code == 422
    assert res.json()["detail"][0]["loc"] == ["body", "inizio"]


def test_orario_con_fuso_rifiutato(client, ricerca):
    """Gli istanti sono ora locale dell'osservatorio: salvarne uno con offset
    renderebbe le fasce non più confrontabili fra loro."""
    giorno = notte()
    res = invia(client, ricerca["id"], f"{giorno}T22:00:00+02:00", f"{giorno}T23:00:00")

    assert res.status_code == 422
    assert res.json()["detail"][0]["loc"] == ["body", "inizio"]


def test_fascia_non_valida_non_scrive_sul_database(client, ricerca):
    inizio, fine = fascia(notte())
    invia(client, ricerca["id"], fine, inizio)

    assert client.get("/telescope-time/richieste").json() == []


# ─── Sovrapposizione: ammessa in attesa, bloccata all'approvazione ────────────

def test_due_richieste_possono_contendersi_la_stessa_fascia(client, ricerca):
    altra = client.post("/telescope-time/ricerche", json={"nome": "Comete"}).json()
    giorno = notte()

    assert crea_richiesta(client, ricerca["id"], giorno).status_code == 201
    assert crea_richiesta(client, altra["id"], giorno).status_code == 201


def test_fasce_disgiunte_nella_stessa_notte_si_approvano_entrambe(client, ricerca):
    altra = client.post("/telescope-time/ricerche", json={"nome": "Comete"}).json()
    giorno = notte()
    prima = crea_richiesta(client, ricerca["id"], giorno, ora=21, durata=2).json()
    seconda = crea_richiesta(client, altra["id"], giorno, ora=23, durata=2).json()

    assert approva(client, prima["id"]).status_code == 200
    assert approva(client, seconda["id"]).status_code == 200


def test_approvare_una_fascia_sovrapposta_da_409(client, ricerca):
    altra = client.post("/telescope-time/ricerche", json={"nome": "Comete"}).json()
    giorno = notte()
    prima = crea_richiesta(client, ricerca["id"], giorno, ora=21, durata=3).json()
    seconda = crea_richiesta(client, altra["id"], giorno, ora=23, durata=2).json()
    approva(client, prima["id"])

    res = approva(client, seconda["id"])

    assert res.status_code == 409
    assert res.headers["content-type"] == "application/json"
    dettaglio = res.json()["detail"]
    assert f"#{prima['id']}" in dettaglio, dettaglio
    assert ricerca["nome"] in dettaglio, dettaglio


def test_la_richiesta_in_conflitto_resta_in_attesa(client, ricerca):
    altra = client.post("/telescope-time/ricerche", json={"nome": "Comete"}).json()
    giorno = notte()
    prima = crea_richiesta(client, ricerca["id"], giorno, ora=21, durata=3).json()
    seconda = crea_richiesta(client, altra["id"], giorno, ora=23, durata=2).json()
    approva(client, prima["id"])
    approva(client, seconda["id"])

    dopo = client.get(f"/telescope-time/richieste/{seconda['id']}").json()
    assert dopo["stato"] == "in_attesa"
    assert dopo["aggiornata_il"] is None
    assert client.get(f"/telescope-time/richieste/{seconda['id']}/storico").json() == []


def test_rifiutare_non_e_impedito_dalla_sovrapposizione(client, ricerca):
    altra = client.post("/telescope-time/ricerche", json={"nome": "Comete"}).json()
    giorno = notte()
    prima = crea_richiesta(client, ricerca["id"], giorno, ora=21, durata=3).json()
    seconda = crea_richiesta(client, altra["id"], giorno, ora=23, durata=2).json()
    approva(client, prima["id"])

    assert approva(client, seconda["id"], stato="rifiutata").status_code == 200


def test_una_rifiutata_non_occupa_la_fascia(client, ricerca):
    altra = client.post("/telescope-time/ricerche", json={"nome": "Comete"}).json()
    giorno = notte()
    prima = crea_richiesta(client, ricerca["id"], giorno, ora=21, durata=3).json()
    seconda = crea_richiesta(client, altra["id"], giorno, ora=23, durata=2).json()
    approva(client, prima["id"], stato="rifiutata")

    assert approva(client, seconda["id"]).status_code == 200


def test_riapprovare_la_stessa_richiesta_non_e_un_conflitto(client, ricerca):
    richiesta = crea_richiesta(client, ricerca["id"], notte()).json()
    approva(client, richiesta["id"])

    assert approva(client, richiesta["id"]).status_code == 200


def test_il_conflitto_attraversa_la_soglia_di_notte(client, ricerca):
    """Le due richieste stanno in notti diverse (la soglia delle 12, #47) — è
    il confronto fra istanti a scoprire che occupano lo strumento nello
    stesso momento."""
    altra = client.post("/telescope-time/ricerche", json={"nome": "Comete"}).json()
    giorno = notte()
    prima = crea_richiesta(client, ricerca["id"], giorno, ora=11, durata=2).json()
    seconda = crea_richiesta(client, altra["id"], giorno, ora=12, durata=2).json()
    assert prima["giorno_richiesto"] != seconda["giorno_richiesto"]
    approva(client, prima["id"])

    assert approva(client, seconda["id"]).status_code == 409


def test_fasce_contigue_non_si_sovrappongono(client, ricerca):
    """La fine di una e l'inizio dell'altra coincidono: non è sovrapposizione."""
    altra = client.post("/telescope-time/ricerche", json={"nome": "Comete"}).json()
    giorno = notte()
    prima = crea_richiesta(client, ricerca["id"], giorno, ora=22, durata=2).json()
    seconda = crea_richiesta(
        client, altra["id"], giorno + timedelta(days=1), ora=0, durata=2
    ).json()
    assert prima["fine"] == seconda["inizio"]
    approva(client, prima["id"])

    assert approva(client, seconda["id"]).status_code == 200


def test_solo_i_responsabili_incontrano_il_vincolo(client_authelia, ricerca_authelia):
    """Il 409 non deve diventare una via per scoprire lo stato altrui: chi non
    può approvare riceve comunque 403."""
    from conftest import RESPONSABILE, SOCIO

    giorno = notte()
    inizio, fine = fascia(giorno, ora=21, durata=3)
    prima = client_authelia.post(
        "/telescope-time/richieste",
        json={"ricerca_id": ricerca_authelia["id"], "inizio": inizio, "fine": fine},
        headers=RESPONSABILE,
    ).json()
    client_authelia.patch(
        f"/telescope-time/richieste/{prima['id']}",
        json={"stato": "approvata"}, headers=RESPONSABILE,
    )

    altra = client_authelia.post(
        "/telescope-time/ricerche", json={"nome": "Comete"}, headers=RESPONSABILE
    ).json()
    inizio, fine = fascia(giorno, ora=23, durata=2)
    seconda = client_authelia.post(
        "/telescope-time/richieste",
        json={"ricerca_id": altra["id"], "inizio": inizio, "fine": fine},
        headers=SOCIO,
    ).json()

    res = client_authelia.patch(
        f"/telescope-time/richieste/{seconda['id']}",
        json={"stato": "approvata"}, headers=SOCIO,
    )
    assert res.status_code == 403
