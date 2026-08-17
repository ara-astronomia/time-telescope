"""La dashboard mostra l'ora in cui la richiesta è arrivata: deve essere
quella locale di chi guarda, non l'UTC scambiato per locale (#7)."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

PAGINA = "/telescope_time_dashboard.html"
FUSO = ZoneInfo("Europe/Rome")


@pytest.fixture
def browser_context_args(browser_context_args):
    """Fuso fissato: il test non deve dipendere da quello della macchina."""
    return {**browser_context_args, "timezone_id": "Europe/Rome"}


def crea(page, app_url, nome, giorno):
    ricerca = page.request.post(
        f"{app_url}/telescope-time/ricerche", data={"nome": nome}
    ).json()
    return page.request.post(
        f"{app_url}/telescope-time/richieste",
        data={"ricerca_id": ricerca["id"], "osservatore": "Anna Verdi",
              "giorno_richiesto": giorno},
    ).json()


def test_orario_di_creazione_mostrato_in_ora_locale(page, app_url):
    # L'ora vera in cui la richiesta viene creata, nel fuso di chi guarda.
    # Non si deriva dal timestamp dell'API: è proprio quello sotto esame.
    richiesta = crea(page, app_url, "Orario", "2026-09-12")
    atteso = datetime.now(FUSO).strftime("%H:%M")

    page.goto(f"{app_url}{PAGINA}")
    page.wait_for_selector(".richiesta-card")
    riga = page.locator(f'#card-{richiesta["id"]} .rc-meta').inner_text()

    assert atteso in riga, f"atteso {atteso} (ora di Roma), riga: {riga!r}"


def test_lo_scarto_utc_non_compare(page, app_url):
    """Controprova esplicita: l'ora UTC non deve essere mostrata come se
    fosse locale. Con l'ora legale italiana lo scarto è di due ore."""
    richiesta = crea(page, app_url, "Scarto", "2026-09-13")
    adesso = datetime.now(FUSO)
    ora_locale = adesso.strftime("%H:%M")
    ora_utc = adesso.astimezone(ZoneInfo("UTC")).strftime("%H:%M")
    if ora_utc == ora_locale:
        pytest.skip("nessuno scarto fra UTC e ora locale in questo momento")

    page.goto(f"{app_url}{PAGINA}")
    page.wait_for_selector(".richiesta-card")
    riga = page.locator(f'#card-{richiesta["id"]} .rc-meta').inner_text()

    assert ora_utc not in riga, f"mostrata l\'ora UTC {ora_utc} invece di {ora_locale}"
