ALTER TABLE runs ADD COLUMN result_count INTEGER;

CREATE TABLE run_listings (
 run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
 listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
 PRIMARY KEY (run_id, listing_id)
);
