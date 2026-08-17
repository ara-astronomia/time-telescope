"""Validazione lato client della pagina di richiesta.

Il server resta l'unico presidio di correttezza (vedi test_richieste.py):
qui si verifica che l'utente riceva un messaggio immediato e che le
richieste palesemente sbagliate non partano nemmeno.
"""

from datetime import date, timedelta

PAGINA = "/telescope_time_request.html"


def prepara(page, app_url):
    """Apre la pagina con una ricerca già disponibile nel menu."""
    page.request.post(
        f"{app_url}/telescope-time/ricerche", data={"nome": f"Ricerca {date.today()}"}
    )
    page.goto(f"{app_url}{PAGINA}")
    page.wait_for_selector("#ricerca_id option", state="attached")
    return page


def compila(page, giorno):
    """Il nome dell'osservatore non si compila: arriva da Authelia (#5)."""
    page.select_option("#ricerca_id", index=1)
    page.fill("#giorno_richiesto", giorno)


def giorni_inviati(page, app_url):
    return [r["giorno_richiesto"] for r in page.request.get(f"{app_url}/telescope-time/richieste").json()]


def test_data_nel_passato_non_viene_inviata(page, app_url):
    prepara(page, app_url)
    compila(page, "2020-01-01")
    page.click("#btn-submit")
    page.wait_for_timeout(500)
    assert "2020-01-01" not in giorni_inviati(page, app_url)


def test_data_nel_passato_segnalata_all_utente(page, app_url):
    prepara(page, app_url)
    compila(page, "2020-01-01")
    page.click("#btn-submit")
    campo = page.locator("#giorno_richiesto")
    assert campo.evaluate("e => e.validity.rangeUnderflow") is True


def test_campi_obbligatori_dichiarati_nel_markup(page, app_url):
    prepara(page, app_url)
    for campo in ("#ricerca_id", "#giorno_richiesto"):
        assert page.locator(campo).evaluate("e => e.required") is True, campo


def test_data_futura_viene_inviata(page, app_url):
    prepara(page, app_url)
    domani = (date.today() + timedelta(days=1)).isoformat()
    compila(page, domani)
    page.click("#btn-submit")
    page.wait_for_selector("#toast.show")
    assert domani in giorni_inviati(page, app_url)


def test_errore_di_validazione_del_server_mostrato_all_utente(page, app_url):
    """Se il client viene aggirato, il 422 del server non deve diventare un
    generico 'Errore durante l'invio'."""
    prepara(page, app_url)
    dopodomani = (date.today() + timedelta(days=2)).isoformat()
    compila(page, dopodomani)
    # Aggira il vincolo del browser come farebbe chi manipola il DOM: un
    # input[type=date] rifiuta un valore non conforme, quindi va cambiato tipo.
    page.evaluate("""
        const campo = document.getElementById('giorno_richiesto');
        campo.type = 'text';
        campo.value = 'domani';
    """)
    page.click("#btn-submit")
    page.wait_for_selector("#toast.show")
    testo = page.inner_text("#toast").lower()
    assert "data" in testo, f"il messaggio non nomina il campo: {testo!r}"


# ─── L'identità non si digita più (#5) ────────────────────────────────────────

def test_il_nome_non_si_digita_piu(page, app_url):
    prepara(page, app_url)
    assert page.locator("#osservatore").count() == 0, "il campo del nome è ancora nel modulo"


def test_la_pagina_mostra_chi_sei(page, app_url):
    prepara(page, app_url)
    page.wait_for_selector("#utente-corrente")
    assert "sviluppo" in page.inner_text("#utente-corrente")


def test_la_richiesta_parte_senza_nome(page, app_url):
    from datetime import date, timedelta
    prepara(page, app_url)
    fra_una_settimana = (date.today() + timedelta(days=7)).isoformat()
    page.select_option("#ricerca_id", index=1)
    page.fill("#giorno_richiesto", fra_una_settimana)
    page.click("#btn-submit")
    page.wait_for_selector("#toast.show")

    inviate = page.request.get(f"{app_url}/telescope-time/richieste").json()
    mia = [r for r in inviate if r["giorno_richiesto"] == fra_una_settimana]
    assert mia, "la richiesta non è stata registrata"
    assert all(r["osservatore"] == "sviluppo" for r in mia)
