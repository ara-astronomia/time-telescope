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
