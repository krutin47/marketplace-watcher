-- Gate B1: listing evidence and listing-scoped assistant conversations.
CREATE TABLE evidence (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
 kind TEXT NOT NULL CHECK(kind IN ('seller_claim','user_note')),
 origin TEXT NOT NULL,
 content TEXT NOT NULL,
 content_hash TEXT NOT NULL,
 received_at REAL,
 created REAL NOT NULL,
 supersedes_id INTEGER REFERENCES evidence(id)
);
CREATE INDEX evidence_listing ON evidence(listing_id, created);

CREATE TABLE conversations (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 scope TEXT NOT NULL CHECK(scope IN ('listing')),
 listing_id INTEGER REFERENCES listings(id) ON DELETE CASCADE,
 title TEXT NOT NULL,
 archived INTEGER NOT NULL DEFAULT 0,
 excluded TEXT NOT NULL DEFAULT '[]',
 version INTEGER NOT NULL DEFAULT 1,
 created REAL NOT NULL,
 updated REAL NOT NULL
);
CREATE INDEX conversation_listing ON conversations(listing_id, archived, updated);

CREATE TABLE assistant_runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
 user_message_id INTEGER NOT NULL,
 request_key TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed','cancelled','interrupted')),
 provider TEXT NOT NULL,
 model TEXT NOT NULL,
 prompt_version TEXT NOT NULL,
 input_tokens INTEGER,
 output_tokens INTEGER,
 estimated_cost_micros INTEGER,
 error_code TEXT,
 cancel_requested INTEGER NOT NULL DEFAULT 0,
 created REAL NOT NULL,
 started REAL,
 finished REAL,
 UNIQUE(conversation_id, request_key)
);
CREATE INDEX run_status ON assistant_runs(status, created);

CREATE TABLE chat_messages (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
 seq INTEGER NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('user','assistant')),
 content TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('complete','partial','failed','cancelled')),
 run_id INTEGER REFERENCES assistant_runs(id) ON DELETE SET NULL,
 listing_id INTEGER,
 created REAL NOT NULL,
 UNIQUE(conversation_id, seq)
);

CREATE TABLE context_snapshots (
 run_id INTEGER PRIMARY KEY REFERENCES assistant_runs(id) ON DELETE CASCADE,
 data TEXT NOT NULL
);

CREATE TABLE run_events (
 run_id INTEGER NOT NULL REFERENCES assistant_runs(id) ON DELETE CASCADE,
 seq INTEGER NOT NULL,
 type TEXT NOT NULL CHECK(type IN ('started','delta','evidence','completed','failed','cancelled')),
 data TEXT NOT NULL,
 PRIMARY KEY(run_id, seq)
);
