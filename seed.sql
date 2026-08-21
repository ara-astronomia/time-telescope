-- Dati di esempio per lo sviluppo locale.
--
--   sqlite3 telescope_time.db < seed.sql
--
-- Richiede che l'app sia già stata avviata almeno una volta (init_db crea le
-- tabelle). Pensato per un database vuoto: rilanciandolo, `utenti` e
-- `ricerche` non vengono duplicati (INSERT OR IGNORE), ma le `richieste` sì.
--
-- Nomi, ricerche e username sono di fantasia: nessuno di questi osservatori
-- esiste davvero in Authelia. Le date sono relative a oggi, così il
-- calendario ha sempre qualcosa da mostrare nel mese corrente e nel
-- successivo senza dover aggiornare questo file.

INSERT OR IGNORE INTO utenti (username, nome, email) VALUES
    ('gvernier',  'Giulia Vernier',  'giulia.vernier@example.test'),
    ('efabbri',   'Elena Fabbri',    'elena.fabbri@example.test'),
    ('dmanzoni',  'Davide Manzoni',  'davide.manzoni@example.test'),
    ('pranieri',  'Paolo Ranieri',   'paolo.ranieri@example.test'),
    ('cbellandi', 'Chiara Bellandi', 'chiara.bellandi@example.test'),
    ('socio-dev', 'Luca Bertani',    'socio-dev@example.test');

INSERT OR IGNORE INTO ricerche (nome, descrizione, specifiche) VALUES
    ('Survey exoplanet',        'Ricerca di transiti su nane rosse vicine', 'Filtri BVRI, pose da 120s'),
    ('Monitoraggio comete',     'Curve di luce di comete periodiche',       'Filtro R, binning 2x2'),
    ('Curve di luce asteroidi', 'Determinazione periodi di rotazione',      'Senza filtro, cadenza 60s');

-- Notte bloccata.
INSERT INTO richieste (ricerca_id, richiedente_id, co_osservatori, giorno_richiesto, inizio, fine, stato, note_responsabile, aggiornata_il)
SELECT id, (SELECT id FROM utenti WHERE nome = 'Giulia Vernier'), 'Marco Silvestri',
       date('now', '+3 days'),
       date('now', '+3 days') || 'T22:00:00', date('now', '+4 days') || 'T01:00:00',
       'approvata', 'Meteo previsto stabile.', strftime('%Y-%m-%dT%H:%M:%SZ','now')
FROM ricerche WHERE nome = 'Survey exoplanet';

-- Notte contesa: due ricerche diverse chiedono fasce che si intersecano.
INSERT INTO richieste (ricerca_id, richiedente_id, co_osservatori, giorno_richiesto, inizio, fine)
SELECT id, (SELECT id FROM utenti WHERE nome = 'Elena Fabbri'), NULL,
       date('now', '+7 days'),
       date('now', '+7 days') || 'T21:00:00', date('now', '+8 days') || 'T00:30:00'
FROM ricerche WHERE nome = 'Monitoraggio comete';

INSERT INTO richieste (ricerca_id, richiedente_id, co_osservatori, giorno_richiesto, inizio, fine)
SELECT id, (SELECT id FROM utenti WHERE nome = 'Davide Manzoni'), 'Sara Ferretti',
       date('now', '+7 days'),
       date('now', '+7 days') || 'T23:00:00', date('now', '+8 days') || 'T03:00:00'
FROM ricerche WHERE nome = 'Curve di luce asteroidi';

-- Notte solo richiesta: turni distinti, non si sovrappongono.
INSERT INTO richieste (ricerca_id, richiedente_id, co_osservatori, giorno_richiesto, inizio, fine)
SELECT id, (SELECT id FROM utenti WHERE nome = 'Chiara Bellandi'), 'Luca Toselli',
       date('now', '+12 days'),
       date('now', '+12 days') || 'T20:30:00', date('now', '+12 days') || 'T23:00:00'
FROM ricerche WHERE nome = 'Monitoraggio comete';

INSERT INTO richieste (ricerca_id, richiedente_id, co_osservatori, giorno_richiesto, inizio, fine)
SELECT id, (SELECT id FROM utenti WHERE nome = 'Elena Fabbri'), NULL,
       date('now', '+12 days'),
       date('now', '+12 days') || 'T23:00:00', date('now', '+13 days') || 'T02:00:00'
FROM ricerche WHERE nome = 'Curve di luce asteroidi';

-- Richiesta rifiutata: non compare nel calendario e libera di nuovo la notte.
INSERT INTO richieste (ricerca_id, richiedente_id, co_osservatori, giorno_richiesto, inizio, fine, stato, note_responsabile, aggiornata_il)
SELECT id, (SELECT id FROM utenti WHERE nome = 'Paolo Ranieri'), NULL,
       date('now', '+10 days'),
       date('now', '+10 days') || 'T22:00:00', date('now', '+11 days') || 'T02:00:00',
       'rifiutata', 'Strumentazione in manutenzione.', strftime('%Y-%m-%dT%H:%M:%SZ','now')
FROM ricerche WHERE nome = 'Survey exoplanet';

-- Comincia dopo mezzanotte: notte precedente al giorno di `inizio`, il caso
-- che #47 corregge.
INSERT INTO richieste (ricerca_id, richiedente_id, co_osservatori, giorno_richiesto, inizio, fine)
SELECT id, (SELECT id FROM utenti WHERE nome = 'Paolo Ranieri'), NULL,
       date('now', '+8 days'),
       date('now', '+9 days') || 'T02:00:00', date('now', '+9 days') || 'T04:30:00'
FROM ricerche WHERE nome = 'Curve di luce asteroidi';

-- ─── Mese successivo ───────────────────────────────────────────────────────

-- Notte bloccata.
INSERT INTO richieste (ricerca_id, richiedente_id, co_osservatori, giorno_richiesto, inizio, fine, stato, note_responsabile, aggiornata_il)
SELECT id, (SELECT id FROM utenti WHERE nome = 'Paolo Ranieri'), NULL,
       date('now', '+15 days'),
       date('now', '+15 days') || 'T21:00:00', date('now', '+15 days') || 'T23:30:00',
       'approvata', 'Confermato.', strftime('%Y-%m-%dT%H:%M:%SZ','now')
FROM ricerche WHERE nome = 'Survey exoplanet';

-- Notte contesa.
INSERT INTO richieste (ricerca_id, richiedente_id, co_osservatori, giorno_richiesto, inizio, fine)
SELECT id, (SELECT id FROM utenti WHERE nome = 'Chiara Bellandi'), 'Luca Toselli',
       date('now', '+20 days'),
       date('now', '+20 days') || 'T22:00:00', date('now', '+21 days') || 'T00:30:00'
FROM ricerche WHERE nome = 'Monitoraggio comete';

INSERT INTO richieste (ricerca_id, richiedente_id, co_osservatori, giorno_richiesto, inizio, fine)
SELECT id, (SELECT id FROM utenti WHERE nome = 'Davide Manzoni'), NULL,
       date('now', '+20 days'),
       date('now', '+20 days') || 'T23:00:00', date('now', '+21 days') || 'T01:30:00'
FROM ricerche WHERE nome = 'Curve di luce asteroidi';

-- Richiesta rifiutata.
INSERT INTO richieste (ricerca_id, richiedente_id, co_osservatori, giorno_richiesto, inizio, fine, stato, note_responsabile, aggiornata_il)
SELECT id, (SELECT id FROM utenti WHERE nome = 'Elena Fabbri'), NULL,
       date('now', '+30 days'),
       date('now', '+30 days') || 'T20:00:00', date('now', '+30 days') || 'T22:00:00',
       'rifiutata', 'Strumento non disponibile.', strftime('%Y-%m-%dT%H:%M:%SZ','now')
FROM ricerche WHERE nome = 'Survey exoplanet';

-- Comincia dopo mezzanotte, come sopra ma più tardi.
INSERT INTO richieste (ricerca_id, richiedente_id, co_osservatori, giorno_richiesto, inizio, fine, stato, note_responsabile, aggiornata_il)
SELECT id, (SELECT id FROM utenti WHERE nome = 'Chiara Bellandi'), 'Luca Toselli',
       date('now', '+25 days'),
       date('now', '+26 days') || 'T03:30:00', date('now', '+26 days') || 'T05:30:00',
       'approvata', 'Ok.', strftime('%Y-%m-%dT%H:%M:%SZ','now')
FROM ricerche WHERE nome = 'Survey exoplanet';

-- Comincia dopo mezzanotte: appartiene alla notte precedente, non a quella
-- del giorno del `inizio` — il caso che #47 corregge.
INSERT INTO richieste (ricerca_id, richiedente_id, co_osservatori, giorno_richiesto, inizio, fine)
SELECT id, (SELECT id FROM utenti WHERE nome = 'Giulia Vernier'), 'Marco Silvestri',
       date('now', '+35 days'),
       date('now', '+36 days') || 'T01:00:00', date('now', '+36 days') || 'T03:30:00'
FROM ricerche WHERE nome = 'Curve di luce asteroidi';

-- Notte bloccata, fine mese.
INSERT INTO richieste (ricerca_id, richiedente_id, co_osservatori, giorno_richiesto, inizio, fine, stato, note_responsabile, aggiornata_il)
SELECT id, (SELECT id FROM utenti WHERE nome = 'Davide Manzoni'), NULL,
       date('now', '+40 days'),
       date('now', '+40 days') || 'T22:30:00', date('now', '+41 days') || 'T01:00:00',
       'approvata', 'Ok.', strftime('%Y-%m-%dT%H:%M:%SZ','now')
FROM ricerche WHERE nome = 'Monitoraggio comete';

-- Notte solo richiesta, ultimo giorno del mese.
INSERT INTO richieste (ricerca_id, richiedente_id, co_osservatori, giorno_richiesto, inizio, fine)
SELECT id, (SELECT id FROM utenti WHERE nome = 'Chiara Bellandi'), 'Sara Ferretti',
       date('now', '+42 days'),
       date('now', '+42 days') || 'T21:30:00', date('now', '+42 days') || 'T23:00:00'
FROM ricerche WHERE nome = 'Survey exoplanet';

-- Del socio sintetizzato dallo switcher dev: sempre in attesa, per provare
-- da subito se il proprietario può modificarla lui stesso.
INSERT INTO richieste (ricerca_id, richiedente_id, co_osservatori, giorno_richiesto, inizio, fine)
SELECT id, (SELECT id FROM utenti WHERE nome = 'Luca Bertani'), NULL,
       date('now', '+18 days'),
       date('now', '+18 days') || 'T21:00:00', date('now', '+18 days') || 'T23:00:00'
FROM ricerche WHERE nome = 'Survey exoplanet';
