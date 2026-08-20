"""Il calendario deve rendere visibile la compresenza di più osservazioni nella
stessa notte (#4) e distinguere una notte semplicemente richiesta da una
davvero contesa (#33)."""

from datetime import date, datetime, time, timedelta

PAGINA = "/telescope_time_calendario.html"


def mese_prossimo() -> date:
    """Le richieste si accettano solo nel futuro: il mese prossimo lo è per
    intero, qualunque sia il giorno di oggi."""
    oggi = date.today()
    return date(oggi.year + (oggi.month == 12), oggi.month % 12 + 1, 1)


def crea(page, app_url, giorno, ora, durata=2, nome=None, approvata=False):
    inizio = datetime.combine(giorno, time(ora))
    ricerca = page.request.post(
        f"{app_url}/telescope-time/ricerche",
        data={"nome": nome or f"Ricerca {giorno} {ora}"},
    ).json()
    richiesta = page.request.post(
        f"{app_url}/telescope-time/richieste",
        data={
            "ricerca_id": ricerca["id"],
            "inizio": inizio.isoformat(),
            "fine": (inizio + timedelta(hours=durata)).isoformat(),
        },
    ).json()
    if approvata:
        page.request.patch(
            f"{app_url}/telescope-time/richieste/{richiesta['id']}",
            data={"stato": "approvata"},
        )
    return richiesta


def apri(page, app_url, giorno):
    """Apre il calendario sul mese di `giorno`, che non è quello corrente."""
    page.goto(f"{app_url}{PAGINA}")
    page.wait_for_selector(".day-cell")
    page.click(".month-nav button:last-of-type")
    page.wait_for_selector(f'.day-cell[data-giorno="{giorno.isoformat()}"]')
    return page.locator(f'.day-cell[data-giorno="{giorno.isoformat()}"]')


def classe(cella):
    return cella.get_attribute("class") or ""


# ─── Compresenza (#4) ─────────────────────────────────────────────────────────

def test_notte_con_due_programmi_segnalata_nella_griglia(page, app_url):
    giorno = mese_prossimo().replace(day=11)
    crea(page, app_url, giorno, ora=18, approvata=True)
    crea(page, app_url, giorno, ora=21, approvata=True)

    cella = apri(page, app_url, giorno)
    assert cella.locator(".turni").count() == 1
    assert "2" in cella.locator(".turni").inner_text()


def test_notte_con_un_solo_programma_non_segnalata(page, app_url):
    giorno = mese_prossimo().replace(day=12)
    crea(page, app_url, giorno, ora=18, approvata=True)

    cella = apri(page, app_url, giorno)
    assert cella.locator(".turni").count() == 0
    assert "bloccata" in classe(cella)


# ─── Richiesta ≠ contesa (#33, assorbe #42) ───────────────────────────────────

def test_una_sola_richiesta_non_colora_la_notte_come_contesa(page, app_url):
    giorno = mese_prossimo().replace(day=13)
    crea(page, app_url, giorno, ora=21)

    cella = apri(page, app_url, giorno)
    assert "richiesta" in classe(cella)
    assert "contesa" not in classe(cella)


def test_due_richieste_sovrapposte_colorano_la_notte_come_contesa(page, app_url):
    giorno = mese_prossimo().replace(day=14)
    crea(page, app_url, giorno, ora=21, durata=3)
    crea(page, app_url, giorno, ora=22, durata=3)

    cella = apri(page, app_url, giorno)
    assert "contesa" in classe(cella)


def test_due_richieste_in_turni_distinti_non_sono_contesa(page, app_url):
    giorno = mese_prossimo().replace(day=15)
    crea(page, app_url, giorno, ora=18, durata=2)
    crea(page, app_url, giorno, ora=21, durata=2)

    cella = apri(page, app_url, giorno)
    assert "richiesta" in classe(cella)
    assert "contesa" not in classe(cella)


def test_la_legenda_distingue_i_due_stati(page, app_url):
    page.goto(f"{app_url}{PAGINA}")
    page.wait_for_selector(".legenda")
    legenda = page.inner_text(".legenda").lower()

    assert "richiesta" in legenda
    assert "contesa" in legenda


# ─── La fascia oraria è visibile nel dettaglio ────────────────────────────────

def test_il_dettaglio_della_notte_mostra_la_fascia(page, app_url):
    giorno = mese_prossimo().replace(day=16)
    crea(page, app_url, giorno, ora=21, durata=3)

    cella = apri(page, app_url, giorno)
    cella.click()
    page.wait_for_selector("#overlay.open")

    assert "21:00" in page.inner_text("#dp-content")
    assert "00:00" in page.inner_text("#dp-content")


# ─── Identità: il calendario mostra anche lui chi è collegato (#26) ───────────

def test_il_banner_mostra_chi_e_collegato(page, app_url):
    page.goto(f"{app_url}{PAGINA}")
    page.wait_for_selector("#utente-corrente:not(:empty)")
    assert "Marta Conti" in page.inner_text("#utente-corrente")


def test_lo_switcher_dev_e_visibile(page, app_url):
    page.goto(f"{app_url}{PAGINA}")
    page.wait_for_selector("#dev-switcher")
    assert page.is_visible("#dev-switcher")


def test_link_alla_dashboard_e_al_modulo_presenti(page, app_url):
    page.goto(f"{app_url}{PAGINA}")
    assert page.locator('a[href="telescope_time_dashboard.html"]').count() == 1
    assert page.locator('a[href="telescope_time_request.html"]').count() == 1
