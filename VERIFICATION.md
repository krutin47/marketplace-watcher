# Local verification

| Check | Observed result |
| --- | --- |
| `python3 -m compileall -q watcher` | Passed |
| `python3 -m unittest discover -s tests -v` | 12 passed, including scheduler timing with an injected clock, timeout and cancellation, access pause and Retry-After, normalized matching, baseline suppression, cross-watchlist deduplication, stale observations, authenticated idempotent ingestion, and retry after acknowledgement loss |
| Unit and integration tests run with the built image's installed dependencies | 12 passed |
| `node --check web/app.js` and `node --check tests/browser-smoke.cjs` | Passed |
| `node tests/browser-smoke.cjs` with headless Chromium | Passed at 390px. Verified watchlist creation, empty and recoverable error states, manual import, draft generation, edit, copy and persistence, listing state, and test notification enqueue |
| `docker compose config --quiet` and Compose build/start | Passed. The runner's TLS inspection required a CA bundle passed using the optional BuildKit secret. The service ran as a non-root user and reached healthy |
| Fixture end to end through the running container | One suppressed baseline, one new alert, one separate price-drop alert, and WD drive revisions CAD 40 and CAD 25 |
| Console notifier through the running container | A queued test notification appeared in Compose logs |
| Compose forced recreation | Watchlists, revisions, and delivery state persisted on the named volume |
| SQLite backup and restore with Compose | Online snapshot passed SQLite quick check. Restore in the disposable Compose project recovered the expected fixture listing |

The test runner emitted a Starlette warning about future migration from HTTPX in TestClient. All tests passed. Scheduler tests advance injected time to cover the five-minute interval without a five-minute wall-clock wait. The running service exercised a scheduled initial poll and two manual reruns.

Live Telegram delivery, a live marketplace connector, a complete accessibility audit, and performance measurements remain unverified. Proxmox deployment was outside the local milestone.
