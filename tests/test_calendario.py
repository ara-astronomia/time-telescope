"""Il calendario è l'unica logica non banale del servizio: deriva lo stato di
ogni giorno dagli stati delle richieste che lo occupano."""

from conftest import approva, crea_richiesta


def giorni(client, anno=2026, mese=9):
    res = client.get("/telescope-time/calendario", params={"anno": anno, "mese": mese})
    assert res.status_code == 200
    return res.json()["giorni"]


def test_giorno_senza_richieste_non_compare(client, ricerca):
    assert giorni(client) == {}


def test_solo_in_attesa_e_contesa(client, ricerca):
    crea_richiesta(client, ricerca["id"], "2026-09-12")
    assert giorni(client)["2026-09-12"]["stato_giorno"] == "contesa"


def test_una_approvata_blocca_il_giorno(client, ricerca):
    richiesta = crea_richiesta(client, ricerca["id"], "2026-09-12").json()
    approva(client, richiesta["id"])
    assert giorni(client)["2026-09-12"]["stato_giorno"] == "bloccata"


def test_approvata_prevale_sulle_in_attesa(client, ricerca):
    altra = client.post("/telescope-time/ricerche", json={"nome": "Comete"}).json()
    approvata = crea_richiesta(client, ricerca["id"], "2026-09-12").json()
    crea_richiesta(client, altra["id"], "2026-09-12")
    approva(client, approvata["id"])

    giorno = giorni(client)["2026-09-12"]
    assert giorno["stato_giorno"] == "bloccata"
    assert len(giorno["richieste"]) == 2


def test_le_rifiutate_sono_escluse(client, ricerca):
    richiesta = crea_richiesta(client, ricerca["id"], "2026-09-12").json()
    approva(client, richiesta["id"], stato="rifiutata")
    assert "2026-09-12" not in giorni(client)


def test_solo_il_mese_richiesto(client, ricerca):
    crea_richiesta(client, ricerca["id"], "2026-09-30")
    crea_richiesta(client, ricerca["id"], "2026-10-01")
    assert list(giorni(client, mese=9)) == ["2026-09-30"]
    assert list(giorni(client, mese=10)) == ["2026-10-01"]


def test_estremi_del_mese_inclusi(client, ricerca):
    crea_richiesta(client, ricerca["id"], "2026-09-01")
    crea_richiesta(client, ricerca["id"], "2026-09-30")
    assert set(giorni(client, mese=9)) == {"2026-09-01", "2026-09-30"}


def test_ultimo_giorno_di_febbraio_bisestile(client, ricerca):
    """Il range del mese è calcolato con monthrange: il 29 non va perso."""
    crea_richiesta(client, ricerca["id"], "2028-02-29")
    assert "2028-02-29" in giorni(client, anno=2028, mese=2)


def test_dettaglio_richiesta_nel_giorno(client, ricerca):
    """L'osservatore non è più un nome digitato: è l'identità autenticata."""
    crea_richiesta(client, ricerca["id"], "2026-09-12")
    richiesta = giorni(client)["2026-09-12"]["richieste"][0]
    assert richiesta["osservatore"] == "sviluppo"   # DEV_USER
    assert richiesta["nome_ricerca"] == "Supernovae"
    assert richiesta["stato"] == "in_attesa"


def test_senza_parametri_usa_il_mese_corrente(client):
    res = client.get("/telescope-time/calendario")
    assert res.status_code == 200
    from datetime import datetime

    oggi = datetime.now()
    assert (res.json()["anno"], res.json()["mese"]) == (oggi.year, oggi.month)
