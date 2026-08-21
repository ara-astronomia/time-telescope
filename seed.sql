-- Sample data for local development.
--
--   sqlite3 telescope_time.db < seed.sql
--
-- Requires the app to have started at least once already (init_db creates
-- the tables). Meant for an empty database: rerunning it, `users` and
-- `research_programs` don't get duplicated (INSERT OR IGNORE), but
-- `time_requests` do.
--
-- Names, research programs and usernames are made up: none of these
-- observers really exist in Authelia. Dates are relative to today, so the
-- calendar always has something to show in the current month and the next
-- one without needing to update this file.

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

-- Booked night.
INSERT INTO time_requests (research_program_id, requester_id, co_observers, requested_night, start, end, status, reviewer_notes, updated_at)
SELECT id, (SELECT id FROM users WHERE name = 'Giulia Vernier'), 'Marco Silvestri',
       date('now', '+3 days'),
       date('now', '+3 days') || 'T22:00:00', date('now', '+4 days') || 'T01:00:00',
       'approved', 'Meteo previsto stabile.', strftime('%Y-%m-%dT%H:%M:%SZ','now')
FROM research_programs WHERE name = 'Survey exoplanet';

-- Contested night: two different research programs request overlapping slots.
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

-- Pending-only night: distinct shifts, no overlap.
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

-- Rejected request: doesn't show up on the calendar and frees the night again.
INSERT INTO time_requests (research_program_id, requester_id, co_observers, requested_night, start, end, status, reviewer_notes, updated_at)
SELECT id, (SELECT id FROM users WHERE name = 'Paolo Ranieri'), NULL,
       date('now', '+10 days'),
       date('now', '+10 days') || 'T22:00:00', date('now', '+11 days') || 'T02:00:00',
       'rejected', 'Strumentazione in manutenzione.', strftime('%Y-%m-%dT%H:%M:%SZ','now')
FROM research_programs WHERE name = 'Survey exoplanet';

-- Starts after midnight: the previous night to the day of `start`, the case
-- #47 fixes.
INSERT INTO time_requests (research_program_id, requester_id, co_observers, requested_night, start, end)
SELECT id, (SELECT id FROM users WHERE name = 'Paolo Ranieri'), NULL,
       date('now', '+8 days'),
       date('now', '+9 days') || 'T02:00:00', date('now', '+9 days') || 'T04:30:00'
FROM research_programs WHERE name = 'Curve di luce asteroidi';

-- ─── Next month ────────────────────────────────────────────────────────────

-- Booked night.
INSERT INTO time_requests (research_program_id, requester_id, co_observers, requested_night, start, end, status, reviewer_notes, updated_at)
SELECT id, (SELECT id FROM users WHERE name = 'Paolo Ranieri'), NULL,
       date('now', '+15 days'),
       date('now', '+15 days') || 'T21:00:00', date('now', '+15 days') || 'T23:30:00',
       'approved', 'Confermato.', strftime('%Y-%m-%dT%H:%M:%SZ','now')
FROM research_programs WHERE name = 'Survey exoplanet';

-- Contested night.
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

-- Rejected request.
INSERT INTO time_requests (research_program_id, requester_id, co_observers, requested_night, start, end, status, reviewer_notes, updated_at)
SELECT id, (SELECT id FROM users WHERE name = 'Elena Fabbri'), NULL,
       date('now', '+30 days'),
       date('now', '+30 days') || 'T20:00:00', date('now', '+30 days') || 'T22:00:00',
       'rejected', 'Strumento non disponibile.', strftime('%Y-%m-%dT%H:%M:%SZ','now')
FROM research_programs WHERE name = 'Survey exoplanet';

-- Starts after midnight, like above but later.
INSERT INTO time_requests (research_program_id, requester_id, co_observers, requested_night, start, end, status, reviewer_notes, updated_at)
SELECT id, (SELECT id FROM users WHERE name = 'Chiara Bellandi'), 'Luca Toselli',
       date('now', '+25 days'),
       date('now', '+26 days') || 'T03:30:00', date('now', '+26 days') || 'T05:30:00',
       'approved', 'Ok.', strftime('%Y-%m-%dT%H:%M:%SZ','now')
FROM research_programs WHERE name = 'Survey exoplanet';

-- Starts after midnight: belongs to the previous night, not the one for the
-- day of `start` — the case #47 fixes.
INSERT INTO time_requests (research_program_id, requester_id, co_observers, requested_night, start, end)
SELECT id, (SELECT id FROM users WHERE name = 'Giulia Vernier'), 'Marco Silvestri',
       date('now', '+35 days'),
       date('now', '+36 days') || 'T01:00:00', date('now', '+36 days') || 'T03:30:00'
FROM research_programs WHERE name = 'Curve di luce asteroidi';

-- Booked night, end of month.
INSERT INTO time_requests (research_program_id, requester_id, co_observers, requested_night, start, end, status, reviewer_notes, updated_at)
SELECT id, (SELECT id FROM users WHERE name = 'Davide Manzoni'), NULL,
       date('now', '+40 days'),
       date('now', '+40 days') || 'T22:30:00', date('now', '+41 days') || 'T01:00:00',
       'approved', 'Ok.', strftime('%Y-%m-%dT%H:%M:%SZ','now')
FROM research_programs WHERE name = 'Monitoraggio comete';

-- Pending-only night, last day of the month.
INSERT INTO time_requests (research_program_id, requester_id, co_observers, requested_night, start, end)
SELECT id, (SELECT id FROM users WHERE name = 'Chiara Bellandi'), 'Sara Ferretti',
       date('now', '+42 days'),
       date('now', '+42 days') || 'T21:30:00', date('now', '+42 days') || 'T23:00:00'
FROM research_programs WHERE name = 'Survey exoplanet';

-- Belongs to the member synthesized by the dev switcher: always pending, to
-- immediately test whether the owner can edit it themselves.
INSERT INTO time_requests (research_program_id, requester_id, co_observers, requested_night, start, end)
SELECT id, (SELECT id FROM users WHERE name = 'Luca Bertani'), NULL,
       date('now', '+18 days'),
       date('now', '+18 days') || 'T21:00:00', date('now', '+18 days') || 'T23:00:00'
FROM research_programs WHERE name = 'Survey exoplanet';
