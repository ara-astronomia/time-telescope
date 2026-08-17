"""Il calendario deve rendere visibile la compresenza di più osservazioni
nella stessa notte (#4): il solo colore `bloccata` non la distingue."""

from datetime import date

PAGINA = "/telescope_time_calendario.html"


def prepara_giorno(page, app_url, giorno, quante_approvate):
    """Crea `quante_approvate` osservazioni approvate nello stesso giorno."""
    for i in range(quante_approvate):
        nome = f"Ricerca {giorno} {i}"
        ricerca = page.request.post(
            f"{app_url}/telescope-time/ricerche", data={"nome": nome}
        ).json()
        richiesta = page.request.post(
            f"{app_url}/telescope-time/richieste",
            data={"ricerca_id": ricerca["id"], "osservatore": f"Osservatore {i}",
                  "giorno_richiesto": giorno},
        ).json()
        page.request.patch(
            f"{app_url}/telescope-time/richieste/{richiesta['id']}",
            data={"stato": "approvata"},
        )


def cella(page, giorno):
    return page.locator(f'.day-cell[data-giorno="{giorno}"]')


def test_giorno_con_due_programmi_segnalato_nella_griglia(page, app_url):
    giorno = f"{date.today().year}-{date.today().month:02d}-11"
    prepara_giorno(page, app_url, giorno, 2)
    page.goto(f"{app_url}{PAGINA}")
    page.wait_for_selector(".day-cell")

    assert cella(page, giorno).locator(".turni").count() == 1
    assert "2" in cella(page, giorno).locator(".turni").inner_text()


def test_giorno_con_un_solo_programma_non_segnalato(page, app_url):
    giorno = f"{date.today().year}-{date.today().month:02d}-12"
    prepara_giorno(page, app_url, giorno, 1)
    page.goto(f"{app_url}{PAGINA}")
    page.wait_for_selector(".day-cell")

    assert cella(page, giorno).locator(".turni").count() == 0
    assert "bloccata" in (cella(page, giorno).get_attribute("class") or "")


def test_giorno_conteso_non_segnalato_come_turni(page, app_url):
    giorno = f"{date.today().year}-{date.today().month:02d}-13"
    ricerca = page.request.post(
        f"{app_url}/telescope-time/ricerche", data={"nome": f"Contesa {giorno}"}
    ).json()
    page.request.post(
        f"{app_url}/telescope-time/richieste",
        data={"ricerca_id": ricerca["id"], "osservatore": "Anna", "giorno_richiesto": giorno},
    )
    page.goto(f"{app_url}{PAGINA}")
    page.wait_for_selector(".day-cell")

    assert cella(page, giorno).locator(".turni").count() == 0
    assert "contesa" in (cella(page, giorno).get_attribute("class") or "")
