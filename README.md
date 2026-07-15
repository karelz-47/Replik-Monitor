# Replik monitor — deployable Railway artifact

This is one self-contained Python service for a configured public Replik SOAP endpoint. It contains no credentials, sender/recipient values, Railway target, deployment command, or scheduled job creation. Deployment and outbound-email approval remain human operations. A mercifully small boundary between code and regret.

## Safety controls

- `ReplikSoapClient` makes the configured official SOAP/API request; endpoint/action remain environment-only because endpoint contracts vary by approved Replik integration.
- PostgreSQL owns migrations, an advisory poll lock, source-identity deduplication, durable outbox retry, and idempotency keys for Resend.
- The first successful poll transactionally claims `initial_baseline_started_at`, stores its changes, checkpoint, and (when non-empty) one link-first historical outbox digest. Later polls wait while that durable digest is pending or retrying; they cannot create a second historical digest. A successful delivery writes `initial_baseline_complete_at`, after which polling is incremental-only. An empty successful baseline writes that completion marker in the same transaction, so it immediately and durably enters incremental mode.
- `changes.source_id` is the external identity cursor and `changes.ingest_id` is the durable insertion cursor; overlap polling cannot resend an existing identity.
- `MONITOR_EXPIRES_AT` must be future and within 31 days. Its first accepted value is persisted in PostgreSQL, so changing an environment variable cannot extend an active monitor. Poll and delivery stop at expiry.
- `/healthz` returns 200 when the configured database is reachable and the monitor is not expired, even before the first cron checkpoint; it returns 503 for database failure, expiry, or invalid configuration. `/checkpoint` exposes a separate `checkpoint_status` (`missing`, `fresh`, `stale`, or `unknown`) for scheduler monitoring.

## Required configuration names

`DATABASE_URL`, `REPLIK_ENDPOINT`, `RESEND_API_KEY`, `RESEND_FROM`, `ALERT_TO`, `MONITOR_EXPIRES_AT`, `MONITOR_HISTORICAL_SINCE`

Optional configuration names: `REPLIK_SOAP_ACTION`, `MONITOR_HISTORICAL_BATCH_LIMIT` (default `100`, range 1..500), `POLL_OVERLAP_MINUTES` (default `10`), `MONITOR_STALE_AFTER_MINUTES` (default `90`), `DRY_RUN` (default `false`), `PORT` (default `8080`). Values belong only in the approved Railway environment, never in this artifact, tickets, shell history, or logs.

## Commands

Run migration once against the approved database:

```sh
python -m replik_monitor.cli migrate
```

Configure independent Railway Cron jobs at `*/30 * * * *` in this order:

```sh
python -m replik_monitor.cli poll
python -m replik_monitor.cli deliver
```

The web service command is the Docker default:

```sh
python -m replik_monitor.cli serve
```

### Cold-start sequence

1. Run the migration against the approved database, configure the required environment, and start the web service.
2. Railway probes `/healthz`. A reachable database produces HTTP 200 with `checkpoint_status: "missing"`; this is intentional and permits deployment before the first scheduler run.
3. Run the 30-minute `poll` cron. It writes the first checkpoint only after the historical baseline transaction succeeds.
4. Monitor `/checkpoint` independently: `missing` is expected only during step 2, while `stale` after the scheduled interval is a scheduler/operational alert rather than a web-process readiness failure.

Use `DRY_RUN=true` for the approved preflight poll. It makes the SOAP request but writes no database row and creates no email. It does not call Resend; do not invoke `deliver` until sender and recipient are verified and approved.

## Local verification without external effects

```sh
python -m unittest discover -s tests -v
python -m compileall -q replik_monitor tests
```

Tests use SOAP fixture parsing, fake integration seams, and static artifact checks. They do not connect to PostgreSQL, Replik, Railway, or Resend.

## Operations

- Investigate a 503 response from `/healthz` before assuming the scheduler is healthy. `/checkpoint` distinguishes missing, fresh, stale, and expired state.
- To stop: disable both cron jobs, wait one overlap interval, inspect `outbox` pending/failed rows, then stop the web service and revoke/remove approved environment values. Retain/delete the database only under its approved retention policy.
- The `sending` state intentionally retries after an interrupted process; Resend receives the durable outbox ID as `Idempotency-Key`, so it can de-duplicate a recovered delivery.
