"""Due richieste HTTP simultanee: la dashboard le fa sempre (Promise.all su
/richieste e /ricerche), quindi non è un caso di laboratorio."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import httpx2 as httpx


def test_chiamate_simultanee_non_falliscono(app_url):
    def chiama(percorso):
        return httpx.get(f"{app_url}{percorso}", timeout=10).status_code

    percorsi = ["/telescope-time/richieste", "/telescope-time/ricerche"] * 8
    with ThreadPoolExecutor(max_workers=8) as pool:
        esiti = list(pool.map(chiama, percorsi))

    assert set(esiti) == {200}, f"esiti inattesi: {sorted(set(esiti))}"


# ─── Il vincolo di sovrapposizione regge alla concorrenza (#33, #34) ──────────

def test_due_approvazioni_simultanee_non_creano_sovrapposizione(app_url, monkeypatch):
    """Fra il controllo di conflitto e la scrittura c'è una finestra: senza una
    transazione esclusiva, due approvazioni simultanee la attraversano entrambe
    e la sovrapposizione che il vincolo esiste per impedire si crea lo stesso.

    Il ritardo iniettato rende la finestra sempre osservabile, invece di
    lasciare l'esito al caso.
    """
    import time
    import router

    originale = router.gia_approvata_negli_stessi_istanti

    def lenta(*args, **kwargs):
        esito = originale(*args, **kwargs)
        time.sleep(0.3)
        return esito

    monkeypatch.setattr(router, "gia_approvata_negli_stessi_istanti", lenta)

    giorno = (date.today() + timedelta(days=70)).isoformat()
    ids = []
    for ora in ("21:00:00", "22:00:00"):
        ricerca = httpx.post(f"{app_url}/telescope-time/ricerche",
                             json={"nome": f"Gara {giorno} {ora}"}).json()
        ids.append(httpx.post(f"{app_url}/telescope-time/richieste", json={
            "ricerca_id": ricerca["id"],
            "inizio": f"{giorno}T{ora}",
            "fine": f"{giorno}T23:59:00",
        }).json()["id"])

    def approva(richiesta_id):
        return httpx.patch(f"{app_url}/telescope-time/richieste/{richiesta_id}",
                           json={"stato": "approvata"}, timeout=30).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        esiti = list(pool.map(approva, ids))

    approvate = [
        httpx.get(f"{app_url}/telescope-time/richieste/{i}").json()["stato"] for i in ids
    ].count("approvata")
    assert approvate == 1, f"due fasce sovrapposte approvate entrambe (esiti {esiti})"
