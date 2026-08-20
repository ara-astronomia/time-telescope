"""La dashboard mostra l'ora in cui la richiesta è arrivata: deve essere
quella locale di chi guarda, non l'UTC scambiato per locale (#7)."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from conftest import come_socio

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


def test_svuotare_le_note_le_cancella(page, app_url):
    """Il campo si apre precompilato: se svuotarlo non cancellasse nulla,
    sembrerebbe modificabile senza esserlo."""
    richiesta = decisa(page, app_url, "Note A", 60, "approvata")
    page.request.patch(
        f"{app_url}/telescope-time/richieste/{richiesta['id']}",
        data={"stato": "approvata", "note_responsabile": "Da cancellare"},
    )

    page.on("dialog", lambda d: d.accept())
    apri_card(page, app_url, richiesta["id"])
    page.fill(f"#note-{richiesta['id']}", "")
    page.click(f"#card-{richiesta['id']} .btn-reject")
    page.wait_for_selector("#toast.show")

    note = page.request.get(
        f"{app_url}/telescope-time/richieste/{richiesta['id']}"
    ).json()["note_responsabile"]
    assert not note, f"le note non sono state cancellate: {note!r}"


def test_le_note_esistenti_compaiono_nel_campo(page, app_url):
    richiesta = decisa(page, app_url, "Note B", 61, "approvata")
    page.request.patch(
        f"{app_url}/telescope-time/richieste/{richiesta['id']}",
        data={"stato": "approvata", "note_responsabile": "Meteo stabile"},
    )

    apri_card(page, app_url, richiesta["id"])

    assert page.input_value(f"#note-{richiesta['id']}") == "Meteo stabile"


# ─── Il dettaglio non si richiude sotto le mani ───────────────────────────────

def aperta(page, richiesta_id):
    return "open" in (page.locator(f"#detail-{richiesta_id}").get_attribute("class") or "")


def test_la_card_resta_aperta_dopo_una_decisione(page, app_url):
    """Ricostruire la lista chiudeva la card su cui si stava lavorando: con due
    o tre azioni di fila sulla stessa richiesta, si riapre ogni volta."""
    richiesta = decisa(page, app_url, "Aperta A", 62, "approvata")

    page.on("dialog", lambda d: d.accept())
    apri_card(page, app_url, richiesta["id"])
    page.click(f"#card-{richiesta['id']} .btn-reject")
    page.wait_for_selector("#toast.show")
    page.wait_for_timeout(400)

    assert aperta(page, richiesta["id"])


def test_lo_storico_si_aggiorna_senza_riaprire(page, app_url):
    richiesta = decisa(page, app_url, "Aperta B", 63, "approvata")

    page.on("dialog", lambda d: d.accept())
    apri_card(page, app_url, richiesta["id"])
    page.click(f"#card-{richiesta['id']} .btn-reject")
    page.wait_for_selector("#toast.show")
    page.wait_for_function(
        f"document.querySelectorAll('#storico-{richiesta['id']} .voce').length === 2"
    )

    assert "rifiutata" in page.inner_text(f"#storico-{richiesta['id']}")


def test_la_card_resta_aperta_dopo_uno_spostamento(page, app_url):
    giorno = date.today() + timedelta(days=64)
    richiesta = crea_con_fascia(page, app_url, "Aperta C", giorno, ora=21, durata=2)

    page.on("dialog", lambda d: d.accept())
    apri_card(page, app_url, richiesta["id"])
    compila_spostamento(page, richiesta["id"], giorno + timedelta(days=1))
    page.click(f"#card-{richiesta['id']} .btn-sposta")
    page.wait_for_selector("#toast.show")
    page.wait_for_timeout(400)

    assert aperta(page, richiesta["id"])


def test_le_card_chiuse_restano_chiuse(page, app_url):
    giorno = date.today() + timedelta(days=65)
    aperta_ = crea_con_fascia(page, app_url, "Aperta D", giorno, ora=21, durata=2)
    chiusa = crea_con_fascia(page, app_url, "Aperta E", giorno, ora=23, durata=2)

    page.on("dialog", lambda d: d.accept())
    apri_card(page, app_url, aperta_["id"])
    page.click(f"#card-{aperta_['id']} .btn-approve")
    page.wait_for_selector("#toast.show")
    page.wait_for_timeout(400)

    assert not aperta(page, chiusa["id"])


# ─── Identità: nascondere i comandi a chi non è responsabile (#26) ────────────

def test_socio_non_vede_i_comandi_responsabili(page, app_url):
    giorno = date.today() + timedelta(days=70)
    richiesta = crea_con_fascia(page, app_url, "Nascosti", giorno, ora=21, durata=2)

    come_socio(page)
    apri_card(page, app_url, richiesta["id"])

    assert page.locator(f"#detail-{richiesta['id']} .action-area").count() == 0
    assert page.locator(f"#detail-{richiesta['id']} .sposta-area").count() == 0


def test_il_banner_mostra_chi_e_collegato(page, app_url):
    page.goto(f"{app_url}{PAGINA}")
    page.wait_for_selector("#utente-corrente:not(:empty)")
    assert "Marta Conti" in page.inner_text("#utente-corrente")


def test_il_banner_segue_il_cambio_di_utente(page, app_url):
    come_socio(page)
    page.goto(f"{app_url}{PAGINA}")
    page.wait_for_selector("#utente-corrente:not(:empty)")
    assert "mario" in page.inner_text("#utente-corrente")


def test_403_su_approvazione_raggiunta_da_pulsante_gia_nascosto(page, app_url):
    """Chi ha la pagina aperta da prima di un cambio di gruppo non vede più
    i pulsanti, ma la funzione resta richiamabile: il messaggio deve
    distinguersi da un errore generico di rete (#26)."""
    giorno = date.today() + timedelta(days=71)
    richiesta = crea_con_fascia(page, app_url, "403 stato", giorno, ora=21, durata=2)

    come_socio(page)
    apri_card(page, app_url, richiesta["id"])
    page.on("dialog", lambda d: d.accept())
    # `aggiornaStato` legge #note-N dal DOM, che qui non esiste perché
    # l'area è nascosta: lo inietto per riprodurre lo scenario "pagina
    # aperta da prima del cambio di gruppo" senza dipendere dal markup
    # nascosto.
    page.evaluate(f"""() => {{
        const i = document.createElement('textarea');
        i.id = 'note-{richiesta["id"]}';
        document.body.appendChild(i);
    }}""")
    page.evaluate(f"aggiornaStato({richiesta['id']}, 'approvata', 'in_attesa')")
    page.wait_for_selector("#toast.show")

    assert page.inner_text("#toast") == 'Solo i responsabili possono approvare o rifiutare.'

    dopo = page.request.get(f"{app_url}/telescope-time/richieste/{richiesta['id']}").json()
    assert dopo["stato"] == "in_attesa"


def test_403_su_spostamento_raggiunto_da_pulsante_gia_nascosto(page, app_url):
    giorno = date.today() + timedelta(days=72)
    richiesta = crea_con_fascia(page, app_url, "403 orario", giorno, ora=21, durata=2)

    come_socio(page)
    apri_card(page, app_url, richiesta["id"])
    page.on("dialog", lambda d: d.accept())
    # `spostaOrario` legge i campi #sposta-inizio-N/#sposta-fine-N dal DOM,
    # che qui non esistono perché l'area è nascosta: li inietto prima di
    # richiamare la funzione, per riprodurre lo scenario "pagina aperta da
    # prima del cambio di gruppo" senza dipendere dal markup nascosto.
    nuovo_inizio = datetime.combine(giorno + timedelta(days=1), time(22)).strftime("%Y-%m-%dT%H:%M")
    nuova_fine   = datetime.combine(giorno + timedelta(days=1), time(23)).strftime("%Y-%m-%dT%H:%M")
    page.evaluate(f"""() => {{
        const mk = (id, val) => {{ const i = document.createElement('input'); i.id = id; i.value = val; document.body.appendChild(i); }};
        mk('sposta-inizio-{richiesta["id"]}', '{nuovo_inizio}');
        mk('sposta-fine-{richiesta["id"]}', '{nuova_fine}');
        mk('sposta-motivo-{richiesta["id"]}', '');
    }}""")
    page.evaluate(f"spostaOrario({richiesta['id']})")
    page.wait_for_selector("#toast.show")

    assert page.inner_text("#toast") == 'Solo i responsabili possono spostare una richiesta.'


# ─── Switcher di ruolo in dev (#26) ────────────────────────────────────────────

def test_lo_switcher_dev_e_visibile(page, app_url):
    page.goto(f"{app_url}{PAGINA}")
    page.wait_for_selector("#dev-switcher")
    assert page.is_visible("#dev-switcher")


def test_il_ruolo_attivo_e_evidenziato(page, app_url):
    page.goto(f"{app_url}{PAGINA}")
    page.wait_for_selector("#dev-switcher")
    assert "active" in (page.get_attribute("#dev-btn-responsabile", "class") or "")
    assert "active" not in (page.get_attribute("#dev-btn-socio", "class") or "")

    with page.expect_navigation():
        page.click("#dev-btn-socio")
    page.wait_for_selector("#dev-switcher")
    assert "active" in (page.get_attribute("#dev-btn-socio", "class") or "")
    assert "active" not in (page.get_attribute("#dev-btn-responsabile", "class") or "")


def test_passa_a_socio_nasconde_i_comandi_senza_riavviare(page, app_url):
    giorno = date.today() + timedelta(days=73)
    richiesta = crea_con_fascia(page, app_url, "Switcher", giorno, ora=21, durata=2)

    page.goto(f"{app_url}{PAGINA}")
    page.wait_for_selector("#dev-switcher")
    with page.expect_navigation():
        page.click("#dev-btn-socio")
    page.wait_for_selector("#utente-corrente:not(:empty)")
    assert "Luca Bertani" in page.inner_text("#utente-corrente")

    page.click(f"#card-{richiesta['id']} .rc-header")
    page.wait_for_timeout(200)
    assert page.locator(f"#detail-{richiesta['id']} .action-area").count() == 0


def test_link_al_calendario_presente(page, app_url):
    page.goto(f"{app_url}{PAGINA}")
    assert page.locator('a[href="telescope_time_calendario.html"]').count() == 1


def test_link_al_modulo_richiesta_presente(page, app_url):
    page.goto(f"{app_url}{PAGINA}")
    assert page.locator('a[href="telescope_time_request.html"]').count() == 1


# ─── L'osservatore sposta la propria richiesta in attesa ──────────────────────

def crea_richiesta_propria(page, app_url, nome, giorno, ora=21, durata=2, headers=None):
    ricerca = page.request.post(
        f"{app_url}/telescope-time/ricerche", data={"nome": nome}, headers=headers or {}
    ).json()
    inizio = datetime.combine(giorno, time(ora))
    return page.request.post(
        f"{app_url}/telescope-time/richieste",
        data={"ricerca_id": ricerca["id"], "inizio": inizio.isoformat(),
              "fine": (inizio + timedelta(hours=durata)).isoformat()},
        headers=headers or {},
    ).json()


def test_il_proprietario_vede_lo_spostamento_sulla_propria_in_attesa(page, app_url):
    come_socio(page)
    giorno = date.today() + timedelta(days=74)
    richiesta = crea_richiesta_propria(page, app_url, "Propria A", giorno)

    apri_card(page, app_url, richiesta["id"])

    assert page.locator(f"#detail-{richiesta['id']} .sposta-area").count() == 1
    assert page.locator(f"#detail-{richiesta['id']} .action-area").count() == 0


def test_il_proprietario_non_vede_lo_spostamento_su_una_propria_approvata(page, app_url):
    come_socio(page)
    giorno = date.today() + timedelta(days=75)
    richiesta = crea_richiesta_propria(page, app_url, "Propria B", giorno)
    page.request.patch(
        f"{app_url}/telescope-time/richieste/{richiesta['id']}",
        data={"stato": "approvata"},
        headers={"Remote-User": "anna", "Remote-Groups": "soci,telescope-responsabili"},
    )

    apri_card(page, app_url, richiesta["id"])

    assert page.locator(f"#detail-{richiesta['id']} .sposta-area").count() == 0
