CREATE TABLE watchlists (
 id INTEGER PRIMARY KEY AUTOINCREMENT, config TEXT NOT NULL, enabled INTEGER NOT NULL,
 due REAL, queued INTEGER NOT NULL DEFAULT 0, last_attempt REAL, last_success REAL, last_error TEXT
);
CREATE INDEX watch_due ON watchlists(enabled,due);
CREATE TABLE listings (
 id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, external_id TEXT NOT NULL,
 data TEXT NOT NULL, first_seen REAL NOT NULL, last_seen REAL NOT NULL,
 state TEXT NOT NULL DEFAULT 'new', draft TEXT, UNIQUE(source, external_id)
);
CREATE TABLE observations (
 id INTEGER PRIMARY KEY AUTOINCREMENT, listing_id INTEGER NOT NULL REFERENCES listings(id),
 observed REAL NOT NULL, data TEXT NOT NULL
);
CREATE INDEX observation_listing ON observations(listing_id, observed);
CREATE TABLE matches (
 watchlist_id INTEGER NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE,
 listing_id INTEGER NOT NULL REFERENCES listings(id), status TEXT NOT NULL,
 result TEXT NOT NULL, PRIMARY KEY(watchlist_id,listing_id)
);
CREATE INDEX match_status ON matches(status,listing_id);
CREATE TABLE runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, watchlist_id INTEGER REFERENCES watchlists(id) ON DELETE SET NULL,
 name TEXT NOT NULL, source TEXT NOT NULL, started REAL NOT NULL, finished REAL, status TEXT NOT NULL, error TEXT
);
CREATE TABLE source_state (source TEXT PRIMARY KEY, next_allowed REAL NOT NULL DEFAULT 0, paused INTEGER NOT NULL DEFAULT 0);
CREATE TABLE inbox (event_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL);
CREATE TABLE outbox (
 id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT UNIQUE NOT NULL,
 payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
 attempts INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL, error TEXT
);
CREATE INDEX outbox_due ON outbox(status,due);
CREATE TABLE settings (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
INSERT INTO settings VALUES(1,'{"muted":false,"minimum_score":0,"minimum_priority":0}');
