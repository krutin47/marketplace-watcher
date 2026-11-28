# Architecture decision register

Canonical requirements: *Marketplace Watcher Production Architecture & Engineering Handbook, Edition 2.0*.
This register records how the handbook's decisions apply to the actual repository. Each entry gives status,
date, owner, rationale, alternatives, impact and rollback. Implementation status lives in
[`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md).

| ID | Decision | Status | Date |
|---|---|---|---|
| ADR-01 | Modular monolith, single user, single backend process | Retained | 2026-11-28 |
| ADR-02 | Python 3.12 / FastAPI / SQLite (numbered SQL migrations) / dependency-free browser UI instead of .NET 10 / EF Core / Next.js | **Deviation, owner-approved** | 2026-11-28 |
| ADR-03 | Fixture and manual imports first; live sources only with verified authorized capability | Retained | 2026-11-28 |
| ADR-04 | Provider-neutral assistant adapter; deterministic fixture provider is the default | Accepted | 2026-11-28 |
| ADR-05 | Database is authoritative; model output is advisory and evidence-linked | Accepted | 2026-11-28 |
| ADR-06 | Human controls messages, offers, purchases and durable memory changes | Accepted | 2026-11-28 |
| ADR-07 | Local development first; Dell/Proxmox deployment after hardware inspection | Retained | 2026-11-28 |
| ADR-08 | No vector DB or microservices; SQLite indexed filters first | Accepted | 2026-11-28 |
| ADR-09 | Cloud vs local model selected per task; cloud disabled until configured | Accepted (no cloud adapter yet) | 2026-11-28 |
| ADR-10 | CAD default, UTC audit timestamps (epoch seconds in SQLite), browser-local display | Retained | 2026-11-28 |
| ADR-11 | Durable assistant runs with persisted, coalesced stream events | Accepted | 2026-11-28 |
| ADR-12 | Assistant replies rendered as plain text, not markdown | Accepted | 2026-11-28 |
| ADR-13 | Retention pruning keeps the latest observation per listing | Accepted | 2026-11-28 |
| ADR-14 | Purchasing records stored as typed columns plus a versioned JSON `attributes` column | Accepted | 2026-11-28 |
| ADR-15 | Comparisons evaluate only derivable hard requirements; no weighted project score yet | Accepted | 2026-11-28 |
| ADR-16 | GitHub Actions CI with read-only permissions; browser tests stay manual | Accepted | 2026-11-28 |

## ADR-02 — Keep the Python stack (deviation from the handbook default)

- **Context.** The handbook recommends .NET 10 / EF Core / Next.js but marks it "not mandatory if the existing
  repository has good equivalents" and forbids framework migration without a documented reason. The repository
  already implements Phase A in Python 3.12, FastAPI, Pydantic validation, raw SQL migrations on SQLite (WAL) and
  a static HTML/CSS/JS dashboard, with 13 passing tests and a Chromium smoke test.
- **Decision.** Keep the existing stack. The owner confirmed on 2026-11-28: "it is fine if we use python as well.
  Choose what is best for this app."
- **Rationale.** No rebuild of working, tested features. One small process fits the 2 vCPU / 2–4 GB target better
  than a .NET API plus a Node server. Pydantic gives the boundary validation the handbook requires; FastAPI
  generates OpenAPI. No bundler or `node_modules` keeps the supply-chain surface small.
- **Alternatives.** (a) Migrate to .NET/Next.js: high cost, regression risk, no measured benefit. (b) Add a React
  build to the existing backend: rejected until a measured UI complexity need exists.
- **Equivalences used for handbook rules.** EF Core migrations → `watcher/migrations/NNN_*.sql` applied in a
  transaction and recorded in `schema_version`. C# nullable/typed DTOs → Pydantic `Contract` models with
  `extra="forbid"`. `TimeProvider` → injected `clock` callables. xUnit → `unittest`. TypeScript strict API client →
  not available; the dashboard is plain JS that renders only through `textContent`/DOM APIs (see ADR-12).
- **Impact.** Handbook items written in .NET terms (§13 C# conventions, "typed API client generated from OpenAPI")
  are met by the equivalents above or recorded as gaps in the status document.
- **Rollback.** Not applicable; a future migration would need its own ADR.

## ADR-04 / ADR-09 — Assistant provider contract and routing

- `watcher/assistant.py` defines `AssistantProvider` (capabilities: streaming, vision, structured output, tools,
  context limit, `cloud` flag; async streaming; usage reporting; cancellation through task cancellation).
- The only shipped provider is `FixtureAssistantProvider`: deterministic, offline, zero cost, and grounded in the
  structured context packet. It is clearly labeled "Fixture assistant (offline, deterministic)" in the UI. It is
  **not** a language model and its replies are template-based.
- `ASSISTANT_PROVIDER=fixture` (default) or `none` (assistant disabled; listing UI still works, CHAT-10).
- No cloud adapter is implemented. Adding one (gate B3) requires owner approval, server-side credentials, a spend
  cap and a separate ADR. There is no silent fallback between providers.

## ADR-05 / ADR-06 — Authority and human control

- The assistant has no write path. Runs can only append their own assistant message and run metadata. No tool
  calls, listing-state changes, outbox events, memory facts or purchases can originate from a run. Tests assert this
  for a prompt-injection fixture (T-09).
- Memory facts proposed by anyone other than the user enter as `suggested` and require an explicit approve action.

## ADR-11 — Durable assistant runs and streaming

- **Decision.** `POST /api/conversations/{id}/messages` creates the user message and an `assistant_runs` row in one
  transaction (idempotent on `request_key`) and returns `202` with the run ID. An in-process runner executes the
  provider outside any DB transaction. Text deltas are coalesced (≈250 ms or 400 chars) into `run_events` rows;
  `GET /api/assistant/runs/{id}/events` serves them as SSE with monotonic `id:` values and honors `Last-Event-ID`.
  The final assistant message, token usage and completion event are written atomically.
- **Rationale.** Reload and reconnect work without in-memory state; partial output survives a crash and is labeled.
  Coalescing keeps SQLite writes bounded.
- **Alternatives.** In-memory pub/sub with only final persistence: simpler, but loses partial text on restart and
  breaks reconnect. WebSockets: unnecessary for one-way streaming.
- **Recovery.** At startup, `queued`/`running` runs become `interrupted`, and any streamed text is stored as a
  `partial` assistant message. Runs are never marked complete on recovery.
- **Rollback.** Drop the runner; the tables are additive.

## ADR-12 — Plain-text assistant output

Assistant text is untrusted (handbook §16). The UI renders it with `textContent` and `white-space: pre-wrap`; no
markdown or HTML is interpreted, and model-suggested URLs are not turned into links. Revisit only with a vetted
sanitizer and an allowlist.

## ADR-13 — Retention keeps the current revision

The previous prune deleted every observation older than `RETENTION_DAYS`, including a listing's only (current)
revision. Context snapshots reference observation IDs, so pruning now keeps the newest observation per listing.

## ADR-14 — Typed columns plus versioned attributes

Projects, requirements and inventory store queried fields as columns, and category-specific detail in a JSON
`attributes` column with `attributes_schema` version (handbook INV-06). Optimistic concurrency uses an
app-managed `version` integer; stale updates return 409.

## ADR-15 — Narrow, honest requirement evaluation in comparisons

- **Decision.** `comparison/1` evaluates a hard requirement only when the value is derivable from stored data:
  usable capacity from an explicitly stated drive bundle (via the calculator, best-redundancy layout first) and asking
  price in the same currency. Everything else is reported as `unknown` with a reason. Outcomes are
  `blocked by a hard requirement`, `needs evidence`, `candidate` or `no hard requirements set`, never "buy".
- **Rationale.** Handbook SCORE-01/02 forbid treating missing evidence as a pass and require hard constraints to
  dominate price. A weighted 0–100 project score without evidence for most components would mostly show "insufficient
  evidence", so it is deferred until structured spec extraction (RES-02) exists.
- **Rollback.** Snapshots record `algorithm_version`; a future `comparison/2` can coexist with old snapshots.

## ADR-16 — Continuous integration

GitHub Actions runs compile, unit/integration tests, JS syntax checks and `docker compose config` with
`permissions: contents: read`. Browser tests need a running server and Playwright, so they stay manual and are
recorded in the status document. No deployment step exists.

## Open decisions (owner input required)

| Topic | Needed for |
|---|---|
| Authorized marketplace access (Facebook, Kijiji, eBay) | Live sources |
| Cloud model/provider, spending cap, data-sharing terms | Gate B3 |
| Retention defaults for chats, seller notes and photos | Privacy settings |
| Remote authentication method (reverse proxy vs built-in) | Gate D1 |
| Dell OptiPlex hardware / Proxmox inspection | Gate D1 |
| Research/search tool provider | RES-03 |
