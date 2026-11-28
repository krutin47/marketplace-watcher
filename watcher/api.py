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
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from datetime import datetime, timezone
import hashlib

from pydantic import Field, StringConstraints, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from .assistant import PROMPT_VERSION, TERMINAL, AssistantRunner, FixtureAssistantProvider, capabilities_dict
from .errors import ApiError
from .purchasing import register as register_purchasing
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


class EvidenceIn(Contract):
    kind: Literal["seller_claim", "user_note"]
    content: str = Field(min_length=1, max_length=4000)
    origin: Short = "user"
    received_at: datetime | None = None
    supersedes_id: int | None = Field(default=None, ge=1)

    @field_validator("received_at")
    @classmethod
    def aware(cls, value):
        if value is not None and value.utcoffset() is None:
            raise ValueError("Timestamps require a UTC offset")
        return value


class ConversationIn(Contract):
    listing_id: int = Field(ge=1)
    project_id: int | None = Field(default=None, ge=1)
    title: Short | None = None


EXCLUDABLE = r"^(listing:description|history|project|memory|evidence:[1-9][0-9]{0,9})$"


class ConversationPatch(Contract):
    version: int = Field(ge=1)
    title: Short | None = None
    archived: bool | None = None
    excluded: list[Annotated[str, StringConstraints(pattern=EXCLUDABLE)]] | None = Field(default=None, max_length=50)
    project_id: int | None = Field(default=None, ge=1)


RequestKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{8,80}$")]


class MessageIn(Contract):
    content: str = Field(min_length=1, max_length=4000)
    request_key: RequestKey
    listing_id: int = Field(ge=1)
    project_id: int | None = Field(default=None, ge=1)


class RetryIn(Contract):
    request_key: RequestKey


def problem(status, detail, code=None, **extra):
    body = {"type": "about:blank", "title": {
        400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found",
        409: "Conflict", 413: "Content Too Large", 422: "Unprocessable Content",
        429: "Too Many Requests", 503: "Service Unavailable",
    }.get(status, "Request failed"), "status": status, "detail": detail, **extra}
    if code:
        body["code"] = code
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


_DEFAULT = object()


def create_app(store=None, *, background=True, alert_secret=None, notifier=None, clock=time.time,
               assistant=_DEFAULT):
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
    if assistant is _DEFAULT:
        choice = os.getenv("ASSISTANT_PROVIDER", "fixture")
        if choice not in {"fixture", "none"}:
            raise RuntimeError("ASSISTANT_PROVIDER must be 'fixture' or 'none'; no cloud provider is implemented")
        assistant = FixtureAssistantProvider(delay=0.05) if choice == "fixture" else None
    runner = AssistantRunner(store, assistant, clock=clock,
                             timeout=max(1.0, float(os.getenv("ASSISTANT_TIMEOUT_SECONDS", "60"))))

    @asynccontextmanager
    async def lifespan(app):
        store.migrate()
        store.recover(clock())
        runner.recover()
        tasks = [asyncio.create_task(worker.serve(tick)) for tick in (worker.poll, worker.deliver)] if background else []
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await runner.shutdown()

    app = FastAPI(title="Marketplace Watcher", version="0.1.0", lifespan=lifespan)
    allowed_hosts = os.getenv("WATCHER_ALLOWED_HOSTS", "127.0.0.1,localhost,[::1]")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[host.strip() for host in allowed_hosts.split(",")])
    app.state.worker = worker
    app.state.assistant = runner

    @app.middleware("http")
    async def guard(request: Request, call_next):
        request.state.correlation_id = uuid4().hex
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
        response.headers["X-Correlation-ID"] = request.state.correlation_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        if request.url.path == "/" or request.url.path.startswith("/assets/"):
            response.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    def correlation(request):
        return getattr(request.state, "correlation_id", None)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        return problem(exc.status_code, str(exc.detail), correlation_id=correlation(request))

    @app.exception_handler(ApiError)
    async def api_error(request, exc):
        return problem(exc.status, exc.detail, exc.code, correlation_id=correlation(request))

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return problem(422, "Request validation failed", "validation_failed", correlation_id=correlation(request), errors=[
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
        run_id: int | None = Query(None, ge=1),
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
        if run_id is not None:
            filters.append("l.id IN (SELECT listing_id FROM run_listings WHERE run_id=?)")
            args.append(run_id)
        order = {
            "recent": "l.last_seen DESC",
            "price": "json_extract(l.data,'$.price') IS NULL, CAST(json_extract(l.data,'$.price') AS REAL)",
            "score": "(SELECT MAX(json_extract(m.result,'$.score')) FROM matches m WHERE m.listing_id=l.id) DESC",
        }[sort]
        where = " WHERE " + " AND ".join(filters) if filters else ""
        with store.connect() as db:
            if run_id is not None and require(db, "runs", run_id)["result_count"] is None:
                raise HTTPException(409, "This run has no recorded listing results")
            total = db.execute(f"SELECT COUNT(*) FROM listings l{where}", args).fetchone()[0]
            rows = db.execute(f"SELECT l.* FROM listings l{where} ORDER BY {order},l.id DESC LIMIT ? OFFSET ?",
                              (*args, limit, offset)).fetchall()
            return {"items": [store.listing(db, row) for row in rows], "total": total,
                    "next_offset": offset + limit if offset + limit < total else None}

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

    # ----- Evidence (handbook section 7, CHAT-08) -----
    def evidence_row(row):
        return {key: row[key] for key in row.keys()}

    @app.get("/api/listings/{identifier}/evidence")
    async def list_evidence(identifier: int, offset: int = Query(0, ge=0, le=1_000_000),
                            limit: int = Query(50, ge=1, le=100)):
        with store.connect() as db:
            require(db, "listings", identifier)
            total = db.execute("SELECT COUNT(*) FROM evidence WHERE listing_id=?", (identifier,)).fetchone()[0]
            rows = db.execute("""SELECT * FROM evidence WHERE listing_id=? ORDER BY created DESC,id DESC
                                 LIMIT ? OFFSET ?""", (identifier, limit, offset)).fetchall()
            return {"items": [evidence_row(row) for row in rows], "total": total,
                    "next_offset": offset + limit if offset + limit < total else None}

    @app.post("/api/listings/{identifier}/evidence", status_code=201)
    async def add_evidence(identifier: int, body: EvidenceIn):
        now = clock()
        received = body.received_at.timestamp() if body.received_at else None
        if received is not None and received > now + 300:
            raise ApiError(422, "future_timestamp", "Received time is in the future")
        with store.connect() as db:
            require(db, "listings", identifier)
            if body.supersedes_id is not None:
                prior = db.execute("SELECT listing_id FROM evidence WHERE id=?", (body.supersedes_id,)).fetchone()
                if prior is None or prior["listing_id"] != identifier:
                    raise ApiError(422, "invalid_supersession", "Superseded evidence must belong to this listing")
            evidence_id = db.execute("""INSERT INTO evidence(listing_id,kind,origin,content,content_hash,received_at,
                                        created,supersedes_id) VALUES(?,?,?,?,?,?,?,?)""",
                                     (identifier, body.kind, body.origin, body.content,
                                      hashlib.sha256(body.content.encode()).hexdigest(), received, now,
                                      body.supersedes_id)).lastrowid
            return evidence_row(db.execute("SELECT * FROM evidence WHERE id=?", (evidence_id,)).fetchone())

    # ----- Conversations (CHAT-01..07) -----
    def conversation_row(db, row):
        data = dict(row)
        data["excluded"] = json.loads(data["excluded"])
        active = db.execute("""SELECT id,status FROM assistant_runs WHERE conversation_id=?
                               AND status IN ('queued','running') ORDER BY id DESC LIMIT 1""", (row["id"],)).fetchone()
        data["active_run"] = dict(active) if active else None
        last = db.execute("""SELECT id,status,error_code,user_message_id FROM assistant_runs WHERE conversation_id=?
                             ORDER BY id DESC LIMIT 1""", (row["id"],)).fetchone()
        data["last_run"] = dict(last) if last else None
        data["message_count"] = db.execute("SELECT COUNT(*) FROM chat_messages WHERE conversation_id=?",
                                           (row["id"],)).fetchone()[0]
        return data

    def conversation(db, identifier):
        row = db.execute("SELECT * FROM conversations WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise ApiError(404, "conversation_not_found", "Conversation not found")
        return row

    @app.get("/api/conversations")
    async def list_conversations(listing_id: int | None = Query(None, ge=1), archived: bool = False,
                                 limit: int = Query(30, ge=1, le=100)):
        filters, args = ["archived=?"], [archived]
        if listing_id is not None:
            filters.append("listing_id=?")
            args.append(listing_id)
        with store.connect() as db:
            rows = db.execute(f"SELECT * FROM conversations WHERE {' AND '.join(filters)} ORDER BY updated DESC,id DESC"
                              " LIMIT ?", (*args, limit)).fetchall()
            return [conversation_row(db, row) for row in rows]

    @app.post("/api/conversations", status_code=201)
    async def create_conversation(body: ConversationIn):
        now = clock()
        with store.connect() as db:
            row = require(db, "listings", body.listing_id)
            title = body.title or ("Chat · " + (json.loads(row["data"]).get("title") or "Untitled listing"))[:160]
            if body.project_id is not None:
                require(db, "projects", body.project_id)
            identifier = db.execute("""INSERT INTO conversations(scope,listing_id,project_id,title,created,updated)
                                       VALUES('listing',?,?,?,?,?)""",
                                    (body.listing_id, body.project_id, title, now, now)).lastrowid
            return conversation_row(db, conversation(db, identifier))

    @app.get("/api/conversations/{identifier}")
    async def get_conversation(identifier: int):
        with store.connect() as db:
            return conversation_row(db, conversation(db, identifier))

    @app.patch("/api/conversations/{identifier}")
    async def patch_conversation(identifier: int, body: ConversationPatch):
        with store.connect() as db:
            row = conversation(db, identifier)
            if row["version"] != body.version:
                raise ApiError(409, "version_conflict", "Conversation changed since it was loaded; reload and retry")
            updates = {key: value for key, value in (("title", body.title), ("archived", body.archived),
                       ("excluded", json.dumps(sorted(set(body.excluded))) if body.excluded is not None else None))
                       if value is not None}
            if "project_id" in body.model_fields_set:
                if body.project_id is not None:
                    require(db, "projects", body.project_id)
                updates["project_id"] = body.project_id
            assignments = "".join(f"{key}=?," for key in updates)
            db.execute(f"UPDATE conversations SET {assignments}version=version+1,updated=? WHERE id=?",
                       (*updates.values(), clock(), identifier))
            return conversation_row(db, conversation(db, identifier))

    @app.delete("/api/conversations/{identifier}", status_code=204)
    async def delete_conversation(identifier: int):
        with store.connect() as db:
            conversation(db, identifier)
            active = [row["id"] for row in db.execute(
                "SELECT id FROM assistant_runs WHERE conversation_id=? AND status IN ('queued','running')", (identifier,))]
        for run_id in active:
            runner.cancel(run_id)
            task = runner.tasks.get(run_id)
            if task:
                await asyncio.gather(task, return_exceptions=True)
        with store.connect() as db:
            db.execute("DELETE FROM conversations WHERE id=?", (identifier,))

    @app.get("/api/conversations/{identifier}/messages")
    async def list_messages(identifier: int, before_seq: int | None = Query(None, ge=1),
                            limit: int = Query(50, ge=1, le=100)):
        with store.connect() as db:
            conversation(db, identifier)
            rows = db.execute("""SELECT * FROM chat_messages WHERE conversation_id=? AND seq<?
                                 ORDER BY seq DESC LIMIT ?""", (identifier, before_seq or 2**62, limit + 1)).fetchall()
            more = len(rows) > limit
            items = [dict(row) for row in reversed(rows[:limit])]
            return {"items": items, "next_before_seq": items[0]["seq"] if more and items else None}

    def require_provider():
        if runner.provider is None:
            raise ApiError(503, "assistant_unavailable",
                           "The assistant is disabled. Listing details, notes and drafts still work.")

    def create_run(db, conversation_id, user_message_id, request_key):
        caps = runner.provider.capabilities
        return db.execute("""INSERT INTO assistant_runs(conversation_id,user_message_id,request_key,status,provider,
                             model,prompt_version,created) VALUES(?,?,?,'queued',?,?,?,?)""",
                          (conversation_id, user_message_id, request_key, caps.id, caps.model,
                           PROMPT_VERSION, clock())).lastrowid

    @app.post("/api/conversations/{identifier}/messages", status_code=202)
    async def post_message(identifier: int, body: MessageIn):
        with store.connect() as db:
            row = conversation(db, identifier)
            prior = db.execute("SELECT * FROM assistant_runs WHERE conversation_id=? AND request_key=?",
                               (identifier, body.request_key)).fetchone()
            if prior:
                message = db.execute("SELECT content FROM chat_messages WHERE id=?", (prior["user_message_id"],)).fetchone()
                if message is None or message["content"] != body.content:
                    raise ApiError(409, "idempotency_conflict", "Request key was already used for a different message")
                return {"run_id": prior["id"], "user_message_id": prior["user_message_id"], "duplicate": True}
            require_provider()
            if row["listing_id"] != body.listing_id:
                raise ApiError(409, "scope_mismatch",
                               "This conversation is about a different listing. Start a new conversation for it.")
            if "project_id" in body.model_fields_set and row["project_id"] != body.project_id:
                raise ApiError(409, "scope_mismatch", "The conversation's project changed. Reload before sending.")
            if row["archived"]:
                raise ApiError(409, "conversation_archived", "Unarchive the conversation to continue it")
            if db.execute("SELECT 1 FROM assistant_runs WHERE conversation_id=? AND status IN ('queued','running')",
                          (identifier,)).fetchone():
                raise ApiError(409, "run_in_progress", "Wait for the current reply or stop it first")
            message_id = runner.add_message(db, identifier, "user", body.content, "complete", listing_id=body.listing_id)
            run_id = create_run(db, identifier, message_id, body.request_key)
        runner.start(run_id)
        return {"run_id": run_id, "user_message_id": message_id, "duplicate": False}

    def run_record(db, run_id):
        row = db.execute("SELECT * FROM assistant_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise ApiError(404, "run_not_found", "Assistant run not found")
        return row

    @app.get("/api/assistant/runs/{identifier}")
    async def get_run(identifier: int):
        with store.connect() as db:
            run = dict(run_record(db, identifier))
            snapshot = db.execute("SELECT data FROM context_snapshots WHERE run_id=?", (identifier,)).fetchone()
            run["context"] = json.loads(snapshot["data"]) if snapshot else None
            return run

    @app.post("/api/assistant/runs/{identifier}/cancel", status_code=202)
    async def cancel_run(identifier: int):
        with store.connect() as db:
            status = run_record(db, identifier)["status"]
        if status in TERMINAL:
            return {"status": status}
        runner.cancel(identifier)
        return {"status": "cancel_requested"}

    @app.post("/api/assistant/runs/{identifier}/retry", status_code=202)
    async def retry_run(identifier: int, body: RetryIn):
        with store.connect() as db:
            prior = run_record(db, identifier)
            existing = db.execute("SELECT id FROM assistant_runs WHERE conversation_id=? AND request_key=?",
                                  (prior["conversation_id"], body.request_key)).fetchone()
            if existing:
                return {"run_id": existing["id"], "duplicate": True}
            require_provider()
            if prior["status"] not in {"failed", "cancelled", "interrupted"}:
                raise ApiError(409, "not_retryable", "Only failed, stopped or interrupted replies can be retried")
            if db.execute("SELECT 1 FROM assistant_runs WHERE conversation_id=? AND status IN ('queued','running')",
                          (prior["conversation_id"],)).fetchone():
                raise ApiError(409, "run_in_progress", "Wait for the current reply or stop it first")
            run_id = create_run(db, prior["conversation_id"], prior["user_message_id"], body.request_key)
        runner.start(run_id)
        return {"run_id": run_id, "duplicate": False}

    @app.get("/api/assistant/runs/{identifier}/events")
    async def run_events(identifier: int, request: Request, after: int = Query(0, ge=0)):
        with store.connect() as db:
            run_record(db, identifier)
        last = request.headers.get("last-event-id", "")
        last = int(last) if last.isdigit() else after

        async def stream():
            nonlocal last
            idle = 0.0
            deadline = time.monotonic() + runner.timeout + 30
            while time.monotonic() < deadline:
                with store.connect() as db:
                    status = db.execute("SELECT status FROM assistant_runs WHERE id=?", (identifier,)).fetchone()
                    rows = db.execute("SELECT * FROM run_events WHERE run_id=? AND seq>? ORDER BY seq",
                                      (identifier, last)).fetchall()
                for row in rows:
                    last = row["seq"]
                    yield f"id: {row['seq']}\nevent: {row['type']}\ndata: {row['data']}\n\n"
                if status is None or status["status"] in TERMINAL:
                    return
                if await request.is_disconnected():
                    return
                await asyncio.sleep(0.1)
                idle = 0.0 if rows else idle + 0.1
                if idle >= 15:
                    idle = 0.0
                    yield ": keep-alive\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.get("/api/assistant/usage")
    async def usage():
        start_of_day = datetime.fromtimestamp(clock(), timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        with store.connect() as db:
            totals = dict(db.execute("""SELECT COUNT(*) runs, COALESCE(SUM(input_tokens),0) input_tokens,
                COALESCE(SUM(output_tokens),0) output_tokens, COALESCE(SUM(estimated_cost_micros),0) estimated_cost_micros,
                COALESCE(SUM(status='failed'),0) failed FROM assistant_runs WHERE created>=?""",
                (start_of_day.timestamp(),)).fetchone())
        return {"provider": capabilities_dict(runner.provider), "enabled": runner.provider is not None,
                "cloud_enabled": False, "today": totals,
                "note": "Usage is recorded per run. The fixture assistant has no cost. No cloud provider is configured."}

    register_purchasing(app, store, clock)

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
                (SELECT COUNT(*) FROM listings) total_listings,
                (SELECT COUNT(DISTINCT listing_id) FROM matches WHERE status='confirmed') confirmed,
                (SELECT MAX(last_success) FROM watchlists) last_sync,
                (SELECT COUNT(*) FROM runs WHERE status='failed') failed""").fetchone())

    @app.get("/", include_in_schema=False)
    async def dashboard():
        return FileResponse(WEB / "index.html")

    app.mount("/assets", StaticFiles(directory=WEB), name="assets")
    return app


app = create_app()
