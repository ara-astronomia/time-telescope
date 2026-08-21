"""Il calendario è l'unica logica non banale del servizio: deriva lo stato di
ogni notte dalle fasce orarie delle richieste che la occupano.

| Situazione della notte                      | stato_giorno |
|---------------------------------------------|--------------|
| nessuna richiesta                           | (assente)    |
| richieste in attesa che non si sovrappongono| richiesta    |
| due o più in attesa con fasce sovrapposte   | contesa      |
| almeno una approvata                        | bloccata     |
"""

from calendar import isleap, monthrange
from datetime import date, timedelta

import pytest

from conftest import approva, crea_richiesta


@pytest.fixture
def mese():
    """Il mese prossimo: interamente nel futuro, come le richieste che accetta."""
    oggi = date.today()
    return date(oggi.year + (oggi.month == 12), oggi.month % 12 + 1, 1)


@pytest.fixture
def altra_ricerca(client):
    return client.post("/telescope-time/ricerche", json={"nome": "Comete"}).json()


def giorni(client, quando):
    res = client.get(
        "/telescope-time/calendario", params={"anno": quando.year, "mese": quando.month}
    )
    assert res.status_code == 200
    return res.json()["giorni"]


def notte_di(client, quando):
    return giorni(client, quando)[quando.isoformat()]


# ─── Stato della notte ────────────────────────────────────────────────────────

def test_notte_senza_richieste_non_compare(client, ricerca, mese):
    assert giorni(client, mese) == {}


def test_una_sola_in_attesa_non_e_una_contesa(client, ricerca, mese):
    """Chi inserisce l'unica richiesta della notte non deve leggere 'contesa'
    e mettersi a cercare le altre (#42)."""
    quando = mese.replace(day=12)
    crea_richiesta(client, ricerca["id"], quando)

    assert notte_di(client, quando)["stato_giorno"] == "richiesta"


def test_due_in_attesa_disgiunte_non_sono_una_contesa(client, ricerca, altra_ricerca, mese):
    quando = mese.replace(day=12)
    crea_richiesta(client, ricerca["id"], quando, ora=21, durata=2)
    crea_richiesta(client, altra_ricerca["id"], quando, ora=23, durata=2)

    notte = notte_di(client, quando)
    assert notte["stato_giorno"] == "richiesta"
    assert notte["sovrapposizioni"] == []


def test_due_in_attesa_sovrapposte_sono_una_contesa(client, ricerca, altra_ricerca, mese):
    quando = mese.replace(day=12)
    crea_richiesta(client, ricerca["id"], quando, ora=21, durata=3)
    crea_richiesta(client, altra_ricerca["id"], quando, ora=23, durata=2)

    assert notte_di(client, quando)["stato_giorno"] == "contesa"


def test_una_approvata_blocca_la_notte(client, ricerca, mese):
    quando = mese.replace(day=12)
    richiesta = crea_richiesta(client, ricerca["id"], quando).json()
    approva(client, richiesta["id"])

    assert notte_di(client, quando)["stato_giorno"] == "bloccata"


def test_approvata_prevale_sulle_in_attesa(client, ricerca, altra_ricerca, mese):
    quando = mese.replace(day=12)
    approvata = crea_richiesta(client, ricerca["id"], quando, ora=21, durata=3).json()
    crea_richiesta(client, altra_ricerca["id"], quando, ora=23, durata=2)
    approva(client, approvata["id"])

    notte = notte_di(client, quando)
    assert notte["stato_giorno"] == "bloccata"
    assert len(notte["richieste"]) == 2


def test_le_rifiutate_sono_escluse(client, ricerca, mese):
    quando = mese.replace(day=12)
    richiesta = crea_richiesta(client, ricerca["id"], quando).json()
    approva(client, richiesta["id"], stato="rifiutata")

    assert quando.isoformat() not in giorni(client, mese)


# ─── Conteggi e conflitti ─────────────────────────────────────────────────────

def test_la_notte_espone_i_conteggi(client, ricerca, altra_ricerca, mese):
    quando = mese.replace(day=12)
    approvata = crea_richiesta(client, ricerca["id"], quando, ora=21, durata=2).json()
    crea_richiesta(client, altra_ricerca["id"], quando, ora=23, durata=2)
    approva(client, approvata["id"])

    notte = notte_di(client, quando)
    assert (notte["approvate"], notte["in_attesa"]) == (1, 1)


def test_le_coppie_sovrapposte_sono_esposte(client, ricerca, altra_ricerca, mese):
    """Al responsabile non basta sapere che c'è un conflitto: deve sapere fra
    quali richieste, per decidere cosa spostare."""
    quando = mese.replace(day=12)
    prima = crea_richiesta(client, ricerca["id"], quando, ora=21, durata=3).json()
    seconda = crea_richiesta(client, altra_ricerca["id"], quando, ora=23, durata=2).json()

    assert notte_di(client, quando)["sovrapposizioni"] == [[prima["id"], seconda["id"]]]


def test_una_fascia_a_cavallo_della_soglia_e_rifiutata(client, ricerca, mese):
    """Due notti sono finestre da 12:00 a 12:00 consecutive e disgiunte: una
    fascia dentro la propria notte non può mai toccare quella successiva."""
    quando = mese.replace(day=12)
    res = crea_richiesta(client, ricerca["id"], quando, ora=11, durata=2)

    assert res.status_code == 422


# ─── Estremi del mese ─────────────────────────────────────────────────────────

def test_solo_il_mese_richiesto(client, ricerca, mese):
    ultimo = mese.replace(day=monthrange(mese.year, mese.month)[1])
    primo_del_prossimo = ultimo + timedelta(days=1)
    crea_richiesta(client, ricerca["id"], ultimo)
    crea_richiesta(client, ricerca["id"], primo_del_prossimo)

    assert list(giorni(client, mese)) == [ultimo.isoformat()]
    assert list(giorni(client, primo_del_prossimo)) == [primo_del_prossimo.isoformat()]


def test_estremi_del_mese_inclusi(client, ricerca, mese):
    ultimo = mese.replace(day=monthrange(mese.year, mese.month)[1])
    crea_richiesta(client, ricerca["id"], mese)
    crea_richiesta(client, ricerca["id"], ultimo)

    assert set(giorni(client, mese)) == {mese.isoformat(), ultimo.isoformat()}


def test_ultimo_giorno_di_febbraio_bisestile(client, ricerca):
    """Il range del mese è calcolato con monthrange: il 29 non va perso."""
    anno = date.today().year + 1
    while not isleap(anno):
        anno += 1
    bisestile = date(anno, 2, 29)
    crea_richiesta(client, ricerca["id"], bisestile)

    assert bisestile.isoformat() in giorni(client, bisestile)


# ─── Contenuto del giorno ─────────────────────────────────────────────────────

def test_dettaglio_richiesta_nella_notte(client, ricerca, mese):
    """L'osservatore non è un nome digitato: è l'identità autenticata."""
    quando = mese.replace(day=12)
    crea_richiesta(client, ricerca["id"], quando, ora=22, durata=3)

    richiesta = notte_di(client, quando)["richieste"][0]
    assert richiesta["osservatore"] == "Marta Conti"   # nome per esteso del DEV_USER di default
    assert richiesta["nome_ricerca"] == "Supernovae"
    assert richiesta["stato"] == "in_attesa"
    assert richiesta["inizio"] == f"{quando}T22:00:00"
    assert richiesta["fine"] == f"{quando + timedelta(days=1)}T01:00:00"


def test_senza_parametri_usa_il_mese_corrente(client):
    res = client.get("/telescope-time/calendario")
    assert res.status_code == 200

    oggi = date.today()
    assert (res.json()["anno"], res.json()["mese"]) == (oggi.year, oggi.month)
