"""Single-process scheduler and persistent notification delivery."""
import asyncio
import logging
import time

from .core import Watchlist
from .providers import FixtureListingSource, ProviderFailure
from .store import utc


class Worker:
    def __init__(self, store, notifier, *, clock=time.time, timeout=10, spacing=2, retention=90,
                 source_factory=FixtureListingSource, sleep=asyncio.sleep):
        self.store = store
        self.notifier = notifier
        self.clock = clock
        self.timeout = timeout
        self.spacing = spacing
        self.retention = retention
        self.source_factory = source_factory
        self.sleep = sleep
        self.last_prune = None
        self.poll_lock = asyncio.Lock()
        self.delivery_lock = asyncio.Lock()

    async def serve(self, tick):
        while True:
            try:
                await tick()
            except Exception:
                logging.getLogger(__name__).error("Background task failed; will retry")
            await self.sleep(1)

    async def poll(self):
        if self.poll_lock.locked():
            return False
        async with self.poll_lock:
            return await self._poll()

    async def _poll(self):
        started = self.clock()
        if self.last_prune is None or started - self.last_prune >= 3600:
            self.store.prune(started, self.retention)
            self.last_prune = started
        with self.store.connect() as db:
            source = db.execute("SELECT * FROM source_state WHERE source='fixture'").fetchone()
            if source and (source["paused"] or source["next_allowed"] > started):
                return False
            row = db.execute("""SELECT * FROM watchlists WHERE enabled=1 AND (queued=1 OR due<=?)
                                ORDER BY queued DESC,due,id LIMIT 1""", (started,)).fetchone()
            if row is None:
                return False
            watch = Watchlist.model_validate_json(row["config"])
            watch_id = row["id"]
            db.execute("UPDATE watchlists SET queued=0,last_attempt=?,due=? WHERE id=?",
                       (started, started + watch.interval_minutes * 60, watch_id))
            latest_step = db.execute("SELECT stage FROM fixture_feed WHERE id=1").fetchone()[0]
            run_id = db.execute("""INSERT INTO runs(watchlist_id,name,source,started,status)
                                   VALUES(?,?,'fixture',?,'running')""", (watch_id, watch.name, started)).lastrowid
        error = None
        retry_after = 0
        paused = False
        status = "failed"
        try:
            stage = max(row["fixture_step"] + 1, latest_step)
            provider = self.source_factory(stage)
            for attempt in range(3):
                candidates = []
                try:
                    async with asyncio.timeout(self.timeout):
                        cursor = None
                        for _ in range(20):
                            page = await provider.retrieve(watch, cursor, utc(self.clock()))
                            candidates.extend(page.listings)
                            if page.cursor is None:
                                break
                            cursor = page.cursor
                        else:
                            raise ProviderFailure("Provider exceeded page limit")
                except TimeoutError:
                    failure = ProviderFailure("Provider timed out", retryable=True)
                except ProviderFailure as exc:
                    failure = exc
                else:
                    with self.store.connect() as db:
                        active = db.execute("SELECT enabled,config FROM watchlists WHERE id=?", (watch_id,)).fetchone()
                        accepted = active and active["enabled"] and active["config"] == row["config"]
                        if accepted:
                            now = self.clock()
                            result = self.store.ingest(db, candidates, now, baseline_id=watch_id)
                            listing_ids = result["listing_ids"]
                            db.executemany("INSERT INTO run_listings VALUES(?,?)",
                                           [(run_id, listing_id) for listing_id in listing_ids])
                            db.execute("UPDATE runs SET result_count=?,status='success',finished=? WHERE id=?",
                                       (len(listing_ids), now, run_id))
                            db.execute("UPDATE watchlists SET fixture_step=fixture_step+1 WHERE id=?", (watch_id,))
                            db.execute("UPDATE fixture_feed SET stage=? WHERE id=1", (stage,))
                    status = "success" if accepted else "cancelled"
                    break
                error = str(failure)
                retry_after = max(0, failure.retry_after)
                paused = failure.pause
                if paused or not failure.retryable or attempt == 2 or retry_after > 15:
                    break
                await self.sleep(max(self.spacing, retry_after, 2 ** attempt))
        except asyncio.CancelledError:
            status = "interrupted"
            error = "Service stopped"
            raise
        except Exception:
            error = "Provider or persistence error"
        finally:
            now = self.clock()
            error = None if status == "success" else error
            with self.store.connect() as db:
                db.execute("UPDATE runs SET finished=?,status=?,error=? WHERE id=?", (now, status, error, run_id))
                db.execute("""UPDATE watchlists SET last_error=?,last_success=CASE WHEN ?='success'
                              THEN ? ELSE last_success END WHERE id=?""", (error, status, now, watch_id))
                db.execute("""INSERT INTO source_state VALUES('fixture',?,?)
                              ON CONFLICT(source) DO UPDATE SET next_allowed=excluded.next_allowed,paused=excluded.paused""",
                           (now + max(self.spacing, retry_after), paused))
                if paused:
                    self.store.enqueue(db, f"pause:{run_id}", "Fixture source paused after access failure. Review source status.",
                                       now, self.store.settings(db))
        return True

    async def deliver(self):
        if self.delivery_lock.locked():
            return False
        async with self.delivery_lock:
            return await self._deliver()

    async def _deliver(self):
        now = self.clock()
        with self.store.connect() as db:
            row = db.execute("""SELECT * FROM outbox WHERE status IN ('pending','retry') AND due<=?
                                ORDER BY due,id LIMIT 1""", (now,)).fetchone()
            if row is None:
                return False
            if self.store.settings(db)["muted"] and not row["event_key"].startswith("test:"):
                db.execute("UPDATE outbox SET status='muted' WHERE id=?", (row["id"],))
                return True
            attempts = row["attempts"] + 1
            db.execute("UPDATE outbox SET attempts=? WHERE id=?", (attempts, row["id"]))
        status, error, due = "delivered", None, now
        try:
            async with asyncio.timeout(self.timeout):
                await self.notifier.send(row["payload"])
        except (TimeoutError, ProviderFailure) as exc:
            error = str(exc) if isinstance(exc, ProviderFailure) else "Delivery acknowledgement timed out"
            retryable = not isinstance(exc, ProviderFailure) or exc.retryable
            status = "retry" if retryable and attempts < 6 else "failed"
            due = self.clock() + max(2 ** attempts, getattr(exc, "retry_after", 0))
        except Exception:
            status, error = "failed", "Unexpected delivery error"
        with self.store.connect() as db:
            db.execute("UPDATE outbox SET status=?,error=?,due=? WHERE id=?", (status, error, due, row["id"]))
        return True
