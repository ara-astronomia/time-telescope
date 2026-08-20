"""I responsabili spostano data e orari di una richiesta (#34).

Senza questo, una data sbagliata non è correggibile: l'unica uscita è
rifiutare e far ricominciare l'osservatore da capo, perdendo la richiesta
originale. Ed è anche la risposta al vincolo introdotto da #33 — due richieste
che si contendono lo stesso intervallo si sbloccano spostandone una, non
rifiutandone una.
"""

from datetime import timedelta

import pytest

from conftest import RESPONSABILE, SOCIO, approva, crea_richiesta, fascia, notte

ORARIO = "/telescope-time/richieste/{}/orario"
STORICO = "/telescope-time/richieste/{}/storico"

ALTRO_SOCIO = {"Remote-User": "luigi", "Remote-Groups": "soci",
               "Remote-Email": "luigi@example.test"}


def sposta(client, richiesta_id, giorno, ora=22, durata=3, motivo=None, headers=None):
    inizio, fine = fascia(giorno, ora, durata)
    corpo = {"inizio": inizio, "fine": fine}
    if motivo is not None:
        corpo["motivo"] = motivo
    return client.patch(ORARIO.format(richiesta_id), json=corpo, headers=headers or {})


@pytest.fixture
def richiesta(client, ricerca, giorno):
    return crea_richiesta(client, ricerca["id"], giorno, ora=22, durata=3).json()


# ─── Spostare ─────────────────────────────────────────────────────────────────

def test_il_responsabile_sposta_una_richiesta_in_attesa(client, richiesta, altro_giorno):
    res = sposta(client, richiesta["id"], altro_giorno, ora=23, durata=4)

    assert res.status_code == 200
    body = res.json()
    assert body["inizio"] == f"{altro_giorno}T23:00:00"
    assert body["fine"] == f"{altro_giorno + timedelta(days=1)}T03:00:00"


def test_lo_spostamento_aggiorna_la_notte_di_riferimento(client, richiesta, altro_giorno):
    """`giorno_richiesto` è derivato: se non lo si ricalcola, il calendario
    continua a mostrare la richiesta nella notte da cui è stata tolta."""
    res = sposta(client, richiesta["id"], altro_giorno, ora=23, durata=4)

    assert res.json()["giorno_richiesto"] == altro_giorno.isoformat()


def test_lo_spostamento_dopo_mezzanotte_aggiorna_la_notte_precedente(client, richiesta, altro_giorno):
    """Eredita il calcolo di #47: uno spostamento verso l'01:00 aggiorna
    `giorno_richiesto` alla notte precedente, non a quella del nuovo giorno."""
    res = sposta(client, richiesta["id"], altro_giorno, ora=1, durata=3)

    assert res.json()["giorno_richiesto"] == (altro_giorno - timedelta(days=1)).isoformat()


def test_si_sposta_anche_una_richiesta_approvata(client, richiesta, altro_giorno):
    approva(client, richiesta["id"])

    res = sposta(client, richiesta["id"], altro_giorno)

    assert res.status_code == 200
    assert res.json()["stato"] == "approvata"


def test_si_sposta_anche_una_richiesta_rifiutata(client, richiesta, altro_giorno):
    """Spostare e poi riapprovare è l'unico ordine che funziona: riapprovare
    prima significherebbe farlo sulla fascia originale, che può nel frattempo
    essere occupata."""
    approva(client, richiesta["id"], stato="rifiutata")

    res = sposta(client, richiesta["id"], altro_giorno)

    assert res.status_code == 200
    assert res.json()["stato"] == "rifiutata"


def test_il_responsabile_puo_spostare_nel_passato(client, richiesta):
    """Serve a registrare a posteriori un'osservazione davvero fatta. Il vincolo
    non è sulla data, è sulla chiarezza: lo dichiara l'interfaccia."""
    ieri = notte(-1)

    res = sposta(client, richiesta["id"], ieri)

    assert res.status_code == 200
    assert res.json()["giorno_richiesto"] == ieri.isoformat()


def test_il_motivo_e_facoltativo(client, richiesta, altro_giorno):
    assert sposta(client, richiesta["id"], altro_giorno).status_code == 200


# ─── Validazione ──────────────────────────────────────────────────────────────

def test_fine_precedente_all_inizio_rifiutata(client, richiesta, altro_giorno):
    inizio, fine = fascia(altro_giorno)
    res = client.patch(ORARIO.format(richiesta["id"]), json={"inizio": fine, "fine": inizio})

    assert res.status_code == 422
    assert res.headers["content-type"] == "application/json"
    assert res.json()["detail"][0]["loc"] == ["body", "fine"]


def test_orario_con_fuso_rifiutato(client, richiesta, altro_giorno):
    res = client.patch(
        ORARIO.format(richiesta["id"]),
        json={"inizio": f"{altro_giorno}T22:00:00+02:00", "fine": f"{altro_giorno}T23:00:00"},
    )

    assert res.status_code == 422
    assert res.json()["detail"][0]["loc"] == ["body", "inizio"]


def test_spostare_una_richiesta_inesistente(client, altro_giorno):
    res = sposta(client, 999, altro_giorno)

    assert res.status_code == 404
    assert res.json()["detail"] == "Richiesta non trovata."


def test_uno_spostamento_invalido_non_tocca_la_richiesta(client, richiesta, altro_giorno):
    inizio, fine = fascia(altro_giorno)
    client.patch(ORARIO.format(richiesta["id"]), json={"inizio": fine, "fine": inizio})

    dopo = client.get(f"/telescope-time/richieste/{richiesta['id']}").json()
    assert dopo["inizio"] == richiesta["inizio"]
    assert client.get(STORICO.format(richiesta["id"])).json() == []


# ─── Autorizzazione (#36: anche il proprietario, con vincoli propri) ──────────

def crea_richiesta_di(client_authelia, ricerca_id, giorno, headers):
    inizio, fine = fascia(giorno)
    return client_authelia.post(
        "/telescope-time/richieste",
        json={"ricerca_id": ricerca_id, "inizio": inizio, "fine": fine},
        headers=headers,
    ).json()


def test_il_proprietario_sposta_la_propria_richiesta_in_attesa(
    client_authelia, ricerca_authelia, giorno, altro_giorno
):
    propria = crea_richiesta_di(client_authelia, ricerca_authelia["id"], giorno, SOCIO)

    res = sposta(client_authelia, propria["id"], altro_giorno, headers=SOCIO)

    assert res.status_code == 200
    assert res.json()["inizio"] == f"{altro_giorno}T22:00:00"


def test_un_altro_socio_non_puo_spostare_una_richiesta_non_sua(
    client_authelia, ricerca_authelia, giorno, altro_giorno
):
    propria = crea_richiesta_di(client_authelia, ricerca_authelia["id"], giorno, SOCIO)

    res = sposta(client_authelia, propria["id"], altro_giorno, headers=ALTRO_SOCIO)

    assert res.status_code == 403
    dopo = client_authelia.get(
        f"/telescope-time/richieste/{propria['id']}", headers=SOCIO
    ).json()
    assert dopo["inizio"] == propria["inizio"]


def test_il_proprietario_non_puo_spostare_una_approvata(
    client_authelia, ricerca_authelia, giorno, altro_giorno
):
    propria = crea_richiesta_di(client_authelia, ricerca_authelia["id"], giorno, SOCIO)
    client_authelia.patch(
        f"/telescope-time/richieste/{propria['id']}",
        json={"stato": "approvata"}, headers=RESPONSABILE,
    )

    res = sposta(client_authelia, propria["id"], altro_giorno, headers=SOCIO)

    assert res.status_code == 409


def test_il_proprietario_non_puo_spostare_una_rifiutata(
    client_authelia, ricerca_authelia, giorno, altro_giorno
):
    propria = crea_richiesta_di(client_authelia, ricerca_authelia["id"], giorno, SOCIO)
    client_authelia.patch(
        f"/telescope-time/richieste/{propria['id']}",
        json={"stato": "rifiutata"}, headers=RESPONSABILE,
    )

    res = sposta(client_authelia, propria["id"], altro_giorno, headers=SOCIO)

    assert res.status_code == 409


def test_il_proprietario_non_puo_spostare_nel_passato(
    client_authelia, ricerca_authelia, giorno
):
    propria = crea_richiesta_di(client_authelia, ricerca_authelia["id"], giorno, SOCIO)

    res = sposta(client_authelia, propria["id"], notte(-5), headers=SOCIO)

    assert res.status_code == 422


# ─── Sovrapposizione ──────────────────────────────────────────────────────────

def test_spostare_una_approvata_su_una_fascia_occupata_da_409(client, ricerca, giorno):
    altra = client.post("/telescope-time/ricerche", json={"nome": "Comete"}).json()
    occupata = crea_richiesta(client, ricerca["id"], giorno, ora=21, durata=3).json()
    mobile = crea_richiesta(client, altra["id"], giorno + timedelta(days=2), ora=21).json()
    approva(client, occupata["id"])
    approva(client, mobile["id"])

    res = sposta(client, mobile["id"], giorno, ora=23, durata=2)

    assert res.status_code == 409
    dettaglio = res.json()["detail"]
    assert f"#{occupata['id']}" in dettaglio, dettaglio


def test_spostare_una_in_attesa_su_una_fascia_occupata_e_permesso(client, ricerca, giorno):
    """La contesa è ammessa finché nessuno ha approvato: il vincolo scatta
    all'approvazione, non prima."""
    altra = client.post("/telescope-time/ricerche", json={"nome": "Comete"}).json()
    occupata = crea_richiesta(client, ricerca["id"], giorno, ora=21, durata=3).json()
    mobile = crea_richiesta(client, altra["id"], giorno + timedelta(days=2), ora=21).json()
    approva(client, occupata["id"])

    assert sposta(client, mobile["id"], giorno, ora=23, durata=2).status_code == 200


def test_una_richiesta_non_confligge_con_se_stessa(client, richiesta, giorno):
    approva(client, richiesta["id"])

    assert sposta(client, richiesta["id"], giorno, ora=22, durata=4).status_code == 200


def test_lo_spostamento_in_conflitto_non_lascia_traccia(client, ricerca, giorno):
    altra = client.post("/telescope-time/ricerche", json={"nome": "Comete"}).json()
    occupata = crea_richiesta(client, ricerca["id"], giorno, ora=21, durata=3).json()
    mobile = crea_richiesta(client, altra["id"], giorno + timedelta(days=2), ora=21).json()
    approva(client, occupata["id"])
    approva(client, mobile["id"])
    prima = client.get(f"/telescope-time/richieste/{mobile['id']}").json()

    sposta(client, mobile["id"], giorno, ora=23, durata=2)

    dopo = client.get(f"/telescope-time/richieste/{mobile['id']}").json()
    assert dopo["inizio"] == prima["inizio"]
    assert len(client.get(STORICO.format(mobile["id"])).json()) == 1   # solo l'approvazione


def test_lo_spostamento_sblocca_una_doppia_approvazione(client, ricerca, giorno):
    """Lo scenario che motiva la storia: due richieste si contendono lo stesso
    intervallo, spostarne una le rende approvabili entrambe."""
    altra = client.post("/telescope-time/ricerche", json={"nome": "Comete"}).json()
    prima = crea_richiesta(client, ricerca["id"], giorno, ora=21, durata=3).json()
    seconda = crea_richiesta(client, altra["id"], giorno, ora=22, durata=3).json()
    approva(client, prima["id"])
    assert approva(client, seconda["id"]).status_code == 409

    sposta(client, seconda["id"], giorno, ora=0, durata=3)

    assert approva(client, seconda["id"]).status_code == 200


# ─── Storico ──────────────────────────────────────────────────────────────────

def test_lo_spostamento_finisce_nello_storico(client_authelia, ricerca_authelia, giorno, altro_giorno):
    inizio, fine = fascia(giorno)
    creata = client_authelia.post(
        "/telescope-time/richieste",
        json={"ricerca_id": ricerca_authelia["id"], "inizio": inizio, "fine": fine},
        headers=RESPONSABILE,
    ).json()

    sposta(client_authelia, creata["id"], altro_giorno, ora=23, durata=4,
           motivo="Manutenzione", headers=RESPONSABILE)

    voce = client_authelia.get(STORICO.format(creata["id"]), headers=RESPONSABILE).json()[0]
    assert voce["tipo"] == "spostamento"
    assert voce["inizio_precedente"] == creata["inizio"]
    assert voce["fine_precedente"] == creata["fine"]
    assert voce["inizio_nuovo"] == f"{altro_giorno}T23:00:00"
    assert voce["note"] == "Manutenzione"
    assert voce["deciso_da"] == "anna"
    assert voce["deciso_il"].endswith("Z")


def test_le_decisioni_restano_distinguibili_dagli_spostamenti(client, richiesta, altro_giorno):
    approva(client, richiesta["id"], note="Meteo stabile")
    sposta(client, richiesta["id"], altro_giorno, motivo="Manutenzione")

    voci = client.get(STORICO.format(richiesta["id"])).json()
    assert [v["tipo"] for v in voci] == ["decisione", "spostamento"]
    assert voci[0]["stato_nuovo"] == "approvata"
    assert voci[0]["inizio_nuovo"] is None
    assert voci[1]["stato_nuovo"] is None


def test_uno_spostamento_a_orari_invariati_non_si_registra(client, richiesta, giorno):
    """Doppio click sul pulsante: come per le decisioni, non è un evento."""
    sposta(client, richiesta["id"], giorno, ora=22, durata=3)

    assert client.get(STORICO.format(richiesta["id"])).json() == []


# ─── Email ────────────────────────────────────────────────────────────────────

@pytest.fixture
def email(monkeypatch):
    import router
    inviate = []
    monkeypatch.setattr(
        router, "invia_messaggio",
        lambda destinatario, oggetto, corpo: inviate.append((destinatario, oggetto, corpo)),
    )
    return inviate


def test_l_osservatore_e_avvisato_dello_spostamento(client, richiesta, email, altro_giorno):
    """Si è visto assegnare un orario diverso da quello chiesto: non è
    un'informazione che possa scoprire per caso aprendo il calendario."""
    sposta(client, richiesta["id"], altro_giorno, ora=23, durata=4, motivo="Manutenzione")

    assert len(email) == 1
    destinatario, oggetto, corpo = email[0]
    assert destinatario == "sviluppo@example.test"
    assert "Manutenzione" in corpo
    assert "23:00" in corpo, corpo


def test_l_avviso_riporta_la_fascia_precedente(client, richiesta, email, altro_giorno):
    sposta(client, richiesta["id"], altro_giorno, ora=23, durata=4)

    corpo = email[0][2]
    assert f"{richiesta['inizio'][8:10]}/" in corpo, corpo


def test_uno_spostamento_nel_passato_e_dichiarato_nell_avviso(client, richiesta, email):
    sposta(client, richiesta["id"], notte(-1))

    corpo = email[0][2].lower()
    assert "passat" in corpo, corpo


def test_nessuna_email_se_lo_spostamento_fallisce(client, ricerca, giorno, email):
    altra = client.post("/telescope-time/ricerche", json={"nome": "Comete"}).json()
    occupata = crea_richiesta(client, ricerca["id"], giorno, ora=21, durata=3).json()
    mobile = crea_richiesta(client, altra["id"], giorno + timedelta(days=2), ora=21).json()
    approva(client, occupata["id"])
    approva(client, mobile["id"])
    email.clear()

    sposta(client, mobile["id"], giorno, ora=23, durata=2)

    assert email == []
