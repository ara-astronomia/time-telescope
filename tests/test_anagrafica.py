"""Anagrafica utenti: l'identità arriva da Authelia, non dal modulo (#5).

`username` non nullo significa identità verificata: solo chi ce l'ha può
aprire una richiesta. Il nome e l'email non si digitano più.
"""

import sqlite3

from conftest import RESPONSABILE, SOCIO


def utenti(client):
    """Legge l'anagrafica direttamente dal database usato dal test."""
    import os
    conn = sqlite3.connect(os.environ["TELESCOPE_DB_PATH"])
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM utenti ORDER BY id")]
    finally:
        conn.close()


def crea_richiesta_come(client, headers, giorno="2026-09-12", ricerca_id=1):
    return client.post(
        "/telescope-time/richieste",
        json={"ricerca_id": ricerca_id, "giorno_richiesto": giorno},
        headers=headers,
    )


# ─── L'anagrafica si popola da sola ───────────────────────────────────────────

def test_il_primo_accesso_registra_l_utente(client_authelia):
    client_authelia.get("/telescope-time/me", headers=RESPONSABILE)
    registrati = utenti(client_authelia)
    assert [u["username"] for u in registrati] == ["anna"]
    assert registrati[0]["email"] == "anna@example.test"
    assert registrati[0]["nome"]


def test_accessi_ripetuti_non_duplicano(client_authelia):
    for _ in range(3):
        client_authelia.get("/telescope-time/me", headers=RESPONSABILE)
    assert len(utenti(client_authelia)) == 1


def test_utenti_diversi_record_diversi(client_authelia):
    client_authelia.get("/telescope-time/me", headers=RESPONSABILE)
    client_authelia.get("/telescope-time/me", headers=SOCIO)
    assert sorted(u["username"] for u in utenti(client_authelia)) == ["anna", "mario"]


def test_email_aggiornata_se_cambia_in_authelia(client_authelia):
    client_authelia.get("/telescope-time/me", headers=RESPONSABILE)
    nuovi = {**RESPONSABILE, "Remote-Email": "anna.nuova@example.test"}
    client_authelia.get("/telescope-time/me", headers=nuovi)
    registrati = utenti(client_authelia)
    assert len(registrati) == 1
    assert registrati[0]["email"] == "anna.nuova@example.test"


def test_email_unica_in_anagrafica(client_authelia):
    import os
    client_authelia.get("/telescope-time/me", headers=RESPONSABILE)
    conn = sqlite3.connect(os.environ["TELESCOPE_DB_PATH"])
    try:
        try:
            conn.execute(
                "INSERT INTO utenti (username, nome, email) VALUES (?,?,?)",
                ("altro", "Altro Nome", "anna@example.test"),
            )
            conn.commit()
            assert False, "due utenti con la stessa email: manca il vincolo UNIQUE"
        except sqlite3.IntegrityError:
            pass
    finally:
        conn.close()


def test_piu_utenti_senza_email_sono_ammessi(client_authelia):
    """Serve ai co-osservatori occasionali di cui non si ha il recapito (#40)."""
    import os
    conn = sqlite3.connect(os.environ["TELESCOPE_DB_PATH"])
    try:
        conn.execute("INSERT INTO utenti (nome) VALUES ('Ospite Uno')")
        conn.execute("INSERT INTO utenti (nome) VALUES ('Ospite Due')")
        conn.commit()
        senza = conn.execute("SELECT COUNT(*) FROM utenti WHERE email IS NULL").fetchone()[0]
        assert senza == 2
    finally:
        conn.close()


# ─── La richiesta non chiede più chi sei ──────────────────────────────────────

def test_la_richiesta_usa_l_utente_autenticato(client_authelia, ricerca_authelia):
    res = crea_richiesta_come(client_authelia, SOCIO)
    assert res.status_code == 201
    assert res.json()["osservatore"] == "mario"


def test_osservatore_nel_body_viene_ignorato(client_authelia, ricerca_authelia):
    """Anche se il campo viene inviato, l'identità resta quella verificata."""
    res = client_authelia.post(
        "/telescope-time/richieste",
        json={"ricerca_id": 1, "giorno_richiesto": "2026-09-12",
              "osservatore": "Qualcun Altro"},
        headers=SOCIO,
    )
    assert res.status_code == 201
    assert res.json()["osservatore"] == "mario"


def test_il_richiedente_e_un_utente_verificato(client_authelia, ricerca_authelia):
    import os
    crea_richiesta_come(client_authelia, SOCIO)
    conn = sqlite3.connect(os.environ["TELESCOPE_DB_PATH"])
    conn.row_factory = sqlite3.Row
    try:
        riga = conn.execute("""
            SELECT u.username FROM richieste r JOIN utenti u ON u.id = r.richiedente_id
        """).fetchone()
        assert riga["username"] is not None
    finally:
        conn.close()


# ─── L'esito va a chi ha chiesto ─────────────────────────────────────────────

def test_l_esito_va_all_email_del_richiedente(client_authelia, ricerca_authelia, monkeypatch):
    import router
    inviate = []
    monkeypatch.setattr(router, "invia_messaggio", lambda destinatario, oggetto, corpo: inviate.append(destinatario))

    crea_richiesta_come(client_authelia, SOCIO)
    client_authelia.patch("/telescope-time/richieste/1", json={"stato": "approvata"},
                          headers=RESPONSABILE)

    assert inviate == ["mario@example.test"]


def test_senza_email_l_esito_va_al_responsabile(client_authelia, ricerca_authelia, monkeypatch):
    import router
    inviate = []
    monkeypatch.setattr(router, "invia_messaggio", lambda destinatario, oggetto, corpo: inviate.append(destinatario))

    senza_email = {"Remote-User": "ospite", "Remote-Groups": "soci"}
    client_authelia.post("/telescope-time/richieste",
                         json={"ricerca_id": 1, "giorno_richiesto": "2026-09-14"},
                         headers=senza_email)
    client_authelia.patch("/telescope-time/richieste/1", json={"stato": "approvata"},
                          headers=RESPONSABILE)

    assert inviate == [router.EMAIL_RESPONSABILE]
