"""I timestamp sono scritti in UTC: devono dirlo (#7).

`datetime('now')` di SQLite produce '2026-08-17 06:30:00', che non è ISO 8601
valido — spazio invece di T, nessun fuso — e i browser lo interpretano come ora
locale, sfalsando di un'ora in inverno e due in estate.
"""

from datetime import datetime, timedelta, timezone

from conftest import approva, crea_richiesta


def parsa(valore):
    """Fallisce se il timestamp non è ISO 8601 con fuso esplicito."""
    momento = datetime.fromisoformat(valore)
    assert momento.tzinfo is not None, f"timestamp senza fuso: {valore!r}"
    return momento


def vicino_a_adesso(valore, tolleranza=timedelta(minutes=2)):
    return abs(parsa(valore) - datetime.now(timezone.utc)) < tolleranza


def test_creata_il_di_una_ricerca(client):
    ricerca = client.post("/telescope-time/ricerche", json={"nome": "Supernovae"}).json()
    assert ricerca["creata_il"].endswith("Z")
    assert vicino_a_adesso(ricerca["creata_il"])


def test_creata_il_di_una_richiesta(client, ricerca):
    richiesta = crea_richiesta(client, ricerca["id"], "2026-09-12").json()
    assert richiesta["creata_il"].endswith("Z")
    assert vicino_a_adesso(richiesta["creata_il"])


def test_aggiornata_il_dopo_una_decisione(client, ricerca):
    richiesta = crea_richiesta(client, ricerca["id"], "2026-09-12").json()
    aggiornata = approva(client, richiesta["id"]).json()["aggiornata_il"]
    assert aggiornata.endswith("Z")
    assert vicino_a_adesso(aggiornata)


def test_deciso_il_nello_storico(client, ricerca):
    richiesta = crea_richiesta(client, ricerca["id"], "2026-09-12").json()
    approva(client, richiesta["id"])
    voce = client.get(f"/telescope-time/richieste/{richiesta['id']}/storico").json()[0]
    assert voce["deciso_il"].endswith("Z")
    assert vicino_a_adesso(voce["deciso_il"])


def test_creata_il_nel_calendario(client, ricerca):
    crea_richiesta(client, ricerca["id"], "2026-09-12")
    giorno = client.get(
        "/telescope-time/calendario", params={"anno": 2026, "mese": 9}
    ).json()["giorni"]["2026-09-12"]
    assert giorno["richieste"][0]["creata_il"].endswith("Z")


def test_ordinamento_per_data_di_creazione_resta_coerente(client, ricerca):
    """Il formato cambia: l'ordinamento lessicografico usato dalle query deve
    continuare a coincidere con quello cronologico."""
    prima = crea_richiesta(client, ricerca["id"], "2026-09-12").json()
    seconda = crea_richiesta(client, ricerca["id"], "2026-09-13").json()
    assert prima["creata_il"] <= seconda["creata_il"]
    assert parsa(prima["creata_il"]) <= parsa(seconda["creata_il"])
