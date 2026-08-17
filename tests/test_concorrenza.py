"""Due richieste HTTP simultanee: la dashboard le fa sempre (Promise.all su
/richieste e /ricerche), quindi non è un caso di laboratorio."""

from concurrent.futures import ThreadPoolExecutor

import httpx2 as httpx


def test_chiamate_simultanee_non_falliscono(app_url):
    def chiama(percorso):
        return httpx.get(f"{app_url}{percorso}", timeout=10).status_code

    percorsi = ["/telescope-time/richieste", "/telescope-time/ricerche"] * 8
    with ThreadPoolExecutor(max_workers=8) as pool:
        esiti = list(pool.map(chiama, percorsi))

    assert set(esiti) == {200}, f"esiti inattesi: {sorted(set(esiti))}"
