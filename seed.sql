-- Dati di esempio per lo sviluppo locale.
--
--   sqlite3 telescope_time.db < seed.sql
--
-- Le tabelle vengono create dall'app al primo avvio (init_db), quindi
-- lanciare prima l'app almeno una volta. Pensato per un database vuoto:
-- rilanciandolo, le ricerche non vengono duplicate ma le richieste sì.
--
-- Nomi e ricerche sono di fantasia.
--
-- Le date sono relative a oggi, così il calendario ha sempre qualcosa da
-- mostrare nel mese corrente senza dover aggiornare questo file.

-- Osservatori di esempio. Chi passa da Authelia viene registrato al primo
-- accesso; questi servono a popolare le richieste del seed, e hanno username
-- fittizi perché nessuno di loro esiste in Authelia.
INSERT OR IGNORE INTO utenti (username, nome, email) VALUES
    ('gvernier',  'Giulia Vernier',  'giulia.vernier@example.test'),
    ('efabbri',   'Elena Fabbri',    'elena.fabbri@example.test'),
    ('dmanzoni',  'Davide Manzoni',  'davide.manzoni@example.test'),
    ('pranieri',  'Paolo Ranieri',   'paolo.ranieri@example.test'),
    ('cbellandi', 'Chiara Bellandi', 'chiara.bellandi@example.test');

INSERT OR IGNORE INTO ricerche (nome, descrizione, specifiche) VALUES
    ('Survey exoplanet',        'Ricerca di transiti su nane rosse vicine', 'Filtri BVRI, pose da 120s'),
    ('Monitoraggio comete',     'Curve di luce di comete periodiche',       'Filtro R, binning 2x2'),
    ('Curve di luce asteroidi', 'Determinazione periodi di rotazione',      'Senza filtro, cadenza 60s');

-- Giorno bloccato: una richiesta approvata.
INSERT INTO richieste (ricerca_id, richiedente_id, co_osservatori, giorno_richiesto, stato, note_responsabile, aggiornata_il)
SELECT id, (SELECT id FROM utenti WHERE nome = 'Giulia Vernier'), 'Marco Silvestri', date('now', '+3 days'), 'approvata', 'Meteo previsto stabile.', strftime('%Y-%m-%dT%H:%M:%SZ','now')
FROM ricerche WHERE nome = 'Survey exoplanet';

-- Giorno conteso: due ricerche diverse chiedono la stessa data, entrambe in attesa.
INSERT INTO richieste (ricerca_id, richiedente_id, co_osservatori, giorno_richiesto)
SELECT id, (SELECT id FROM utenti WHERE nome = 'Elena Fabbri'), NULL, date('now', '+7 days')
FROM ricerche WHERE nome = 'Monitoraggio comete';

INSERT INTO richieste (ricerca_id, richiedente_id, co_osservatori, giorno_richiesto)
SELECT id, (SELECT id FROM utenti WHERE nome = 'Davide Manzoni'), 'Sara Ferretti', date('now', '+7 days')
FROM ricerche WHERE nome = 'Curve di luce asteroidi';

-- Richiesta rifiutata: non compare nel calendario e libera di nuovo la data.
INSERT INTO richieste (ricerca_id, richiedente_id, co_osservatori, giorno_richiesto, stato, note_responsabile, aggiornata_il)
SELECT id, (SELECT id FROM utenti WHERE nome = 'Paolo Ranieri'), NULL, date('now', '+10 days'), 'rifiutata', 'Strumentazione in manutenzione.', strftime('%Y-%m-%dT%H:%M:%SZ','now')
FROM ricerche WHERE nome = 'Survey exoplanet';

-- Richiesta semplice in attesa, più avanti nel mese.
INSERT INTO richieste (ricerca_id, richiedente_id, co_osservatori, giorno_richiesto)
SELECT id, (SELECT id FROM utenti WHERE nome = 'Chiara Bellandi'), 'Luca Toselli', date('now', '+15 days')
FROM ricerche WHERE nome = 'Monitoraggio comete';
