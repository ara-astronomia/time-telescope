"""Validazione lato client della pagina di richiesta.

Il server resta l'unico presidio di correttezza (vedi test_fasce_orarie.py):
qui si verifica che l'utente riceva un messaggio immediato e che le
richieste palesemente sbagliate non partano nemmeno.
"""

from datetime import date, datetime, time, timedelta

PAGINA = "/telescope_time_request.html"

# `datetime-local` non accetta i secondi: il valore è 'YYYY-MM-DDTHH:MM'.
FORMATO = "%Y-%m-%dT%H:%M"


def fascia(giorni_avanti, ora=22, durata=3):
    inizio = datetime.combine(date.today() + timedelta(days=giorni_avanti), time(ora))
    return inizio.strftime(FORMATO), (inizio + timedelta(hours=durata)).strftime(FORMATO)


def prepara(page, app_url):
    """Apre la pagina con una ricerca già disponibile nel menu."""
    page.request.post(
        f"{app_url}/telescope-time/ricerche", data={"nome": f"Ricerca {datetime.now()}"}
    )
    page.goto(f"{app_url}{PAGINA}")
    page.wait_for_selector("#ricerca_id option", state="attached")
    return page


def compila(page, inizio, fine):
    """Il nome dell'osservatore non si compila: arriva da Authelia (#5)."""
    page.select_option("#ricerca_id", index=1)
    page.fill("#inizio", inizio)
    page.fill("#fine", fine)


def inizi_inviati(page, app_url):
    return [r["inizio"] for r in page.request.get(f"{app_url}/telescope-time/richieste").json()]


# ─── Fascia nel passato ───────────────────────────────────────────────────────

def test_fascia_nel_passato_non_viene_inviata(page, app_url):
    prepara(page, app_url)
    inizio, fine = fascia(-30)
    compila(page, inizio, fine)
    page.click("#btn-submit")
    page.wait_for_timeout(500)

    assert f"{inizio}:00" not in inizi_inviati(page, app_url)


def test_fascia_nel_passato_segnalata_all_utente(page, app_url):
    prepara(page, app_url)
    compila(page, *fascia(-30))
    page.click("#btn-submit")

    assert page.locator("#inizio").evaluate("e => e.validity.rangeUnderflow") is True


# ─── Fine prima dell'inizio ───────────────────────────────────────────────────

def test_fine_precedente_all_inizio_segnalata_all_utente(page, app_url):
    """Il vincolo è relativo all'altro campo: `min` di #fine segue #inizio."""
    prepara(page, app_url)
    inizio, fine = fascia(10)
    compila(page, fine, inizio)          # invertiti
    page.click("#btn-submit")

    assert page.locator("#fine").evaluate("e => e.validity.rangeUnderflow") is True


def test_fine_precedente_all_inizio_non_viene_inviata(page, app_url):
    prepara(page, app_url)
    inizio, fine = fascia(10)
    compila(page, fine, inizio)
    page.click("#btn-submit")
    page.wait_for_timeout(500)

    assert inizi_inviati(page, app_url) == [] or f"{fine}:00" not in inizi_inviati(page, app_url)


# ─── Campi obbligatori e invio ────────────────────────────────────────────────

def test_campi_obbligatori_dichiarati_nel_markup(page, app_url):
    prepara(page, app_url)
    for campo in ("#ricerca_id", "#inizio", "#fine"):
        assert page.locator(campo).evaluate("e => e.required") is True, campo


def test_fascia_futura_viene_inviata(page, app_url):
    prepara(page, app_url)
    inizio, fine = fascia(11)
    compila(page, inizio, fine)
    page.click("#btn-submit")
    page.wait_for_selector("#toast.show")

    assert f"{inizio}:00" in inizi_inviati(page, app_url)


def test_errore_di_validazione_del_server_mostrato_all_utente(page, app_url):
    """Se il client viene aggirato, il 422 del server non deve diventare un
    generico 'Errore durante l'invio'."""
    prepara(page, app_url)
    compila(page, *fascia(12))
    # Aggira il vincolo del browser come farebbe chi manipola il DOM: un
    # input[type=datetime-local] rifiuta un valore non conforme, quindi va
    # cambiato tipo.
    page.evaluate("""
        const campo = document.getElementById('inizio');
        campo.type = 'text';
        campo.value = 'domani';
    """)
    page.click("#btn-submit")
    page.wait_for_selector("#toast.show")

    testo = page.inner_text("#toast").lower()
    assert "orario" in testo or "fascia" in testo, f"il messaggio non nomina il campo: {testo!r}"


# ─── L'identità non si digita più (#5) ────────────────────────────────────────

def test_il_nome_non_si_digita_piu(page, app_url):
    prepara(page, app_url)
    assert page.locator("#osservatore").count() == 0, "il campo del nome è ancora nel modulo"


def test_la_pagina_mostra_chi_sei(page, app_url):
    prepara(page, app_url)
    page.wait_for_selector("#utente-corrente")
    assert "sviluppo" in page.inner_text("#utente-corrente")


def test_la_richiesta_parte_senza_nome(page, app_url):
    prepara(page, app_url)
    inizio, fine = fascia(13)
    compila(page, inizio, fine)
    page.click("#btn-submit")
    page.wait_for_selector("#toast.show")

    inviate = page.request.get(f"{app_url}/telescope-time/richieste").json()
    mia = [r for r in inviate if r["inizio"] == f"{inizio}:00"]
    assert mia, "la richiesta non è stata registrata"
    assert all(r["osservatore"] == "sviluppo" for r in mia)


# ─── Identità: switcher di ruolo in dev (#26) ──────────────────────────────────

def test_lo_switcher_dev_e_visibile(page, app_url):
    page.goto(f"{app_url}{PAGINA}")
    page.wait_for_selector("#dev-switcher")
    assert page.is_visible("#dev-switcher")
