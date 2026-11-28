# Implementation status and traceability register

Living document. Requirement IDs refer to the *Engineering Handbook, Edition 2.0*. Decisions and deviations are in
[`DECISIONS.md`](DECISIONS.md). Last updated 2026-11-28.

Status values: **Done** (implemented, tested, results below) · **Partial** (gap named) · **Not started** ·
**Blocked** (needs external access, credentials or an owner decision) · **Conflict** (intentional, see ADR).

> **Nothing here is production-ready.** There is no authentication for remote use, no live marketplace source, no
> cloud or local language model, and no deployment. The assistant is a deterministic template-based fixture, not an
> AI model; it exists so the chat workflow, persistence, streaming and safety controls can be built and tested
> without credentials or cost.

## Gate A0 — Repository inventory (2026-11-28)

| Item | Finding |
|---|---|
| Stack | Python 3.12, FastAPI, Pydantic v2, SQLite (WAL) via `sqlite3`, numbered SQL migrations, static HTML/CSS/JS. **Conflict** with the .NET/Next.js default; owner approved keeping Python (ADR-02). |
| Modules | `core.py` contracts/matching/score/drafts · `providers.py` sources/notifiers · `store.py` migrations/ingest · `worker.py` scheduler/outbox · `api.py` HTTP · `assistant.py` chat (B1) · `purchasing.py` projects/inventory/memory (B2) · `calculations.py`, `comparisons.py` (C1) · `errors.py` · `backup.py` |
| Baseline before this work | compileall passed; **13** unit/integration tests passed; Chromium smoke passed |
| CI | None existed. Added `.github/workflows/ci.yml` (read-only permissions; compile, unit/integration tests, syntax checks, compose config). Browser tests are not in CI (they need Playwright and a running server). |

## Gate A1 — Baseline watcher (Part II)

| Req | Status | Evidence / gap |
|---|---|---|
| 4.1 Watchlists | Done | `test_hard_constraints_and_unknowns`, `test_watchlist_routes_and_typed_errors` |
| 4.2 Ingestion / provider contract | Done | Fixture, manual, authenticated idempotent alerts; `test_alerts_manual_drafts_and_history` |
| 4.3 Scheduler | Done | Fake-clock tests: interval, overlap, timeout, backoff, Retry-After, pause, cancel, restart |
| 4.4 Matching / dedup / history | Done | Revisions, cross-watchlist dedup, newly qualifying, price drops, silent baseline |
| 4.5 Deterministic score | Done | Breakdown and unknowns shown |
| 4.6 Notifications | Partial | Outbox, retry, console notifier tested. Live Telegram **unverified** (credentials). |
| 4.7 Seller messages | Done | Templates, edit/copy, never sent. Note: HDD template keys on "HDD"/"hard drive" in the title only. |
| 4.8 Dashboard | Partial | All screens. Automated axe scans of new screens pass (serious/critical); no screen-reader test. |
| 5 Persistence | Done | Unique keys; transactional listing+match+outbox |
| 6 API | Done | Problem Details now carry `correlation_id` everywhere and a stable `code` on new routes and validation errors. Baseline 404/409 messages have no `code` yet. |
| 7 Security | Partial | Loopback, Host allowlist, Origin/Sec-Fetch CSRF guard, 32 KiB body limit, bearer ingestion, CSP. **No authentication** for remote use (gate D1). |
| Retention | Done (fixed) | Prune no longer deletes a listing's current revision (ADR-13); `test_prune_keeps_current_revision` |

## Gate B1 — Listing chat (first priority)

| Req | Status | Evidence |
|---|---|---|
| CHAT-01 persistent chat, automatic context | Done | `test_reply_grounded_in_stored_revision_and_seller_note` (cites `listing:N@obs:M`, price, seller note) |
| CHAT-02 split view / mobile tabs keep draft | Done | `browser-chat.cjs` at 390 px and 1280 px |
| CHAT-03 listing fields, unknowns, missing price ≠ free | Done | `test_unknown_model_and_missing_price`, detail view |
| CHAT-04 stream, stop, retry, copy, new, rename, archive, delete, resume | Done | `test_cancel_then_timeout`, `test_scope_archive_and_idempotency`, `test_restart_preserves_turns_and_marks_interrupted_partial`, browser copy |
| CHAT-05 context drawer, remove sources | Done | `test_excluded_context_not_sent`, browser persistence of exclusion |
| CHAT-06 explicit scope | Done | `scope_mismatch` for listing and project; switching project is an explicit action |
| CHAT-07 evidence, limitations, next questions | Partial | Fixture replies cite refs, list unknowns and seller questions. Offer suggestions are not produced (no assessment model). |
| CHAT-08 seller updates with origin/time | Partial | Text updates done (`test_seller_update_revises_answer`, original listing untouched). **Screenshots/files not started** (RES-08). |
| CHAT-09 project-level general chat | Not started | Conversations are listing-scoped; comparisons are deterministic tables without chat. |
| CHAT-10 works without AI | Done | `test_listing_features_work_without_assistant` (`ASSISTANT_PROVIDER=none`) |
| AI-03 bounded context | Done | `test_context_budget_prefers_latest_evidence` |
| AI-06 versioned prompt | Partial | `prompt_version` stored per run and snapshot; no prompt regression suite for a real model |
| T-04 / T-05 / T-06 | Done | ProBox grounding; 80k-hours update; unknown model asks a question |
| T-09 injection | Done (server controls) | `test_prompt_injection_has_no_side_effects`: no table changes, delimiter cannot be closed, rules precede untrusted text, secret absent. A real model's behaviour is **untested**. |
| T-10 provider failure | Done | quota failure with partial text; timeout |
| T-11 reconnect/retry/cancel/restart/idempotency | Done | SSE `Last-Event-ID` resume, idempotent `request_key`, recovery |

## Gate B2 — Projects, inventory, memory

| Req | Status | Evidence |
|---|---|---|
| PROJ-01 CRUD + requirements | Done | `test_project_requirements_and_budget_rules` |
| PROJ-02 hard vs weighted | Partial | Validated (hard = pass/fail, weights ≤ 100). Hard checks run only in comparisons and only for derivable attributes (C1). |
| PROJ-03 committed / actual / remaining | Partial | Actual from confirmed purchases; other currencies listed unconverted. **Committed is "not tracked"** until offers (NEG-03). |
| PROJ-04 archive keeps shared records | Done | `test_spend_purchases_and_archive` |
| PROJ-05 budget change needs reason | Done | PATCH refuses; `POST /budget` records reason and audit |
| PROJ-06 component vs total cost | Partial | Budget calculator; comparisons label taxes/shipping unknown. No per-component project costing. |
| INV-01 typed equipment, private serial | Done | Serial hidden unless `reveal_serial`; never in chat context or audit |
| INV-02 allocation limits with override | Done | `test_inventory_privacy_and_allocation_limits` |
| INV-03 purchase ≠ inventory | Done | Purchase requires `confirm: true` and creates no inventory |
| INV-04 compatibility with evidence/scope | Not started | Chat states compatibility is unknown; no compatibility records |
| INV-05 units | Partial | TB vs TiB in calculator; SATA/SAS etc. not modelled |
| INV-06 typed core + versioned attributes | Done | `attributes` JSON + `attributes_schema` (ADR-14) |
| Memory (section 15) | Done | Suggested → approve; correct supersedes; reject; delete; export. `test_memory_lifecycle` |
| T-08 project switch / corrected memory | Done | `test_project_switch_and_memory_correction_in_chat` |
| Audit events | Done | `GET /api/audit`; project, inventory, purchase, memory, comparison actions |

## Gate B3 — Configurable AI provider

| Req | Status |
|---|---|
| AI-01 cloud adapter, server credentials, retry, circuit | **Blocked** — owner approval, provider choice and spend cap required (ADR-09) |
| AI-02 usage and caps | Partial — tokens and zero cost recorded per run; `GET /api/assistant/usage`. No caps (nothing billable exists). |
| AI-04 opt-in enrichment during polling | Not started (model never runs on polls) |
| AI-05 structured output validation | Not started |
| Vision / image consent | Not started |
| T-17 cost limit | Not started (no billable provider) |

## Gate C1 — Comparisons, calculations, research

| Req | Status | Evidence |
|---|---|---|
| CMP-01 2–5 listings, project, side by side | Done | `test_reference_comparison_under_10tb_goal`, `browser-comparisons.cjs` |
| CMP-02 bundles, no assumed drives | Done | Explicit `N x SIZE` detection; otherwise "assume none" |
| CMP-03 snapshots, history, refresh | Done | Immutable snapshots, stale reasons, explicit recompute; `test_validation_staleness_and_history` |
| CMP-04 calculator, tool use | Done | Storage/energy/budget APIs; chat uses calculator output `[calc:storage]`; T-18 exact cases |
| T-07 compare under 10 TB goal | Done | Project-specific outcomes with uncertainty and cost basis |
| SCORE-01..05 versioned project assessment | Partial | Comparison outcomes are versioned (`comparison/1`) and go stale; no weighted 0–100 project score or offer range |
| RES-01 manual model/specs | Partial | Seller claims and notes; inventory specs. No structured listing spec extraction. |
| RES-02 extraction with provenance | Not started |
| RES-03..07 web research | **Blocked** — no approved search tool |

## Gate C2 / D

| Req | Status |
|---|---|
| NEG-01..06 offers, counteroffers, sent-text confirmation | Not started (drafts remain baseline templates; purchases are explicit) |
| Intelligent notifications | Not started |
| D1 deployment, auth, measured resources | Not started by instruction; no host changes |
| D2 local model, extra sources | Not started |

## Known issues and limitations

- Browser tests run against a live server and are manual (not in CI).
- Docker image build and named-volume persistence were **not re-verified** in this session: no Docker daemon was
  available. They were verified in an earlier session (see `VERIFICATION.md`); the Dockerfile is unchanged.
- No performance measurements against the handbook's proposed targets.
- SSE endpoint polls SQLite every 100 ms per open stream; fine for one user, not measured under load.
- The money type assumes two decimal places for every currency.
- Problem Details on baseline routes lack a stable `code`.

## Verification log

| Date | Command | Result |
|---|---|---|
| 2026-11-28 | `python -m unittest discover -s tests` (baseline) | 13 passed |
| 2026-11-28 | `node tests/browser-smoke.cjs` (baseline) | Passed |
| 2026-11-28 | `python -m compileall -q watcher` | Passed |
| 2026-11-28 | `python -m unittest discover -s tests` | **37 passed** (3 consecutive runs green for the B1 suite) |
| 2026-11-28 | `node --check` on `web/app.js` and `tests/*.cjs` | Passed |
| 2026-11-28 | `docker compose config --quiet` | Passed |
| 2026-11-28 | `browser-smoke.cjs`, `browser-chat.cjs`, `browser-projects.cjs`, `browser-comparisons.cjs` (Chromium, 390 px; chat also 1280 px; axe-core 4.10.2 serious/critical scans) | All passed, zero axe violations |
| 2026-11-28 | Upgrade of a database created by commit `090187c` (schema 1–4) to schema 1–7, then online backup | Data preserved; `foreign_key_check` empty; `integrity_check` ok; backup `quick_check` ok |
| 2026-11-28 | `docker compose build` | **Not executed** — no Docker daemon in the environment |
