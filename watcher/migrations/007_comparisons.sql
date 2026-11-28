-- Gate C1: comparison sets and immutable snapshots.
CREATE TABLE comparisons (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 title TEXT NOT NULL,
 project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
 listing_ids TEXT NOT NULL,
 version INTEGER NOT NULL DEFAULT 1,
 created REAL NOT NULL,
 updated REAL NOT NULL
);
CREATE TABLE comparison_snapshots (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 comparison_id INTEGER NOT NULL REFERENCES comparisons(id) ON DELETE CASCADE,
 algorithm_version TEXT NOT NULL,
 data TEXT NOT NULL,
 created REAL NOT NULL
);
CREATE INDEX snapshot_comparison ON comparison_snapshots(comparison_id, id);
