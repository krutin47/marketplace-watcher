"""Same-origin API and dashboard. Run one Uvicorn worker per database."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
from decimal import Decimal
import hmac
import json
import os
from pathlib import Path
import time
import sqlite3
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from .core import Contract, ListingCandidate, ListingInput, Short, Source, Watchlist, seller_draft
from .providers import ConsoleNotifier, SOURCES, TelegramNotifier
from .store import Store, utc
from .worker import Worker

WEB = Path(__file__).resolve().parent.parent / "web"


class Alert(Contract):
    event_id: Short
    listing: ListingInput


class State(Contract):
    state: Literal["new", "saved", "dismissed", "contacted manually"]


class Draft(Contract):
    message: str | None = Field(default=None, max_length=1200)


class Settings(Contract):
    muted: bool = False
    minimum_score: int = Field(default=0, ge=0, le=100)
    minimum_priority: int = Field(default=0, ge=0, le=10)


def problem(status, detail, **extra):
    return JSONResponse({"type": "about:blank", "title": {
        400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found",
        409: "Conflict", 413: "Content Too Large", 422: "Unprocessable Content",
        503: "Service Unavailable",
    }.get(status, "Request failed"), "status": status, "detail": detail, **extra},
        status_code=status, media_type="application/problem+json")


def create_app(store=None, *, background=True, alert_secret=None, notifier=None, clock=time.time):
    dashboard_url = os.getenv("WATCHER_DASHBOARD_URL")
    if dashboard_url:
        dashboard = urlsplit(dashboard_url)
        if (dashboard.scheme not in {"http", "https"} or not dashboard.hostname
                or dashboard.username or dashboard.password or dashboard.path not in {"", "/"}
                or dashboard.query or dashboard.fragment):
            raise RuntimeError("Dashboard URL must be an HTTP(S) origin")
        dashboard_url = dashboard_url.rstrip("/")
    store = store or Store(os.getenv("WATCHER_DB", "data/watcher.sqlite3"), dashboard_url)
    alert_secret = alert_secret if alert_secret is not None else os.getenv("INGESTION_SECRET", "")
    token, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if bool(token) != bool(chat):
        raise RuntimeError("Set both Telegram credentials or leave both empty")
    notifier = notifier or (TelegramNotifier(token, chat) if token else ConsoleNotifier())
    worker = Worker(store, notifier, clock=clock, spacing=max(0.1, float(os.getenv("SOURCE_SPACING_SECONDS", "2"))),
                    timeout=max(0.1, float(os.getenv("WORKER_TIMEOUT_SECONDS", "12"))),
                    retention=max(1, int(os.getenv("RETENTION_DAYS", "90"))))

    @asynccontextmanager
    async def lifespan(app):
        store.migrate()
        store.recover(clock())
        tasks = [asyncio.create_task(worker.serve(tick)) for tick in (worker.poll, worker.deliver)] if background else []
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(title="Marketplace Watcher", version="0.1.0", lifespan=lifespan)
    allowed_hosts = os.getenv("WATCHER_ALLOWED_HOSTS", "127.0.0.1,localhost,[::1]")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[host.strip() for host in allowed_hosts.split(",")])
    app.state.worker = worker

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            allowed = os.getenv("WATCHER_ORIGIN") or str(request.base_url).rstrip("/")
            if (origin and origin != allowed) or request.headers.get("sec-fetch-site") == "cross-site":
                return problem(403, "Cross-site changes are disabled")
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 32768:
                    return problem(413, "Request exceeds 32 KiB")
            request._body = bytes(body)
            if body and request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
                return problem(400, "Use application/json")
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        if request.url.path == "/" or request.url.path.startswith("/assets/"):
            response.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        return problem(exc.status_code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return problem(422, "Request validation failed", errors=[
            {"field": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
            for error in exc.errors()])

    def require(db, table, identifier):
        row = db.execute(f"SELECT * FROM {table} WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise HTTPException(404, "Record not found")
        return row

    @app.get("/api/watchlists")
    async def watches():
        with store.connect() as db:
            return store.watchlists(db)

    @app.post("/api/watchlists", status_code=201)
    async def create_watch(body: Watchlist):
        with store.connect() as db:
            return store.save_watch(db, body, clock())

    @app.get("/api/watchlists/{identifier}")
    async def get_watch(identifier: int):
        with store.connect() as db:
            return store.watch(require(db, "watchlists", identifier))

    @app.put("/api/watchlists/{identifier}")
    async def edit_watch(identifier: int, body: Watchlist):
        with store.connect() as db:
            require(db, "watchlists", identifier)
            return store.save_watch(db, body, clock(), identifier)

    @app.delete("/api/watchlists/{identifier}", status_code=204)
    async def delete_watch(identifier: int):
        with store.connect() as db:
            require(db, "watchlists", identifier)
            db.execute("DELETE FROM watchlists WHERE id=?", (identifier,))

    @app.post("/api/watchlists/{identifier}/run", status_code=202)
    async def run_watch(identifier: int):
        with store.connect() as db:
            row = require(db, "watchlists", identifier)
            if not row["enabled"] or "fixture" not in store.watch(row)["sources"]:
                raise HTTPException(409, "Enable a polling-capable source to run checks")
            if db.execute("SELECT 1 FROM source_state WHERE source='fixture' AND paused=1").fetchone():
                raise HTTPException(409, "Fixture source is paused")
            running = db.execute("SELECT 1 FROM runs WHERE watchlist_id=? AND status='running'", (identifier,)).fetchone()
            if not running:
                db.execute("UPDATE watchlists SET queued=1 WHERE id=?", (identifier,))
            return {"status": "running" if running else "queued"}

    @app.get("/api/sources")
    async def sources():
        with store.connect() as db:
            return [dict(asdict(source), state=dict(row) if (row := db.execute(
                "SELECT * FROM source_state WHERE source=?", (source.id,)).fetchone()) else None)
                    for source in SOURCES]

    @app.post("/api/sources/fixture/resume")
    async def resume():
        with store.connect() as db:
            db.execute("UPDATE source_state SET paused=0 WHERE source='fixture'")
        return {"status": "resumed"}

    @app.post("/api/demo", status_code=201)
    async def demo():
        examples = (("8TB HDD", 50), ("Tesla P100 16GB", 100), ("Dell OptiPlex", 30), ("Free electronics", 0))
        with store.connect() as db:
            names = {watch["name"] for watch in store.watchlists(db)}
            return [store.save_watch(db, Watchlist(name=name, phrase=name, max_price=price), clock())
                    for name, price in examples if name not in names]

    @app.get("/api/listings")
    async def listings(
        offset: int = Query(0, ge=0, le=1_000_000),
        limit: int = Query(30, ge=1, le=100),
        state: Literal["new", "saved", "dismissed", "contacted manually"] | None = None,
        source: Source | None = None,
        status: Literal["confirmed", "uncertain", "unmatched"] | None = None,
        watchlist_id: int | None = None,
        sort: Literal["recent", "price", "score"] = "recent",
    ):
        filters, args = [], []
        for field, value in (("state", state), ("source", source)):
            if value is not None:
                filters.append(f"l.{field}=?")
                args.append(value)
        if status == "unmatched":
            filters.append("NOT EXISTS (SELECT 1 FROM matches m WHERE m.listing_id=l.id)")
        elif status:
            filters.append("EXISTS (SELECT 1 FROM matches m WHERE m.listing_id=l.id AND m.status=?)")
            args.append(status)
        if watchlist_id is not None:
            filters.append("EXISTS (SELECT 1 FROM matches m WHERE m.listing_id=l.id AND m.watchlist_id=?)")
            args.append(watchlist_id)
        order = {
            "recent": "l.last_seen DESC",
            "price": "json_extract(l.data,'$.price') IS NULL, CAST(json_extract(l.data,'$.price') AS REAL)",
            "score": "(SELECT MAX(json_extract(m.result,'$.score')) FROM matches m WHERE m.listing_id=l.id) DESC",
        }[sort]
        where = " WHERE " + " AND ".join(filters) if filters else ""
        with store.connect() as db:
            rows = db.execute(f"SELECT l.* FROM listings l{where} ORDER BY {order},l.id DESC LIMIT ? OFFSET ?",
                              (*args, limit + 1, offset)).fetchall()
            return {"items": [store.listing(db, row) for row in rows[:limit]],
                    "next_offset": offset + limit if len(rows) > limit else None}

    @app.get("/api/listings/{identifier}")
    async def listing(identifier: int):
        with store.connect() as db:
            return store.listing(db, require(db, "listings", identifier))

    @app.patch("/api/listings/{identifier}/state")
    async def update_state(identifier: int, body: State):
        with store.connect() as db:
            require(db, "listings", identifier)
            db.execute("UPDATE listings SET state=? WHERE id=?", (body.state, identifier))
            return {"state": body.state}

    @app.get("/api/listings/{identifier}/history")
    async def history(identifier: int, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
        with store.connect() as db:
            require(db, "listings", identifier)
            return [dict(observed=row["observed"], **json.loads(row["data"]))
                    for row in db.execute("""SELECT * FROM observations WHERE listing_id=?
                        ORDER BY observed DESC,id DESC LIMIT ? OFFSET ?""", (identifier, limit, offset))]

    @app.post("/api/listings/{identifier}/message-draft")
    async def draft(identifier: int, body: Draft):
        with store.connect() as db:
            row = require(db, "listings", identifier)
            data = json.loads(row["data"])
            message = body.message if body.message is not None else row["draft"]
            if message is None:
                message = seller_draft(data["title"], free=data["price"] is not None and Decimal(data["price"]) == 0)
            db.execute("UPDATE listings SET draft=? WHERE id=?", (message, identifier))
            return {"message": message}

    def candidate(body, source):
        now = utc(clock())
        if body.observed_at and body.observed_at.timestamp() > clock() + 300:
            raise HTTPException(422, "Observation is too far in the future")
        if body.listed_at and body.listed_at > now:
            raise HTTPException(422, "Posted time is in the future")
        return ListingCandidate(**body.model_dump(exclude={"observed_at"}), source=source,
                                observed_at=body.observed_at or now)

    @app.post("/api/ingestion/manual", status_code=201)
    async def manual(body: ListingInput):
        with store.connect() as db:
            return store.ingest(db, [candidate(body, "manual")], clock())

    async def check_alert(request: Request):
        header = request.headers.get("authorization", "")
        if not alert_secret or not hmac.compare_digest(header.encode(), f"Bearer {alert_secret}".encode()):
            raise HTTPException(401, "Invalid ingestion credentials")

    @app.post("/api/ingestion/alerts", dependencies=[Depends(check_alert)])
    async def alert(body: Alert):
        with store.connect() as db:
            try:
                return store.ingest(db, [candidate(body.listing, "alert")], clock(), event_id=body.event_id,
                                    event_observed_at=body.listing.observed_at.isoformat() if body.listing.observed_at else None)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None

    @app.get("/api/runs")
    async def runs(limit: int = Query(40, ge=1, le=100)):
        with store.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))]

    @app.get("/api/notifications")
    async def notifications(limit: int = Query(40, ge=1, le=100)):
        with store.connect() as db:
            return {"settings": store.settings(db), "provider": "telegram" if token else "console",
                    "delivery": [dict(row) for row in db.execute("SELECT * FROM outbox ORDER BY id DESC LIMIT ?", (limit,))]}

    @app.put("/api/notifications/settings")
    async def save_settings(body: Settings):
        with store.connect() as db:
            db.execute("UPDATE settings SET data=? WHERE id=1", (body.model_dump_json(),))
            return store.settings(db)

    @app.post("/api/notifications/test", status_code=202)
    async def send_test():
        with store.connect() as db:
            store.enqueue(db, f"test:{uuid4()}", "Marketplace Watcher test notification", clock(), {"muted": False})
        return {"status": "queued"}

    @app.get("/health/live")
    async def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready():
        try:
            with store.connect() as db:
                db.execute("SELECT version FROM schema_version").fetchall()
        except sqlite3.Error:
            return problem(503, "Database is unavailable")
        return {"status": "ready"}

    @app.get("/api/overview")
    async def overview():
        with store.connect() as db:
            return dict(db.execute("""SELECT
                (SELECT COUNT(*) FROM watchlists WHERE enabled=1) enabled,
                (SELECT COUNT(DISTINCT listing_id) FROM matches WHERE status='confirmed') confirmed,
                (SELECT MAX(last_success) FROM watchlists) last_sync,
                (SELECT COUNT(*) FROM runs WHERE status='failed') failed""").fetchone())

    @app.get("/", include_in_schema=False)
    async def dashboard():
        return FileResponse(WEB / "index.html")

    app.mount("/assets", StaticFiles(directory=WEB), name="assets")
    return app


app = create_app()
