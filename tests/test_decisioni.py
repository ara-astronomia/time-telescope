"""Storico delle decisioni (#9) e compresenza di più osservazioni (#4)."""

import pytest

from conftest import approva, corpo_richiesta, crea_richiesta

STORICO = "/telescope-time/richieste/{}/storico"


def calendario_del(client, giorno):
    res = client.get(
        "/telescope-time/calendario",
        params={"anno": giorno.year, "mese": giorno.month},
    )
    return res.json()["giorni"][giorno.isoformat()]


# ─── #9 — storico e idempotenza ───────────────────────────────────────────────

def test_la_prima_decisione_finisce_nello_storico(client, ricerca, giorno):
    richiesta = crea_richiesta(client, ricerca["id"], giorno).json()
    approva(client, richiesta["id"], note="Meteo stabile")

    voci = client.get(STORICO.format(richiesta["id"])).json()
    assert len(voci) == 1
    assert voci[0]["stato_precedente"] == "in_attesa"
    assert voci[0]["stato_nuovo"] == "approvata"
    assert voci[0]["note"] == "Meteo stabile"
    assert voci[0]["deciso_il"] is not None


def test_il_ribaltamento_e_permesso_e_tracciato(client, ricerca, giorno):
    richiesta = crea_richiesta(client, ricerca["id"], giorno).json()
    approva(client, richiesta["id"], note="Meteo stabile")
    res = approva(client, richiesta["id"], stato="rifiutata", note="Previsioni peggiorate")

    assert res.status_code == 200
    assert res.json()["stato"] == "rifiutata"
    voci = client.get(STORICO.format(richiesta["id"])).json()
    assert [(v["stato_precedente"], v["stato_nuovo"]) for v in voci] == [
        ("in_attesa", "approvata"),
        ("approvata", "rifiutata"),
    ]


def test_lo_storico_registra_chi_ha_deciso(client_authelia, ricerca_authelia):
    from conftest import RESPONSABILE
    c = client_authelia
    c.post("/telescope-time/richieste",
           json=corpo_richiesta(osservatore="Mario"),
           headers=RESPONSABILE)
    c.patch("/telescope-time/richieste/1", json={"stato": "approvata"}, headers=RESPONSABILE)

    voci = c.get(STORICO.format(1), headers=RESPONSABILE).json()
    assert voci[0]["deciso_da"] == "anna"


def test_le_note_non_vengono_azzerate_se_non_passate(client, ricerca, giorno):
    richiesta = crea_richiesta(client, ricerca["id"], giorno).json()
    approva(client, richiesta["id"], note="Meteo stabile")

    res = client.patch(f"/telescope-time/richieste/{richiesta['id']}", json={"stato": "rifiutata"})
    assert res.json()["note_responsabile"] == "Meteo stabile"


def test_stato_invariato_non_duplica_lo_storico(client, ricerca, giorno):
    richiesta = crea_richiesta(client, ricerca["id"], giorno).json()
    approva(client, richiesta["id"])
    approva(client, richiesta["id"])          # doppio click sul pulsante

    assert len(client.get(STORICO.format(richiesta["id"])).json()) == 1


def test_storico_di_richiesta_inesistente(client):
    assert client.get(STORICO.format(999)).status_code == 404


def test_storico_vuoto_per_richiesta_mai_decisa(client, ricerca, giorno):
    richiesta = crea_richiesta(client, ricerca["id"], giorno).json()
    assert client.get(STORICO.format(richiesta["id"])).json() == []


# ─── #4 — più osservazioni nella stessa notte ─────────────────────────────────

def test_due_approvazioni_nella_stessa_notte_sono_permesse(client, ricerca, giorno):
    altra = client.post("/telescope-time/ricerche", json={"nome": "Comete"}).json()
    prima = crea_richiesta(client, ricerca["id"], giorno, ora=21, durata=2).json()
    seconda = crea_richiesta(client, altra["id"], giorno, ora=23, durata=2).json()

    assert approva(client, prima["id"]).status_code == 200
    assert approva(client, seconda["id"]).status_code == 200


def test_il_calendario_conta_le_approvate(client, ricerca, giorno):
    altra = client.post("/telescope-time/ricerche", json={"nome": "Comete"}).json()
    prima = crea_richiesta(client, ricerca["id"], giorno, ora=21, durata=2).json()
    seconda = crea_richiesta(client, altra["id"], giorno, ora=23, durata=2).json()
    approva(client, prima["id"])
    approva(client, seconda["id"])

    notte_bloccata = calendario_del(client, giorno)
    assert notte_bloccata["approvate"] == 2
    assert notte_bloccata["stato_giorno"] == "bloccata"


def test_giorno_con_una_sola_approvata(client, ricerca, giorno):
    richiesta = crea_richiesta(client, ricerca["id"], giorno).json()
    approva(client, richiesta["id"])

    assert calendario_del(client, giorno)["approvate"] == 1


def test_notte_solo_richiesta_non_ha_approvate(client, ricerca, giorno):
    crea_richiesta(client, ricerca["id"], giorno)

    notte_richiesta = calendario_del(client, giorno)
    assert notte_richiesta["approvate"] == 0
    assert notte_richiesta["stato_giorno"] == "richiesta"
