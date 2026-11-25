"""SQLite migrations and transactional listing, match, and outbox writes."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode
import sqlite3

from .core import ListingCandidate, Watchlist, match, seller_draft


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def utc(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc)


class Store:
    def __init__(self, path, dashboard_url=None):
        self.path = Path(path)
        self.dashboard_url = dashboard_url

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db
        finally:
            db.close()

    def migrate(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)")
            for migration in sorted(Path(__file__).with_name("migrations").glob("*.sql")):
                version = int(migration.name.split("_")[0])
                if not db.execute("SELECT 1 FROM schema_version WHERE version=?", (version,)).fetchone():
                    db.executescript(f"BEGIN IMMEDIATE;\n{migration.read_text()}\n"
                                     f"INSERT INTO schema_version VALUES ({version}); COMMIT;")

    def recover(self, now):
        with self.connect() as db:
            db.execute("UPDATE runs SET status='interrupted',finished=?,error='Service restarted' WHERE status='running'", (now,))
            ids = db.execute("SELECT id FROM watchlists WHERE due < ? ORDER BY due,id", (now,)).fetchall()
            for index, row in enumerate(ids):
                db.execute("UPDATE watchlists SET due=? WHERE id=?", (now + index * 5, row["id"]))

    def watchlists(self, db):
        return [self.watch(row) for row in db.execute("SELECT * FROM watchlists ORDER BY id")]

    @staticmethod
    def watch(row):
        data = dict(row)
        data.update(json.loads(data.pop("config")))
        return data

    def save_watch(self, db, config, now, watch_id=None):
        due = now if config.enabled and "fixture" in config.sources else None
        args = (config.model_dump_json(), config.enabled, due)
        if watch_id is None:
            watch_id = db.execute("INSERT INTO watchlists(config,enabled,due) VALUES(?,?,?)", args).lastrowid
        else:
            db.execute("UPDATE watchlists SET config=?,enabled=?,due=?,queued=0 WHERE id=?", (*args, watch_id))
            candidates = db.execute("SELECT * FROM listings").fetchall()
            db.execute("DELETE FROM matches WHERE watchlist_id=?", (watch_id,))
            for row in candidates:
                data = json.loads(row["data"])
                if data["source"] in config.sources and config.enabled:
                    self.set_match(db, watch_id, row["id"], match(config, ListingCandidate(**data), utc(now)))
        return self.watch(db.execute("SELECT * FROM watchlists WHERE id=?", (watch_id,)).fetchone())

    @staticmethod
    def set_match(db, watch_id, listing_id, result):
        if result["status"] == "rejected":
            db.execute("DELETE FROM matches WHERE watchlist_id=? AND listing_id=?", (watch_id, listing_id))
            return
        db.execute("""INSERT INTO matches VALUES(?,?,?,?) ON CONFLICT(watchlist_id,listing_id)
                    DO UPDATE SET status=excluded.status,result=excluded.result""",
                   (watch_id, listing_id, result["status"], encode(result)))

    def ingest(self, db, candidates, now, *, baseline_id=None, event_id=None, event_observed_at=None):
        if event_id:
            event = [c.model_dump(mode="json", exclude={"observed_at"}) for c in candidates]
            fingerprint = hashlib.sha256(encode([event, event_observed_at]).encode()).hexdigest()
            prior = db.execute("SELECT fingerprint FROM inbox WHERE event_id=?", (event_id,)).fetchone()
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise ValueError("Event ID is already associated with different data")
                return {"duplicate": True, "listing_ids": []}
            db.execute("INSERT INTO inbox VALUES (?,?)", (event_id, fingerprint))

        settings = self.settings(db)
        watches = [(row["id"], Watchlist(**json.loads(row["config"])))
                   for row in db.execute("SELECT id,config FROM watchlists WHERE enabled=1")]
        initialized = {row[0] for row in db.execute("SELECT watchlist_id FROM baselines")}
        digest = []
        ids = []
        for candidate in candidates:
            data = candidate.model_dump(mode="json")
            content = encode(candidate.model_dump(mode="json", exclude={"observed_at"}))
            identity = candidate.external_id or candidate.url
            observed = candidate.observed_at.timestamp()
            prior = db.execute("SELECT * FROM listings WHERE source=? AND external_id=?",
                               (candidate.source, identity)).fetchone()
            previous = json.loads(prior["data"]) if prior else None
            if prior:
                listing_id = prior["id"]
            else:
                listing_id = db.execute(
                    "INSERT INTO listings(source,external_id,data,first_seen,last_seen) VALUES(?,?,?,?,?)",
                    (candidate.source, identity, encode(data), observed, observed)).lastrowid
            if listing_id not in ids:
                ids.append(listing_id)
            stale = prior and observed < prior["last_seen"]
            previous_content = encode({k: v for k, v in previous.items() if k != "observed_at"}) if previous else None
            changed = previous_content != content
            revision = None
            if changed:
                revision = db.execute("""INSERT INTO observations(listing_id,observed,data)
                    SELECT ?,?,? WHERE NOT EXISTS (
                        SELECT 1 FROM observations WHERE listing_id=? AND observed=? AND data=?
                    )""", (listing_id, observed, content, listing_id, observed, content))
                revision = revision.lastrowid if revision.rowcount else None
            if stale:
                db.execute("UPDATE listings SET first_seen=min(first_seen,?) WHERE id=?", (observed, listing_id))
                continue
            db.execute("UPDATE listings SET data=?,last_seen=? WHERE id=?", (encode(data), observed, listing_id))
            prior_matches = {row["watchlist_id"] for row in db.execute(
                "SELECT watchlist_id FROM matches WHERE listing_id=? AND status='confirmed'", (listing_id,))}
            confirmed, qualifying_new, qualifying_drop = [], [], []
            silent_baseline = False
            for watch_id, watch in watches:
                if candidate.source not in watch.sources:
                    continue
                result = match(watch, candidate, utc(now))
                self.set_match(db, watch_id, listing_id, result)
                if result["status"] != "confirmed":
                    continue
                confirmed.append((watch, result))
                eligible = result["score"] >= settings["minimum_score"] and watch.priority >= settings["minimum_priority"]
                if not eligible:
                    continue
                baselining = candidate.source == "fixture" and watch_id not in initialized
                if baselining and watch_id == baseline_id and watch.initial == "silent" and watch.notify_new:
                    silent_baseline = True
                if baselining and watch_id == baseline_id and watch.initial == "digest" and watch.notify_new:
                    if not db.execute("SELECT 1 FROM outbox WHERE event_key=?", (f"new:{listing_id}",)).fetchone():
                        digest.append((listing_id, candidate, watch))
                if watch.notify_new and (
                    baselining and watch_id == baseline_id and watch.initial == "alert"
                    or not baselining and watch_id not in prior_matches
                ):
                    qualifying_new.append(watch)
                if watch.notify_drops and not baselining:
                    qualifying_drop.append(watch)

            if qualifying_new:
                self.enqueue(db, f"new:{listing_id}", self.notification("New match", candidate, confirmed, prior, listing_id), now, settings)
            elif silent_baseline:
                self.enqueue(db, f"new:{listing_id}", "", now, settings, suppressed=True)
            if (revision and previous and candidate.price is not None and previous["price"] is not None
                    and candidate.currency == previous["currency"] and candidate.price < type(candidate.price)(previous["price"])
                    and qualifying_drop):
                self.enqueue(db, f"drop:{revision}", self.notification(
                    f"Price drop from {previous['price']} {previous['currency']}", candidate, confirmed, prior, listing_id), now, settings)
        if digest:
            digest_lines = [f"{c.title or 'Untitled'} · {c.price} {c.currency}\n{c.url}" for _, c, _ in digest]
            self.enqueue(db, f"digest:{baseline_id}", "Initial match digest\n" + "\n".join(digest_lines), now, settings)
            for listing_id, _, _ in digest:
                self.enqueue(db, f"new:{listing_id}", "", now, settings, suppressed=True)
        if baseline_id:
            db.execute("INSERT OR IGNORE INTO baselines VALUES(?)", (baseline_id,))
        return {"duplicate": False, "listing_ids": ids}

    def notification(self, kind, candidate, confirmed, prior, listing_id):
        price = f"{candidate.price} {candidate.currency}" if candidate.price is not None else "Price unknown"
        time = (f"Posted {candidate.listed_at.isoformat()}" if candidate.listed_at else
                f"First seen {utc(prior['first_seen'] if prior else candidate.observed_at.timestamp()).isoformat()}")
        distance = f"{candidate.distance_km} km from {candidate.distance_from}" if candidate.distance_km is not None and candidate.distance_from else ""
        reasons = "\n".join(f"{w.name}: {', '.join(r['reasons'])}" for w, r in confirmed)
        draft = seller_draft(candidate.title, free=candidate.price == 0) if any(w.drafts for w, _ in confirmed) else ""
        dashboard = f"{self.dashboard_url}/?{urlencode({'listing': listing_id})}" if self.dashboard_url else ""
        return "\n".join(item for item in (f"{kind} · {candidate.title or 'Untitled'}", price,
                         "Simulated fixture" if candidate.source == "fixture" else candidate.source,
                         candidate.location, distance, time, candidate.url, dashboard, reasons, draft) if item)

    @staticmethod
    def settings(db):
        return json.loads(db.execute("SELECT data FROM settings WHERE id=1").fetchone()["data"])

    @staticmethod
    def enqueue(db, key, message, now, settings, suppressed=False):
        status = "suppressed" if suppressed else "muted" if settings["muted"] else "pending"
        db.execute("INSERT OR IGNORE INTO outbox(event_key,payload,status,due) VALUES(?,?,?,?)",
                   (key, message, status, now))

    def listing(self, db, row):
        result = dict(row)
        result.update(json.loads(result.pop("data")))
        result["matches"] = [dict(json.loads(m["result"]), watchlist_id=m["watchlist_id"],
                                  watchlist=json.loads(m["config"])["name"])
                             for m in db.execute("""SELECT m.*,w.config FROM matches m JOIN watchlists w
                                                    ON w.id=m.watchlist_id WHERE listing_id=?""", (row["id"],))]
        return result

    def prune(self, now, retention):
        cutoff = now - retention * 86400
        with self.connect() as db:
            db.execute("DELETE FROM observations WHERE observed<?", (cutoff,))
            db.execute("DELETE FROM runs WHERE finished<?", (cutoff,))
            db.execute("UPDATE outbox SET payload='',error=NULL WHERE due<? AND status IN ('delivered','failed','muted','suppressed')", (cutoff,))
