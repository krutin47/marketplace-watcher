# Implementation status and traceability register

Living document. Requirement IDs refer to the *Engineering Handbook, Edition 2.0*. Decisions and deviations are in
[`DECISIONS.md`](DECISIONS.md). Status values:

- **Done** — implemented, tested, results recorded below.
- **Partial** — part of the acceptance evidence exists; the gap is named.
- **Not started** — no implementation.
- **Blocked** — needs external access, credentials or owner decision.
- **Conflict** — the repository intentionally differs from the handbook (see ADR).

Nothing here is production-ready: there is no remote authentication, no live marketplace source, no cloud AI
provider and no deployment. The assistant is a deterministic fixture, not a language model.

## Gate A0 — Repository inventory (2026-11-28)

| Item | Finding |
|---|---|
| Stack | Python 3.12, FastAPI 0.141, Uvicorn, Pydantic v2, SQLite (WAL) via `sqlite3`, numbered SQL migrations, static HTML/CSS/JS. **Conflict** with ADR-02 default (.NET/EF Core/Next.js); owner approved keeping Python (DECISIONS ADR-02). |
| Layout | `watcher/core.py` (contracts, matching, score, drafts), `providers.py` (fixture source, Telegram/console), `store.py` (migrations, transactional ingest), `worker.py` (scheduler, outbox), `api.py` (HTTP, guards), `backup.py`, `web/`, `tests/`. Handbook's `.sln` layout does not apply. |
| Baseline build/test | `compileall` passed; `unittest` **13 passed**; `node --check` passed; Chromium smoke **passed**. |
| CI | None in repository. |
| Existing completed work | Phase A watcher, see table below. |

## Gate A1 — Baseline watcher (Part II)

| Req | Status | Evidence / gap |
|---|---|---|
| 4.1 Watchlists | Done | CRUD, validation, run-now, seed demo, unknown-not-free; `test_hard_constraints_and_unknowns`, `test_watchlist_routes_and_typed_errors` |
| 4.2 Ingestion / provider contract | Done | `IListingSource`, fixture, manual, authenticated idempotent alert endpoint; `test_alerts_manual_drafts_and_history` |
| 4.3 Scheduler | Done | Fake-clock tests for interval, overlap, timeout, backoff, Retry-After, pause, cancel, restart spread |
| 4.4 Matching / dedup / history | Done | Revisions, cross-watchlist dedup, newly qualifying, price drops, silent baseline |
| 4.5 Deterministic score | Done | Breakdown and unknowns shown |
| 4.6 Notifications | Partial | Outbox, retry, console notifier tested. Live Telegram delivery **unverified** (needs credentials). |
| 4.7 Seller messages | Done | Category templates, edit/copy, no sending |
| 4.8 Dashboard | Partial | All baseline screens. No full accessibility audit; no automated a11y checker. |
| 5 Persistence | Done | Unique keys, transactional listing+match+outbox |
| 6 API | Partial | All baseline routes. Problem Details lack stable machine `code` and correlation ID on baseline routes (added for new routes). |
| 7 Security | Partial | Loopback binding, Host allowlist, Origin/Sec-Fetch CSRF guard, 32 KiB limit, bearer ingestion, CSP. **No authentication** for remote use (D1). |
| 8 Acceptance | Partial | All automated items pass; Telegram and Compose recreation verified only in earlier session (VERIFICATION.md). |
| Retention | Done (fixed) | Prune previously deleted a listing's only revision; now keeps latest (ADR-13) |

## Phase B/C checklist

Priority order per owner: listing chat → projects → inventory → memory → comparisons → research.
Columns: **Dep** = dependencies; **Acceptance** = handbook evidence; **Test** = verifying test.

### B1 — Listing chat vertical slice

| Req | Dep | Acceptance | Test | Status |
|---|---|---|---|---|
| CHAT-01 Persistent chat from a listing; context attached automatically | A1 | Reply grounded in exact stored revision | `ChatTests.test_reply_grounded_in_stored_revision_and_seller_note` | Planned |
| CHAT-02 Desktop split view; mobile Details/Chat tabs retain draft | CHAT-01 | Narrow-screen test, no lost content | `browser-smoke.cjs` chat section | Planned |
| CHAT-03 Show listing fields, seller notes, unknowns, stale labels | A1 | Missing price never free | existing `money()` + smoke | Planned |
| CHAT-04 Streaming, stop, retry, copy, new, rename, archive, delete, resume | CHAT-01 | Reload preserves turns; partial labeled | `test_cancel_and_retry`, `test_restart_marks_interrupted_partial` | Planned |
| CHAT-05 Context drawer; user can remove optional sources | CHAT-01 | Removed source excluded from next request | `test_excluded_context_not_sent` | Planned |
| CHAT-06 Explicit listing scope | CHAT-01 | Mismatched scope rejected | `test_scope_mismatch_rejected` | Planned |
| CHAT-07 Evidence, assumptions, limitations, next questions | CHAT-01 | Unknown model → question | `test_unknown_model_asks_question` | Planned |
| CHAT-08 Seller updates with origin and received time | Evidence | 80k hours update revises answer; original listing untouched | `test_seller_update_revises_answer` | Planned (text only; screenshots/files not started) |
| CHAT-09 Project-level general chat | B2 | Compare offers, reopen after restart | — | Not started |
| CHAT-10 Works without AI | — | Provider unavailable test | `test_provider_disabled_listing_still_works` | Planned |
| AI-03 Context caps / truncation marker | CHAT-01 | Bounded context | `test_context_budget_truncates` | Planned |
| T-09 Injection regression | CHAT-01 | No secret, tool or mutation | `test_prompt_injection_has_no_side_effects` | Planned |
| T-10 Provider timeout / failure | CHAT-01 | Visible error, no fake success | `test_provider_failure_visible` | Planned |
| T-11 Reconnect, retry, cancel, restart, idempotency | CHAT-01 | No duplicate turn | `test_idempotent_submission` | Planned |

### B2 — Projects, inventory, memory

| Req | Dep | Acceptance | Status |
|---|---|---|---|
| PROJ-01..06 | A1 | CRUD, hard vs soft, budget committed/actual/remaining, archive, override with reason | Not started |
| INV-01..06 | PROJ | Typed equipment, allocation limits, purchase ≠ listing, compatibility with scope | Not started |
| MemoryFact (§15) | PROJ, INV | Suggested → approved by user only; correction, deletion, export | Not started |
| Project context in chat | PROJ, CHAT-01 | T-08 no stale facts after project switch | Not started |

### B3 — Configurable AI provider

| Req | Status |
|---|---|
| AI-01 server credentials, timeout, retry, circuit | Blocked — needs owner approval and provider choice |
| AI-02 usage, cost, caps | Partial target — tokens recorded per run; no cost cap needed for zero-cost fixture |
| AI-05 structured JSON validation | Not started |
| Vision handling | Not started |

### C1 / C2 — Comparisons, research, assessments, negotiation

| Req | Status |
|---|---|
| CMP-04 storage/energy/budget calculator (T-18) | Not started |
| CMP-01..03 comparison snapshots | Not started |
| SCORE-01..05 project assessment | Not started |
| RES-01..02 manual model/specs | Not started |
| RES-03..07 web research | Blocked — no approved search tool |
| NEG-01..06 offers/purchases | Not started |

### D — Deployment

Not started by instruction. No host changes.

## Verification log

| Date | Command | Result |
|---|---|---|
| 2026-11-28 | `python -m unittest discover -s tests` (baseline) | 13 passed |
| 2026-11-28 | `node tests/browser-smoke.cjs` (baseline) | Passed |
