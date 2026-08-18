"""La dashboard mostra l'ora in cui la richiesta è arrivata: deve essere
quella locale di chi guarda, non l'UTC scambiato per locale (#7)."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

PAGINA = "/telescope_time_dashboard.html"
FUSO = ZoneInfo("Europe/Rome")
UTC = ZoneInfo("UTC")


@pytest.fixture
def browser_context_args(browser_context_args):
    """Fuso fissato: il test non deve dipendere da quello della macchina."""
    return {**browser_context_args, "timezone_id": "Europe/Rome"}


def crea(page, app_url, nome, giorni_avanti):
    """Crea una richiesta e restituisce (richiesta, istanti).

    Gli istanti sono due — prima e dopo la creazione — perché fra i due il
    minuto può scattare: senza, il test fallirebbe una volta ogni tanto
    senza che nulla sia rotto.
    """
    inizio = datetime.combine(date.today() + timedelta(days=giorni_avanti), time(22))
    prima = datetime.now(FUSO)
    ricerca = page.request.post(
        f"{app_url}/telescope-time/ricerche", data={"nome": nome}
    ).json()
    richiesta = page.request.post(
        f"{app_url}/telescope-time/richieste",
        data={"ricerca_id": ricerca["id"], "osservatore": "Anna Verdi",
              "inizio": inizio.isoformat(),
              "fine": (inizio + timedelta(hours=3)).isoformat()},
    ).json()
    return richiesta, (prima, datetime.now(FUSO))


def crea_con_fascia(page, app_url, nome, giorno, ora, durata):
    inizio = datetime.combine(giorno, time(ora))
    ricerca = page.request.post(
        f"{app_url}/telescope-time/ricerche", data={"nome": nome}
    ).json()
    return page.request.post(
        f"{app_url}/telescope-time/richieste",
        data={"ricerca_id": ricerca["id"], "inizio": inizio.isoformat(),
              "fine": (inizio + timedelta(hours=durata)).isoformat()},
    ).json()


def riga_meta(page, app_url, richiesta_id):
    page.goto(f"{app_url}{PAGINA}")
    page.wait_for_selector(".richiesta-card")
    return page.locator(f"#card-{richiesta_id} .rc-meta").inner_text()


def test_orario_di_creazione_mostrato_in_ora_locale(page, app_url):
    richiesta, istanti = crea(page, app_url, "Orario", 20)
    attesi = {m.strftime("%H:%M") for m in istanti}

    riga = riga_meta(page, app_url, richiesta["id"])

    assert any(ora in riga for ora in attesi), (
        f"attesa una fra {sorted(attesi)} (ora di Roma), riga: {riga!r}"
    )


def test_lo_scarto_utc_non_compare(page, app_url):
    """Controprova: l'ora UTC non deve comparire al posto di quella locale.
    Lo scarto italiano è di due ore in estate, una in inverno."""
    richiesta, istanti = crea(page, app_url, "Scarto", 21)
    ore_utc = {m.astimezone(UTC).strftime("%H:%M") for m in istanti}
    ore_locali = {m.strftime("%H:%M") for m in istanti}
    if ore_utc & ore_locali:
        pytest.skip("nessuno scarto fra UTC e ora locale in questo momento")

    riga = riga_meta(page, app_url, richiesta["id"])

    for ora in ore_utc:
        assert ora not in riga, (
            f"mostrata l'ora UTC {ora} invece di una fra {sorted(ore_locali)}"
        )


# ─── Il conflitto di fascia va detto, non nascosto (#33) ──────────────────────

def test_il_conflitto_di_fascia_e_mostrato_all_utente(page, app_url):
    """Il 409 nomina la richiesta in conflitto: se la dashboard lo appiattisce
    su 'Errore durante l'aggiornamento', il responsabile non sa cosa spostare."""
    giorno = date.today() + timedelta(days=25)
    approvata = crea_con_fascia(page, app_url, "Conflitto A", giorno, ora=21, durata=3)
    page.request.patch(
        f"{app_url}/telescope-time/richieste/{approvata['id']}", data={"stato": "approvata"}
    )
    seconda = crea_con_fascia(page, app_url, "Conflitto B", giorno, ora=23, durata=2)

    page.on("dialog", lambda d: d.accept())
    page.goto(f"{app_url}{PAGINA}")
    page.wait_for_selector(f"#card-{seconda['id']}")
    page.click(f"#card-{seconda['id']} .rc-header")
    page.click(f"#card-{seconda['id']} .btn-approve")
    page.wait_for_selector("#toast.show")

    testo = page.inner_text("#toast")
    assert f"#{approvata['id']}" in testo, f"il messaggio non nomina il conflitto: {testo!r}"


def test_la_richiesta_in_conflitto_resta_in_attesa(page, app_url):
    giorno = date.today() + timedelta(days=26)
    approvata = crea_con_fascia(page, app_url, "Conflitto C", giorno, ora=21, durata=3)
    page.request.patch(
        f"{app_url}/telescope-time/richieste/{approvata['id']}", data={"stato": "approvata"}
    )
    seconda = crea_con_fascia(page, app_url, "Conflitto D", giorno, ora=23, durata=2)

    page.on("dialog", lambda d: d.accept())
    page.goto(f"{app_url}{PAGINA}")
    page.wait_for_selector(f"#card-{seconda['id']}")
    page.click(f"#card-{seconda['id']} .rc-header")
    page.click(f"#card-{seconda['id']} .btn-approve")
    page.wait_for_selector("#toast.show")

    dopo = page.request.get(f"{app_url}/telescope-time/richieste/{seconda['id']}").json()
    assert dopo["stato"] == "in_attesa"


# ─── Spostare data e orari dalla dashboard (#34) ──────────────────────────────

def orari(page, app_url, richiesta_id):
    r = page.request.get(f"{app_url}/telescope-time/richieste/{richiesta_id}").json()
    return r["inizio"], r["fine"]


def apri_card(page, app_url, richiesta_id):
    page.goto(f"{app_url}{PAGINA}")
    page.wait_for_selector(f"#card-{richiesta_id}")
    page.click(f"#card-{richiesta_id} .rc-header")


def compila_spostamento(page, richiesta_id, giorno, ora=23, durata=4, motivo=""):
    inizio = datetime.combine(giorno, time(ora))
    page.fill(f"#sposta-inizio-{richiesta_id}", inizio.strftime("%Y-%m-%dT%H:%M"))
    page.fill(f"#sposta-fine-{richiesta_id}",
              (inizio + timedelta(hours=durata)).strftime("%Y-%m-%dT%H:%M"))
    if motivo:
        page.fill(f"#sposta-motivo-{richiesta_id}", motivo)


def test_il_comando_di_spostamento_c_e_anche_sulle_approvate(page, app_url):
    """Una richiesta approvata è un impegno preso, non un impegno immutabile:
    il meteo cambia e la notte va spostata, non cancellata."""
    giorno = date.today() + timedelta(days=40)
    richiesta = crea_con_fascia(page, app_url, "Sposta A", giorno, ora=21, durata=2)
    page.request.patch(
        f"{app_url}/telescope-time/richieste/{richiesta['id']}", data={"stato": "approvata"}
    )

    apri_card(page, app_url, richiesta["id"])

    assert page.locator(f"#sposta-inizio-{richiesta['id']}").count() == 1
    assert page.locator(f"#card-{richiesta['id']} .btn-sposta").count() == 1


def test_anche_le_rifiutate_si_spostano(page, app_url):
    """Una rifiutata per meteo si recupera spostandola e riapprovandola. Se non
    la si potesse spostare prima, bisognerebbe riapprovarla sulla fascia
    originale — che nel frattempo può essere occupata da un'altra approvata, e
    a quel punto non c'è più via d'uscita."""
    giorno = date.today() + timedelta(days=41)
    richiesta = crea_con_fascia(page, app_url, "Sposta B", giorno, ora=21, durata=2)
    page.request.patch(
        f"{app_url}/telescope-time/richieste/{richiesta['id']}", data={"stato": "rifiutata"}
    )

    page.on("dialog", lambda d: d.accept())
    apri_card(page, app_url, richiesta["id"])
    compila_spostamento(page, richiesta["id"], giorno + timedelta(days=1))
    page.click(f"#card-{richiesta['id']} .btn-sposta")
    page.wait_for_selector("#toast.show")

    inizio, _ = orari(page, app_url, richiesta["id"])
    assert inizio == f"{giorno + timedelta(days=1)}T23:00:00"


def test_lo_spostamento_cambia_gli_orari(page, app_url):
    giorno = date.today() + timedelta(days=42)
    richiesta = crea_con_fascia(page, app_url, "Sposta C", giorno, ora=21, durata=2)

    page.on("dialog", lambda d: d.accept())
    apri_card(page, app_url, richiesta["id"])
    compila_spostamento(page, richiesta["id"], giorno + timedelta(days=1), motivo="Manutenzione")
    page.click(f"#card-{richiesta['id']} .btn-sposta")
    page.wait_for_selector("#toast.show")

    inizio, _ = orari(page, app_url, richiesta["id"])
    assert inizio == f"{giorno + timedelta(days=1)}T23:00:00"


def test_una_data_passata_e_dichiarata_prima_di_confermare(page, app_url):
    """Spostare nel passato è permesso, ma dev'essere una scelta consapevole:
    la conferma lo dice, invece di lasciarlo passare in silenzio."""
    giorno = date.today() + timedelta(days=43)
    richiesta = crea_con_fascia(page, app_url, "Sposta D", giorno, ora=21, durata=2)

    messaggi = []
    page.on("dialog", lambda d: (messaggi.append(d.message), d.dismiss()))
    apri_card(page, app_url, richiesta["id"])
    compila_spostamento(page, richiesta["id"], date.today() - timedelta(days=5))
    page.click(f"#card-{richiesta['id']} .btn-sposta")
    page.wait_for_timeout(400)

    assert messaggi, "nessuna conferma chiesta"
    assert "trascors" in messaggi[0].lower(), messaggi[0]


def test_la_conferma_rifiutata_non_sposta_nulla(page, app_url):
    giorno = date.today() + timedelta(days=44)
    richiesta = crea_con_fascia(page, app_url, "Sposta E", giorno, ora=21, durata=2)
    prima = orari(page, app_url, richiesta["id"])

    page.on("dialog", lambda d: d.dismiss())
    apri_card(page, app_url, richiesta["id"])
    compila_spostamento(page, richiesta["id"], giorno + timedelta(days=1))
    page.click(f"#card-{richiesta['id']} .btn-sposta")
    page.wait_for_timeout(400)

    assert orari(page, app_url, richiesta["id"]) == prima


def test_il_conflitto_di_fascia_blocca_lo_spostamento(page, app_url):
    giorno = date.today() + timedelta(days=45)
    occupata = crea_con_fascia(page, app_url, "Sposta F", giorno, ora=21, durata=3)
    mobile = crea_con_fascia(page, app_url, "Sposta G", giorno + timedelta(days=2), ora=21, durata=2)
    for r in (occupata, mobile):
        page.request.patch(
            f"{app_url}/telescope-time/richieste/{r['id']}", data={"stato": "approvata"}
        )

    page.on("dialog", lambda d: d.accept())
    apri_card(page, app_url, mobile["id"])
    compila_spostamento(page, mobile["id"], giorno, ora=22, durata=2)
    page.click(f"#card-{mobile['id']} .btn-sposta")
    page.wait_for_selector("#toast.show")

    testo = page.inner_text("#toast")
    assert f"#{occupata['id']}" in testo, testo


# ─── Cambiare una decisione già presa (#45) ───────────────────────────────────

def decisa(page, app_url, nome, giorni_avanti, stato, ora=21):
    giorno = date.today() + timedelta(days=giorni_avanti)
    richiesta = crea_con_fascia(page, app_url, nome, giorno, ora=ora, durata=2)
    page.request.patch(
        f"{app_url}/telescope-time/richieste/{richiesta['id']}", data={"stato": stato}
    )
    return richiesta


def stato_di(page, app_url, richiesta_id):
    return page.request.get(
        f"{app_url}/telescope-time/richieste/{richiesta_id}"
    ).json()["stato"]


def test_una_approvata_si_puo_rifiutare(page, app_url):
    richiesta = decisa(page, app_url, "Ribalta A", 50, "approvata")

    page.on("dialog", lambda d: d.accept())
    apri_card(page, app_url, richiesta["id"])
    page.click(f"#card-{richiesta['id']} .btn-reject")
    page.wait_for_selector("#toast.show")

    assert stato_di(page, app_url, richiesta["id"]) == "rifiutata"


def test_una_rifiutata_si_puo_approvare(page, app_url):
    richiesta = decisa(page, app_url, "Ribalta B", 51, "rifiutata")

    page.on("dialog", lambda d: d.accept())
    apri_card(page, app_url, richiesta["id"])
    page.click(f"#card-{richiesta['id']} .btn-approve")
    page.wait_for_selector("#toast.show")

    assert stato_di(page, app_url, richiesta["id"]) == "approvata"


def test_il_comando_che_non_cambia_nulla_non_compare(page, app_url):
    """Riapprovare una approvata il server lo tratta già come un non-evento:
    mostrarlo suggerirebbe un'azione che non fa niente."""
    approvata = decisa(page, app_url, "Ribalta C", 52, "approvata")
    rifiutata = decisa(page, app_url, "Ribalta D", 53, "rifiutata")

    apri_card(page, app_url, approvata["id"])
    assert page.locator(f"#card-{approvata['id']} .btn-approve").count() == 0
    assert page.locator(f"#card-{approvata['id']} .btn-reject").count() == 1

    apri_card(page, app_url, rifiutata["id"])
    assert page.locator(f"#card-{rifiutata['id']} .btn-reject").count() == 0
    assert page.locator(f"#card-{rifiutata['id']} .btn-approve").count() == 1


def test_una_in_attesa_ha_entrambi_i_comandi(page, app_url):
    giorno = date.today() + timedelta(days=54)
    richiesta = crea_con_fascia(page, app_url, "Ribalta E", giorno, ora=21, durata=2)

    apri_card(page, app_url, richiesta["id"])

    assert page.locator(f"#card-{richiesta['id']} .btn-approve").count() == 1
    assert page.locator(f"#card-{richiesta['id']} .btn-reject").count() == 1


def test_ribaltare_avverte_che_l_esito_e_gia_stato_comunicato(page, app_url):
    """Non è la stessa cosa di una prima decisione: l'osservatore ha già
    ricevuto un esito e ne riceverà un secondo."""
    richiesta = decisa(page, app_url, "Ribalta F", 55, "approvata")

    messaggi = []
    page.on("dialog", lambda d: (messaggi.append(d.message), d.dismiss()))
    apri_card(page, app_url, richiesta["id"])
    page.click(f"#card-{richiesta['id']} .btn-reject")
    page.wait_for_timeout(400)

    assert messaggi, "nessuna conferma chiesta"
    testo = messaggi[0].lower()
    assert "email" in testo, messaggi[0]
    assert "approvazione" in testo or "già" in testo, messaggi[0]


def test_le_note_restano_scrivibili_su_una_decisa(page, app_url):
    richiesta = decisa(page, app_url, "Ribalta G", 56, "approvata")

    page.on("dialog", lambda d: d.accept())
    apri_card(page, app_url, richiesta["id"])
    page.fill(f"#note-{richiesta['id']}", "Previsioni peggiorate")
    page.click(f"#card-{richiesta['id']} .btn-reject")
    page.wait_for_selector("#toast.show")

    note = page.request.get(
        f"{app_url}/telescope-time/richieste/{richiesta['id']}"
    ).json()["note_responsabile"]
    assert note == "Previsioni peggiorate"


# ─── Lo storico diventa visibile ──────────────────────────────────────────────

def test_lo_storico_e_mostrato_nel_dettaglio(page, app_url):
    """Senza, un ribaltamento è indistinguibile da una decisione presa una
    volta sola — ed è proprio ciò che giustifica il permetterlo."""
    richiesta = decisa(page, app_url, "Storico A", 57, "approvata")
    page.request.patch(
        f"{app_url}/telescope-time/richieste/{richiesta['id']}",
        data={"stato": "rifiutata", "note_responsabile": "Meteo peggiorato"},
    )

    apri_card(page, app_url, richiesta["id"])
    page.wait_for_selector(f"#storico-{richiesta['id']} .voce")

    testo = page.inner_text(f"#storico-{richiesta['id']}")
    assert "approvata" in testo.lower()
    assert "rifiutata" in testo.lower()
    assert "Meteo peggiorato" in testo


def test_lo_storico_mostra_anche_gli_spostamenti(page, app_url):
    giorno = date.today() + timedelta(days=58)
    richiesta = crea_con_fascia(page, app_url, "Storico B", giorno, ora=21, durata=2)
    nuovo = datetime.combine(giorno + timedelta(days=1), time(23))
    page.request.patch(
        f"{app_url}/telescope-time/richieste/{richiesta['id']}/orario",
        data={"inizio": nuovo.isoformat(),
              "fine": (nuovo + timedelta(hours=3)).isoformat(),
              "motivo": "Manutenzione"},
    )

    apri_card(page, app_url, richiesta["id"])
    page.wait_for_selector(f"#storico-{richiesta['id']} .voce")

    testo = page.inner_text(f"#storico-{richiesta['id']}")
    assert "23:00" in testo, testo
    assert "Manutenzione" in testo


def test_una_richiesta_mai_decisa_dichiara_lo_storico_vuoto(page, app_url):
    giorno = date.today() + timedelta(days=59)
    richiesta = crea_con_fascia(page, app_url, "Storico C", giorno, ora=21, durata=2)

    apri_card(page, app_url, richiesta["id"])
    page.wait_for_selector(f"#storico-{richiesta['id']}")

    assert page.locator(f"#storico-{richiesta['id']} .voce").count() == 0
    assert page.inner_text(f"#storico-{richiesta['id']}").strip() != ""
