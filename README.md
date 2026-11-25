# Marketplace Watcher

Marketplace Watcher is a local, single-user deal monitor. It tracks listings against editable watchlists, stores prices and revisions, queues notifications, and prepares seller messages for manual review.

## Run locally

Use Docker Compose v2, or Python 3.12+ with the commands below. The dashboard and API share one server and origin.

```sh
docker compose up --build -d
docker compose logs -f watcher
```

Open http://127.0.0.1:8000. Compose binds the published port to loopback and stores SQLite in the `watcher-data` named volume. `docker compose down` preserves that volume. `docker compose down -v` removes it.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn watcher.api:app --host 127.0.0.1 --port 8000 --no-proxy-headers
```

Native development uses `data/watcher.sqlite3` unless overridden by `WATCHER_DB`. For Compose configuration, copy `.env.example` to `.env` and edit it before bringing the stack up. For native development, export any settings into the shell that starts Uvicorn. The default console notifier and demo work without credentials. Optional Telegram delivery requires both `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID`. Start a chat with your bot first, then use **Settings → Send test notification**. Use a separate bot or console settings while testing. Set `WATCHER_DASHBOARD_URL` to your dashboard origin to include a listing detail link in notifications.

If a local TLS inspection proxy supplies a CA that the container image lacks, provide a Compose build secret named `pip_ca` through a build override, targeting a trusted CA bundle. The Dockerfile uses it only for package installation.

## Demo

On a fresh database, open **Overview → Add demo watchlists**. The examples are editable and use simulated fixtures. For an isolated demonstration, create only an **8TB HDD** fixture watchlist with phrase `8TB HDD`, maximum `50 CAD`, and the default location. Wait until Activity shows its first successful run, then use **Run now** twice, waiting for each successful run to finish. The default polling interval is five minutes, and manual runs share the same source limits.

1. The first run shows the baseline Seagate drive at CAD 45 and suppresses delivery.
2. The second run introduces a matching WD drive at CAD 40. The duplicate WD record generates one new-match notification. The broken 8GB drive does not qualify, and the missing-price record is available as uncertain.
3. The third run lowers the WD drive to CAD 25. Listings shows the separate revision and Settings shows the price-drop notification.
4. Open the WD details, generate or edit the SMART health draft, save it, and use **Copy draft**. Seller communication remains manual.

## Source capabilities

| Source | Acquisition | Fields and limitations |
| --- | --- | --- |
| Fixture | Scheduled and manual polling, paginated | Deterministic synthetic records with deliberately missing fields and historical posted dates |
| Manual | Dashboard form or JSON | URL required. Enter any known details. The app never fetches the URL |
| Forwarded alert | Authenticated inbound JSON | User-controlled or authorized forwarding. Pass stable IDs, observation times and details where available |
| Facebook Marketplace | Future authorized connector | Live coverage requires appropriate access and a verified supported integration |

Notifications and matches are keyed by source and external ID, with the normalized URL as fallback. Use a consistent external ID strategy for each listing within a source. Manual and alert identity namespaces are intentionally independent.

## API

OpenAPI is available at `/openapi.json` and the interactive API documentation at `/docs`. The dashboard exposes watchlist CRUD, run enqueueing, source capabilities, listing filters and revisions, notification configuration, and delivery history. `/health/live` reports the process, `/health/ready` checks the database, and Overview and Activity report the last successful poll and failures.

```sh
curl -fsS http://127.0.0.1:8000/api/watchlists \
  -H 'Content-Type: application/json' \
  -d '{"name":"Manual HDDs","sources":["manual","alert"],"phrase":"8TB HDD","max_price":"50","location":null}'

curl -fsS http://127.0.0.1:8000/api/ingestion/manual \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com/listing/drive","title":"8TB HDD","price":"39","currency":"CAD"}'
```

Set `INGESTION_SECRET` server-side before using inbound alerts. Pass it in the Authorization header from a trusted client.

```sh
curl -fsS http://127.0.0.1:8000/api/ingestion/alerts \
  -H "Authorization: Bearer $INGESTION_SECRET" \
  -H 'Content-Type: application/json' \
  -d '{"event_id":"forwarder-123","listing":{"external_id":"drive-42","url":"https://example.com/listing/drive","title":"8TB HDD","price":"35","currency":"CAD","observed_at":"2026-01-10T13:10:00Z"}}'
```

Repeated alert IDs with the same listing content return `duplicate: true`. Reuse with different content returns HTTP 409. If omitted, observation time is receipt time. Supply an offset for timestamps, a three-letter uppercase currency when known, and a distance origin alongside distance if you want radius matching. Use zero for free items and omit or use null for unknown prices. Both ingestion endpoints accept a complete observation, so send known details again when reporting a revision.

## Backup and restore

Use SQLite's online backup API for a consistent snapshot while writes continue. Keep the snapshot outside the data directory and protect it as you would the database.

```sh
python -m watcher.backup data/watcher.sqlite3 ./watcher-backup.sqlite3
```

For Compose, run the backup inside the container, then copy the snapshot out. Use unique filenames.

```sh
docker compose exec -T watcher python -m watcher.backup /data/watcher.sqlite3 /tmp/watcher-backup.sqlite3
docker compose cp watcher:/tmp/watcher-backup.sqlite3 ./watcher-backup.sqlite3
```

Stop the application before restoring. Keep the previous database and its WAL/SHM files together in a separate recovery directory, then install the verified snapshot at the original database path. Start the app after confirming file ownership and that no old WAL/SHM files remain at the live path. For Compose, stop the service and use a one-off container against the same named volume.

```sh
docker compose stop watcher
docker compose run --rm -T --no-deps --entrypoint sh watcher -ec '
  umask 077
  mkdir /data/pre-restore
  cat > /data/pre-restore/incoming.sqlite3
  python -c "import sqlite3; c=sqlite3.connect(\"/data/pre-restore/incoming.sqlite3\"); assert c.execute(\"PRAGMA quick_check\").fetchone()[0] == \"ok\"; c.execute(\"SELECT version FROM schema_version\").fetchall()"
  for file in /data/watcher.sqlite3 /data/watcher.sqlite3-wal /data/watcher.sqlite3-shm; do
    if [ -e "$file" ]; then mv "$file" /data/pre-restore/; fi
  done
  mv /data/pre-restore/incoming.sqlite3 /data/watcher.sqlite3
' < ./watcher-backup.sqlite3
docker compose up -d
```

## Design and operating limits

- The empty workspace was scaffolded with FastAPI, numbered SQL migrations, SQLite WAL, and a dependency-free browser dashboard. These replace the suggested .NET, EF Core and Next.js defaults. `watcher/core.py` owns validation, contracts and matching. `watcher/providers.py` owns source metadata and delivery. `watcher/store.py` owns database transactions. `watcher/api.py` and `watcher/worker.py` own the HTTP and background entry points.
- Use one process and one local SQLite database. The poller runs one fixture check at a time, spaces checks, and persists due times, source pauses and backoff. The fixture stage advances monotonically across watchlists. The browser shows latest poll status on refresh. Provider support beyond fixture polling needs provider-specific scheduling configuration and verification.
- A transaction records listing, revision, match and outbox changes together. Late observations can extend history without rolling back the current listing. Initial fixture baseline behavior is configured per watchlist; a listing that already has a deduplication key stays deduplicated across watchlists and restarts. Scheduled poll times are best effort.
- Positive phrases use normalized, contiguous token sequences. Include terms are ANDed and any exclusion rejects. Radius uses supplied distances from the watchlist's named origin. The app performs no geocoding or currency conversion. Match age and freshness are calculated at observation or watchlist edit time. A score is a rule-based heuristic.
- Outbox delivery is at-least-once if a delivery attempt succeeds remotely but its acknowledgement is lost. Terminal delivery records and ingress keys retain deduplication identity after history pruning. Listing identities, current values and user state remain until database management. One worker is expected.
- The local dashboard and administrative APIs use loopback access. Before remote use, put the service behind a trusted reverse proxy with mandatory single-user authentication over HTTPS for the dashboard and administrative APIs. Keep the app port private, preserve the expected Host, set `WATCHER_ALLOWED_HOSTS` to the public hostname and `WATCHER_ORIGIN` to the HTTPS origin. Route alert forwarding only through its bearer-authenticated endpoint and enforce gateway request limits. Deployment and authentication wiring on the target host are future work.
- Live Telegram delivery needs credentials and an operator smoke test. Live marketplace coverage depends on authorized provider access. No host deployment, resource benchmark, or full accessibility audit was performed.

## Tests

```sh
python -m compileall -q watcher
python -m unittest discover -s tests -v
node --check web/app.js
docker compose config --quiet
```

The frontend consists of static HTML, CSS and JavaScript, so it has no bundling step. An optional Chromium smoke test exercises the dashboard against an empty, disposable running instance. Install Playwright outside the project or in a temporary Node environment and make it resolvable through `NODE_PATH`.

```sh
WATCHER_TEST_ORIGIN=http://127.0.0.1:8000 node tests/browser-smoke.cjs
```

Set `CHROMIUM_PATH` to use a system Chromium instead of Playwright's bundled browser. See `VERIFICATION.md` for observed checks and results.
