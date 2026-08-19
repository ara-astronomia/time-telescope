# Piano — #47 Una sessione che comincia dopo la mezzanotte finisce nella notte sbagliata

## Cosa dice l'issue

`FasciaOraria.notte` (router.py:336-338) usa `self.inizio.date()` senza soglia:
una sessione che comincia all'01:00 finisce nella notte del giorno stesso
invece che in quella precedente, rompendo sia il raggruppamento del
calendario sia il vincolo applicativo "una richiesta per ricerca per notte".

Decisioni prese con l'utente (l'issue le lasciava aperte):
- **Soglia: mezzogiorno** (proposta dell'issue, convenzione astronomica).
- **UI hint incluso in questa storia**: riga "notte del 25 agosto" nel
  modulo di richiesta, sotto i campi di inizio/fine.

## Ambiente

Worktree `../time-telescope-47-notte-dopo-mezzanotte`, branch
`47-notte-dopo-mezzanotte`, da `main` aggiornato (9758677, dopo il merge di
#45/PR #48). `uv sync` eseguito, baseline verde: `tests/test_fasce_orarie.py`,
`tests/test_spostamenti.py`, `tests/test_calendario.py` → 61 passed. Nessuna
modifica a Docker/env richiesta per questa storia.

## Impatto nel codice

- `FasciaOraria.notte` (router.py:336-338) — punto unico, un solo file.
- Eredita automaticamente: `POST /richieste` (righe 646/657, usa `body.notte`)
  e `PATCH /orario` di #34 (riga 738, usa già `body.notte`).
- Nessuna migrazione: DB di sviluppo si azzera, come già fatto per #33.
- `router.py` non importa `time` né `timedelta` da `datetime` — vanno
  aggiunti all'import esistente (riga 12).
- Frontend: `static/telescope_time_request.html`, sezione "03 Sessione
  Richiesta" — nuova riga sotto i campi `inizio`/`fine`, calcolata lato
  client con la stessa soglia (mezzogiorno) sull'evento `input` già presente
  su `campoInizio`.

## Test

Suite automatica esistente (pytest), nessuna novità nella toolchain:
- `tests/test_fasce_orarie.py`: nuovo test per sessione che comincia
  all'01:00 → `giorno_richiesto` è il giorno precedente. Il test esistente
  `test_la_notte_di_riferimento_e_quella_di_inizio` (ora=23) resta verde
  invariato (23:00 ≥ soglia).
- `tests/test_spostamenti.py`: stesso caso sull'endpoint `/orario` di #34,
  visto che eredita `body.notte`.
- UI hint: verifica manuale (nessun test Playwright esistente copre
  quel modulo per questo dettaglio; non introduco una suite nuova per una
  riga di testo derivato).

## Ordine di lavoro

TDD rosso→verde su `FasciaOraria.notte` e sui due endpoint che lo ereditano,
poi l'hint UI, poi push e PR con `Closes #47` in prima riga.
