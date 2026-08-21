-- Dati di esempio per lo sviluppo locale.
--
--   sqlite3 telescope_time.db < seed.sql
--
-- Richiede che l'app sia già stata avviata almeno una volta (init_db crea le
-- tabelle). Pensato per un database vuoto: rilanciandolo, `users` e
-- `research_programs` non vengono duplicati (INSERT OR IGNORE), ma le
-- `time_requests` sì.
--
-- Nomi, ricerche e username sono di fantasia: nessuno di questi osservatori
-- esiste davvero in Authelia. Le date sono relative a oggi, così il
-- calendario ha sempre qualcosa da mostrare nel mese corrente e nel
-- successivo senza dover aggiornare questo file.

INSERT OR IGNORE INTO users (username, name, email) VALUES
    ('gvernier',  'Giulia Vernier',  'giulia.vernier@example.test'),
    ('efabbri',   'Elena Fabbri',    'elena.fabbri@example.test'),
    ('dmanzoni',  'Davide Manzoni',  'davide.manzoni@example.test'),
    ('pranieri',  'Paolo Ranieri',   'paolo.ranieri@example.test'),
    ('cbellandi', 'Chiara Bellandi', 'chiara.bellandi@example.test'),
    ('socio-dev', 'Luca Bertani',    'socio-dev@example.test');

INSERT OR IGNORE INTO research_programs (name, description, specs) VALUES
    ('Survey exoplanet',        'Ricerca di transiti su nane rosse vicine', 'Filtri BVRI, pose da 120s'),
    ('Monitoraggio comete',     'Curve di luce di comete periodiche',       'Filtro R, binning 2x2'),
    ('Curve di luce asteroidi', 'Determinazione periodi di rotazione',      'Senza filtro, cadenza 60s');

-- Notte bloccata.
INSERT INTO time_requests (research_program_id, requester_id, co_observers, requested_night, start, end, status, reviewer_notes, updated_at)
SELECT id, (SELECT id FROM users WHERE name = 'Giulia Vernier'), 'Marco Silvestri',
       date('now', '+3 days'),
       date('now', '+3 days') || 'T22:00:00', date('now', '+4 days') || 'T01:00:00',
       'approved', 'Meteo previsto stabile.', strftime('%Y-%m-%dT%H:%M:%SZ','now')
FROM research_programs WHERE name = 'Survey exoplanet';

-- Notte contesa: due ricerche diverse chiedono fasce che si intersecano.
INSERT INTO time_requests (research_program_id, requester_id, co_observers, requested_night, start, end)
SELECT id, (SELECT id FROM users WHERE name = 'Elena Fabbri'), NULL,
       date('now', '+7 days'),
       date('now', '+7 days') || 'T21:00:00', date('now', '+8 days') || 'T00:30:00'
FROM research_programs WHERE name = 'Monitoraggio comete';

INSERT INTO time_requests (research_program_id, requester_id, co_observers, requested_night, start, end)
SELECT id, (SELECT id FROM users WHERE name = 'Davide Manzoni'), 'Sara Ferretti',
       date('now', '+7 days'),
       date('now', '+7 days') || 'T23:00:00', date('now', '+8 days') || 'T03:00:00'
FROM research_programs WHERE name = 'Curve di luce asteroidi';

-- Notte solo richiesta: turni distinti, non si sovrappongono.
INSERT INTO time_requests (research_program_id, requester_id, co_observers, requested_night, start, end)
SELECT id, (SELECT id FROM users WHERE name = 'Chiara Bellandi'), 'Luca Toselli',
       date('now', '+12 days'),
       date('now', '+12 days') || 'T20:30:00', date('now', '+12 days') || 'T23:00:00'
FROM research_programs WHERE name = 'Monitoraggio comete';

INSERT INTO time_requests (research_program_id, requester_id, co_observers, requested_night, start, end)
SELECT id, (SELECT id FROM users WHERE name = 'Elena Fabbri'), NULL,
       date('now', '+12 days'),
       date('now', '+12 days') || 'T23:00:00', date('now', '+13 days') || 'T02:00:00'
FROM research_programs WHERE name = 'Curve di luce asteroidi';

-- Richiesta rifiutata: non compare nel calendario e libera di nuovo la notte.
INSERT INTO time_requests (research_program_id, requester_id, co_observers, requested_night, start, end, status, reviewer_notes, updated_at)
SELECT id, (SELECT id FROM users WHERE name = 'Paolo Ranieri'), NULL,
       date('now', '+10 days'),
       date('now', '+10 days') || 'T22:00:00', date('now', '+11 days') || 'T02:00:00',
       'rejected', 'Strumentazione in manutenzione.', strftime('%Y-%m-%dT%H:%M:%SZ','now')
FROM research_programs WHERE name = 'Survey exoplanet';

-- Comincia dopo mezzanotte: notte precedente al giorno di `start`, il caso
-- che #47 corregge.
INSERT INTO time_requests (research_program_id, requester_id, co_observers, requested_night, start, end)
SELECT id, (SELECT id FROM users WHERE name = 'Paolo Ranieri'), NULL,
       date('now', '+8 days'),
       date('now', '+9 days') || 'T02:00:00', date('now', '+9 days') || 'T04:30:00'
FROM research_programs WHERE name = 'Curve di luce asteroidi';

-- ─── Mese successivo ───────────────────────────────────────────────────────

-- Notte bloccata.
INSERT INTO time_requests (research_program_id, requester_id, co_observers, requested_night, start, end, status, reviewer_notes, updated_at)
SELECT id, (SELECT id FROM users WHERE name = 'Paolo Ranieri'), NULL,
       date('now', '+15 days'),
       date('now', '+15 days') || 'T21:00:00', date('now', '+15 days') || 'T23:30:00',
       'approved', 'Confermato.', strftime('%Y-%m-%dT%H:%M:%SZ','now')
FROM research_programs WHERE name = 'Survey exoplanet';

-- Notte contesa.
INSERT INTO time_requests (research_program_id, requester_id, co_observers, requested_night, start, end)
SELECT id, (SELECT id FROM users WHERE name = 'Chiara Bellandi'), 'Luca Toselli',
       date('now', '+20 days'),
       date('now', '+20 days') || 'T22:00:00', date('now', '+21 days') || 'T00:30:00'
FROM research_programs WHERE name = 'Monitoraggio comete';

INSERT INTO time_requests (research_program_id, requester_id, co_observers, requested_night, start, end)
SELECT id, (SELECT id FROM users WHERE name = 'Davide Manzoni'), NULL,
       date('now', '+20 days'),
       date('now', '+20 days') || 'T23:00:00', date('now', '+21 days') || 'T01:30:00'
FROM research_programs WHERE name = 'Curve di luce asteroidi';

-- Richiesta rifiutata.
INSERT INTO time_requests (research_program_id, requester_id, co_observers, requested_night, start, end, status, reviewer_notes, updated_at)
SELECT id, (SELECT id FROM users WHERE name = 'Elena Fabbri'), NULL,
       date('now', '+30 days'),
       date('now', '+30 days') || 'T20:00:00', date('now', '+30 days') || 'T22:00:00',
       'rejected', 'Strumento non disponibile.', strftime('%Y-%m-%dT%H:%M:%SZ','now')
FROM research_programs WHERE name = 'Survey exoplanet';

-- Comincia dopo mezzanotte, come sopra ma più tardi.
INSERT INTO time_requests (research_program_id, requester_id, co_observers, requested_night, start, end, status, reviewer_notes, updated_at)
SELECT id, (SELECT id FROM users WHERE name = 'Chiara Bellandi'), 'Luca Toselli',
       date('now', '+25 days'),
       date('now', '+26 days') || 'T03:30:00', date('now', '+26 days') || 'T05:30:00',
       'approved', 'Ok.', strftime('%Y-%m-%dT%H:%M:%SZ','now')
FROM research_programs WHERE name = 'Survey exoplanet';

-- Comincia dopo mezzanotte: appartiene alla notte precedente, non a quella
-- del giorno del `start` — il caso che #47 corregge.
INSERT INTO time_requests (research_program_id, requester_id, co_observers, requested_night, start, end)
SELECT id, (SELECT id FROM users WHERE name = 'Giulia Vernier'), 'Marco Silvestri',
       date('now', '+35 days'),
       date('now', '+36 days') || 'T01:00:00', date('now', '+36 days') || 'T03:30:00'
FROM research_programs WHERE name = 'Curve di luce asteroidi';

-- Notte bloccata, fine mese.
INSERT INTO time_requests (research_program_id, requester_id, co_observers, requested_night, start, end, status, reviewer_notes, updated_at)
SELECT id, (SELECT id FROM users WHERE name = 'Davide Manzoni'), NULL,
       date('now', '+40 days'),
       date('now', '+40 days') || 'T22:30:00', date('now', '+41 days') || 'T01:00:00',
       'approved', 'Ok.', strftime('%Y-%m-%dT%H:%M:%SZ','now')
FROM research_programs WHERE name = 'Monitoraggio comete';

-- Notte solo richiesta, ultimo giorno del mese.
INSERT INTO time_requests (research_program_id, requester_id, co_observers, requested_night, start, end)
SELECT id, (SELECT id FROM users WHERE name = 'Chiara Bellandi'), 'Sara Ferretti',
       date('now', '+42 days'),
       date('now', '+42 days') || 'T21:30:00', date('now', '+42 days') || 'T23:00:00'
FROM research_programs WHERE name = 'Survey exoplanet';

-- Del socio sintetizzato dallo switcher dev: sempre in attesa, per provare
-- da subito se il proprietario può modificarla lui stesso.
INSERT INTO time_requests (research_program_id, requester_id, co_observers, requested_night, start, end)
SELECT id, (SELECT id FROM users WHERE name = 'Luca Bertani'), NULL,
       date('now', '+18 days'),
       date('now', '+18 days') || 'T21:00:00', date('now', '+18 days') || 'T23:00:00'
FROM research_programs WHERE name = 'Survey exoplanet';
