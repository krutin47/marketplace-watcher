CREATE TABLE fixture_feed (id INTEGER PRIMARY KEY CHECK(id=1), stage INTEGER NOT NULL);
INSERT INTO fixture_feed SELECT 1, COALESCE(MAX(fixture_step),0) FROM watchlists;
