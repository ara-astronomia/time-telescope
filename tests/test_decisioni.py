"""Storico delle decisioni (#9) e compresenza di più osservazioni (#4)."""

import pytest

from conftest import approva, crea_richiesta

STORICO = "/telescope-time/richieste/{}/storico"


# ─── #9 — storico e idempotenza ───────────────────────────────────────────────

def test_la_prima_decisione_finisce_nello_storico(client, ricerca):
    richiesta = crea_richiesta(client, ricerca["id"], "2026-09-12").json()
    approva(client, richiesta["id"], note="Meteo stabile")

    voci = client.get(STORICO.format(richiesta["id"])).json()
    assert len(voci) == 1
    assert voci[0]["stato_precedente"] == "in_attesa"
    assert voci[0]["stato_nuovo"] == "approvata"
    assert voci[0]["note"] == "Meteo stabile"
    assert voci[0]["deciso_il"] is not None


def test_il_ribaltamento_e_permesso_e_tracciato(client, ricerca):
    richiesta = crea_richiesta(client, ricerca["id"], "2026-09-12").json()
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
           json={"ricerca_id": 1, "osservatore": "Mario", "giorno_richiesto": "2026-09-12"},
           headers=RESPONSABILE)
    c.patch("/telescope-time/richieste/1", json={"stato": "approvata"}, headers=RESPONSABILE)

    voci = c.get(STORICO.format(1), headers=RESPONSABILE).json()
    assert voci[0]["deciso_da"] == "anna"


def test_le_note_non_vengono_azzerate_se_non_passate(client, ricerca):
    richiesta = crea_richiesta(client, ricerca["id"], "2026-09-12").json()
    approva(client, richiesta["id"], note="Meteo stabile")

    res = client.patch(f"/telescope-time/richieste/{richiesta['id']}", json={"stato": "rifiutata"})
    assert res.json()["note_responsabile"] == "Meteo stabile"


def test_stato_invariato_non_duplica_lo_storico(client, ricerca):
    richiesta = crea_richiesta(client, ricerca["id"], "2026-09-12").json()
    approva(client, richiesta["id"])
    approva(client, richiesta["id"])          # doppio click sul pulsante

    assert len(client.get(STORICO.format(richiesta["id"])).json()) == 1


def test_storico_di_richiesta_inesistente(client):
    assert client.get(STORICO.format(999)).status_code == 404


def test_storico_vuoto_per_richiesta_mai_decisa(client, ricerca):
    richiesta = crea_richiesta(client, ricerca["id"], "2026-09-12").json()
    assert client.get(STORICO.format(richiesta["id"])).json() == []


# ─── #4 — più osservazioni nella stessa notte ─────────────────────────────────

def test_due_approvazioni_nella_stessa_notte_sono_permesse(client, ricerca):
    altra = client.post("/telescope-time/ricerche", json={"nome": "Comete"}).json()
    prima = crea_richiesta(client, ricerca["id"], "2026-09-12").json()
    seconda = crea_richiesta(client, altra["id"], "2026-09-12").json()

    assert approva(client, prima["id"]).status_code == 200
    assert approva(client, seconda["id"]).status_code == 200


def test_il_calendario_conta_le_approvate(client, ricerca):
    altra = client.post("/telescope-time/ricerche", json={"nome": "Comete"}).json()
    prima = crea_richiesta(client, ricerca["id"], "2026-09-12").json()
    seconda = crea_richiesta(client, altra["id"], "2026-09-12").json()
    approva(client, prima["id"])
    approva(client, seconda["id"])

    giorno = client.get("/telescope-time/calendario", params={"anno": 2026, "mese": 9}).json()["giorni"]["2026-09-12"]
    assert giorno["approvate"] == 2
    assert giorno["stato_giorno"] == "bloccata"


def test_giorno_con_una_sola_approvata(client, ricerca):
    richiesta = crea_richiesta(client, ricerca["id"], "2026-09-12").json()
    approva(client, richiesta["id"])
    giorno = client.get("/telescope-time/calendario", params={"anno": 2026, "mese": 9}).json()["giorni"]["2026-09-12"]
    assert giorno["approvate"] == 1


def test_giorno_conteso_non_ha_approvate(client, ricerca):
    crea_richiesta(client, ricerca["id"], "2026-09-12")
    giorno = client.get("/telescope-time/calendario", params={"anno": 2026, "mese": 9}).json()["giorni"]["2026-09-12"]
    assert giorno["approvate"] == 0
    assert giorno["stato_giorno"] == "contesa"
