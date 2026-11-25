import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient
from pydantic import ValidationError

from watcher.api import create_app
from watcher.core import ListingCandidate, ListingInput, Watchlist, match
from watcher.providers import ProviderFailure
from watcher.store import Store
from watcher.worker import Worker


class Clock:
    def __init__(self):
        self.now = 1_800_000_000
        self.sleeps = []

    def __call__(self):
        return self.now

    async def sleep(self, duration):
        self.sleeps.append(duration)
        self.now += duration


class Recorder:
    def __init__(self, fail_once=False):
        self.messages = []
        self.fail_once = fail_once

    async def send(self, message):
        self.messages.append(message)
        if self.fail_once:
            self.fail_once = False
            raise ProviderFailure("Acknowledgement lost", retryable=True)


class MatchingTests(unittest.TestCase):
    def test_hard_constraints_and_unknowns(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        watch = Watchlist(name="Disk", phrase="8TB HDD", max_price=50, radius_km=20,
                          conditions=["Used"], max_age_hours=72)
        data = dict(source="manual", url="https://example.com/1", title="ＳＥＡＧＡＴＥ 8tb hdd",
                    price=30, currency="CAD", condition="used", distance_from="Halifax",
                    distance_km=10, listed_at=now - timedelta(hours=2), observed_at=now)
        result = match(watch, ListingCandidate(**data), now)
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(sum(result["breakdown"].values()), result["score"])
        for changes, expected in [
            ({"price": None}, "uncertain"), ({"currency": None}, "uncertain"),
            ({"currency": "USD"}, "rejected"), ({"title": "8GB HDD"}, "rejected"),
            ({"description": "BROKEN"}, "rejected"), ({"distance_km": 25}, "rejected"),
            ({"distance_from": "Dartmouth"}, "uncertain"), ({"condition": None}, "uncertain"),
            ({"condition": "salvage"}, "rejected"), ({"listed_at": None}, "uncertain"),
            ({"listed_at": now - timedelta(days=8)}, "rejected"),
        ]:
            with self.subTest(changes=changes):
                self.assertEqual(match(watch, ListingCandidate(**(data | changes)), now)["status"], expected)

    def test_free_and_model_boundaries(self):
        now = datetime.now(timezone.utc)
        watch = Watchlist(name="Free", phrase="P100", max_price=0, location=None)
        candidate = ListingCandidate(source="manual", url="https://example.com/1",
                                     title="Free Tesla P100", currency="CAD", price=0, observed_at=now)
        self.assertEqual(match(watch, candidate, now)["status"], "confirmed")
        self.assertEqual(match(watch, candidate.model_copy(update={"price": None}), now)["status"], "uncertain")
        self.assertEqual(match(watch, candidate.model_copy(update={"title": "P1000"}), now)["status"], "rejected")

    def test_url_validation(self):
        self.assertEqual(ListingInput(url="https://m.facebook.com/marketplace/item/123?fbclid=x").url,
                         "https://www.facebook.com/marketplace/item/123/")
        for url in ("javascript:alert(1)", "https://person@example.com", "https://example.com:25"):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                ListingInput(url=url)


class DatabaseCase:
    def setup_database(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "watcher.sqlite3"
        self.store = Store(self.path)
        self.store.migrate()
        self.clock = Clock()

    def add_watch(self, **kwargs):
        with self.store.connect() as db:
            return self.store.save_watch(db, Watchlist(**({"name": "Disk", "phrase": "8TB HDD",
                                                           "max_price": 50} | kwargs)), self.clock())

    def rows(self, table):
        with self.store.connect() as db:
            return [dict(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]


class WorkerTests(DatabaseCase, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.setup_database()
        self.notifier = Recorder()
        self.worker = Worker(self.store, self.notifier, clock=self.clock, sleep=self.clock.sleep)

    async def test_schedule_overlap_prices_and_restart(self):
        first = self.add_watch()
        self.add_watch(name="Other disk")
        self.assertEqual(first["interval_minutes"], 5)
        self.assertTrue(await self.worker.poll())
        self.assertFalse(await self.worker.poll())  # source spacing
        self.clock.now += 2
        self.assertTrue(await self.worker.poll())
        self.assertEqual([row["status"] for row in self.rows("outbox")], ["suppressed"])
        self.clock.now += 297
        self.assertFalse(await self.worker.poll())
        self.clock.now += 1
        self.assertTrue(await self.worker.poll())
        self.assertTrue(await self.worker.deliver())
        self.assertIn("Other disk", self.notifier.messages[0])
        self.assertIn("Simulated fixture", self.notifier.messages[0])
        self.clock.now += 2
        await self.worker.poll()
        self.clock.now += 298
        await self.worker.poll()
        await self.worker.deliver()
        self.assertIn("Price drop from 40", self.notifier.messages[1])
        self.assertEqual(len([r for r in self.rows("observations") if r["listing_id"] == 2]), 2)
        restarted = Store(self.path)
        restarted.migrate()
        restarted.recover(self.clock())
        worker = Worker(restarted, self.notifier, clock=self.clock)
        self.clock.now += 2
        await worker.poll()
        self.clock.now += 300
        await worker.poll()
        self.assertFalse(await worker.deliver())
        self.assertEqual(len(self.notifier.messages), 2)
        with self.store.connect() as db:
            db.execute("UPDATE watchlists SET enabled=0")
        self.clock.now += 900
        self.assertFalse(await worker.poll())

    async def test_timeout_retry_pause_and_cancel(self):
        self.add_watch()
        entered = asyncio.Event()

        class Hanging:
            async def retrieve(self, *args):
                entered.set()
                await asyncio.Future()

        self.worker.source_factory = lambda step: Hanging()
        self.worker.timeout = 0.01
        running = asyncio.create_task(self.worker.poll())
        await entered.wait()
        self.assertFalse(await self.worker.poll())
        await running
        self.assertEqual(self.rows("runs")[-1]["error"], "Provider timed out")
        self.assertEqual(self.clock.sleeps, [2, 2])

        class Denied:
            async def retrieve(self, *args):
                raise ProviderFailure("Access denied", pause=True)

        self.clock.now += 300
        self.worker.source_factory = lambda step: Denied()
        await self.worker.poll()
        self.assertEqual(self.rows("source_state")[0]["paused"], 1)
        self.assertEqual(self.rows("outbox")[0]["status"], "pending")
        self.clock.now += 300
        self.assertFalse(await self.worker.poll())

        with self.store.connect() as db:
            db.execute("UPDATE source_state SET paused=0")
        self.worker.source_factory = lambda step: Hanging()
        self.worker.timeout = 5
        entered.clear()
        running = asyncio.create_task(self.worker.poll())
        await entered.wait()
        running.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await running
        self.assertEqual(self.rows("runs")[-1]["status"], "interrupted")

    async def test_retry_after_and_delivery_across_restart(self):
        self.add_watch()
        class RateLimited:
            async def retrieve(self, *args):
                raise ProviderFailure("Rate limited", retryable=True, retry_after=600)
        self.worker.source_factory = lambda step: RateLimited()
        await self.worker.poll()
        self.assertEqual(self.rows("source_state")[0]["next_allowed"], self.clock() + 600)
        self.clock.now += 301
        self.assertFalse(await self.worker.poll())

        recorder = Recorder(fail_once=True)
        self.worker.notifier = recorder
        with self.store.connect() as db:
            self.store.enqueue(db, "new:99", "Sample", self.clock(), self.store.settings(db))
        await self.worker.deliver()
        self.assertEqual(self.rows("outbox")[0]["status"], "retry")
        restarted = Worker(Store(self.path), recorder, clock=self.clock)
        self.assertFalse(await restarted.deliver())
        self.clock.now += 2
        self.assertTrue(await restarted.deliver())
        self.assertEqual(self.rows("outbox")[0]["status"], "delivered")
        self.assertEqual(self.rows("outbox")[0]["attempts"], 2)
        self.assertEqual(recorder.messages, ["Sample", "Sample"])

    async def test_initial_policies(self):
        digest = self.add_watch(initial="digest")
        await self.worker.poll()
        keys = {entry["event_key"]: entry["status"] for entry in self.rows("outbox")}
        self.assertEqual(keys[f"digest:{digest['id']}"], "pending")
        self.assertEqual(keys["new:1"], "suppressed")


class ApiTests(DatabaseCase, unittest.TestCase):
    def setUp(self):
        self.setup_database()
        self.client = self.enterContext(TestClient(create_app(self.store, background=False,
                                                               alert_secret="local-test", clock=self.clock),
                                                  base_url="http://localhost"))
        self.headers = {"Authorization": "Bearer local-test"}

    def test_watchlist_routes_and_typed_errors(self):
        client = self.client
        config = {"name": "One", "max_price": 10}
        identifier = client.post("/api/watchlists", json=config).json()["id"]
        self.assertEqual(client.get(f"/api/watchlists/{identifier}").json()["interval_minutes"], 5)
        self.assertEqual(client.post(f"/api/watchlists/{identifier}/run").status_code, 202)
        self.assertEqual(client.put(f"/api/watchlists/{identifier}", json=config | {"enabled": False}).status_code, 200)
        self.assertEqual(client.post(f"/api/watchlists/{identifier}/run").status_code, 409)
        self.assertEqual(client.delete(f"/api/watchlists/{identifier}").status_code, 204)
        self.assertEqual(client.get(f"/api/watchlists/{identifier}").status_code, 404)
        invalid = client.post("/api/watchlists", json=config | {"min_price": 20})
        self.assertEqual(invalid.status_code, 422)
        self.assertIn("application/problem+json", invalid.headers["content-type"])
        self.assertEqual(client.post("/api/watchlists", json=config, headers={"Origin": "https://elsewhere.example"}).status_code, 403)

    def test_alerts_manual_drafts_and_history(self):
        client = self.client
        self.add_watch(sources=["manual", "alert"])
        listing = {"url": "https://example.com/disk?utm_source=local", "title": "8TB HDD",
                   "price": "40", "currency": "CAD", "location": "Halifax"}
        event = {"event_id": "test-1", "listing": listing}
        self.assertEqual(client.post("/api/ingestion/alerts", json=event).status_code, 401)
        self.assertEqual(client.post("/api/ingestion/alerts", headers=self.headers, json={}).status_code, 422)
        response = client.post("/api/ingestion/alerts", headers=self.headers, json=event)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(client.post("/api/ingestion/alerts", headers=self.headers, json=event).json()["duplicate"])
        conflict = event | {"listing": listing | {"price": "20"}}
        self.assertEqual(client.post("/api/ingestion/alerts", headers=self.headers, json=conflict).status_code, 409)
        response = client.post("/api/ingestion/manual", json=listing)
        self.assertEqual(response.status_code, 201)
        base = "/api/listings/" + str(response.json()["listing_ids"][0])
        self.assertIn("SMART", client.post(base + "/message-draft", json={}).json()["message"])
        client.post(base + "/message-draft", json={"message": "Edited"})
        client.patch(base + "/state", json={"state": "contacted manually"})
        self.clock.now += 60
        client.post("/api/ingestion/manual", json=listing | {"price": "20"})
        stale = datetime.fromtimestamp(self.clock() - 30, timezone.utc).isoformat()
        client.post("/api/ingestion/manual", json=listing | {"price": "10", "observed_at": stale})
        self.assertEqual(client.get(base).json()["draft"], "Edited")
        self.assertEqual(client.get(base).json()["price"], "20")
        self.assertEqual(client.get(base).json()["state"], "contacted manually")
        self.assertEqual(len(client.get(base + "/history").json()), 3)
        self.assertEqual(len(client.get("/api/listings?status=confirmed").json()["items"]), 2)
        self.assertEqual(len(self.rows("outbox")), 3)
        self.assertEqual(client.get("/health/ready").status_code, 200)

    def test_size_and_url_only_import(self):
        oversized = self.client.post("/api/ingestion/manual", json={"url": "x" * 33_000})
        self.assertEqual(oversized.status_code, 413)
        created = self.client.post("/api/ingestion/manual", json={"url": "https://example.com/unknown"})
        self.assertEqual(created.status_code, 201)
        row = self.client.get(f"/api/listings/{created.json()['listing_ids'][0]}").json()
        self.assertIsNone(row["price"])
        self.assertIsNone(row["listed_at"])
        self.assertEqual(self.rows("outbox"), [])

    def test_newly_qualifying_listing_and_dashboard_link(self):
        self.store.dashboard_url = "http://127.0.0.1:8000"
        self.add_watch(sources=["manual"])
        listing = {"url": "https://example.com/disk", "title": "8TB HDD", "price": "70",
                   "currency": "CAD", "location": "Halifax"}
        first = self.client.post("/api/ingestion/manual", json=listing)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(self.rows("outbox"), [])
        self.clock.now += 10
        self.client.post("/api/ingestion/manual", json=listing | {"price": "39"})
        events = self.rows("outbox")
        self.assertEqual(len(events), 2)
        self.assertTrue(events[0]["event_key"].startswith("new:"))
        self.assertTrue(events[1]["event_key"].startswith("drop:"))
        self.assertIn("http://127.0.0.1:8000/?listing=1", events[0]["payload"])

    def test_untrusted_host_and_disabled_alert_endpoint(self):
        self.assertEqual(self.client.get("/", headers={"Host": "unexpected.example"}).status_code, 400)
        with TestClient(create_app(self.store, background=False, alert_secret=""), base_url="http://localhost") as client:
            response = client.post("/api/ingestion/alerts", headers=self.headers,
                                   json={"event_id": "disabled", "listing": {"url": "https://example.com/disk"}})
            self.assertEqual(response.status_code, 401)
