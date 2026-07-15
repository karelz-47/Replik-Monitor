# Replik monitor — official public SOAP client, Railway artifact

This is a bounded Python monitor for the official public IS REPLIK SOAP 1.1 contract. It contains no credentials, sender/recipient values, Railway target, deployment command, or scheduled-job creation. Deployment and outbound-email approval remain human operations. Naturally, the difficult part is not a Dockerfile.

## Official contract verified on 2026-07-15

- Production WSDL: `https://replik-ws.justice.sk/ru-verejnost-ws/konanieService.wsdl`
- Production SOAP endpoint: `https://replik-ws.justice.sk/ru-verejnost-ws/`
- Binding: SOAP 1.1 document/literal, `Content-Type: text/xml; charset=utf-8`, `SOAPAction: ""`.
- Sync request: `vyhladajPoslednuZmenuOdRequest` with `ZmenyOd` (`xsd:dateTime`) and `MaximalnyPocetVysledkov` (`xsd:int`).
- Sync response: `PoslednaZmenaNaKonani` attributes `KonanieId` and `PoslednaZmena`.
- Detail request: `getKonanieDetailRequest` with `KonanieId`; debtor ICO is read from `Konanie/Dlznik/Ico`.

The client makes the bounded sync request, then calls `getKonanieDetail` for each returned proceeding so it can filter on the configured monitor's debtor ICO (`47251301`). It uses `KonanieId` as the durable source identity and the API's timezone-qualified `PoslednaZmena` as the change timestamp.

## Safety controls

- PostgreSQL owns migrations, an advisory poll lock, source-identity deduplication, durable outbox retry, and idempotency keys for Resend.
- `MONITOR_HISTORICAL_BATCH_LIMIT` is passed to the API as `MaximalnyPocetVysledkov` (default `100`, range `1..500`), keeping first and later fetches bounded. If the unfiltered official sync response reaches that exact cap, the poll fails closed with a redacted `sync-overflow` status before any checkpoint, baseline, outbox, or new delivery state can advance; narrow the time window or raise an approved limit before retrying.
- The first successful poll transactionally claims one historical baseline and at most one historical digest. Later polls wait while it is pending/retrying; they cannot create a second historical digest.
- `changes.source_id` is the external `KonanieId`; overlap polling cannot resend an existing identity.
- `MONITOR_EXPIRES_AT` must be future and within 31 days. Its first accepted value is persisted, so changing an environment value cannot extend an active monitor.
- `/healthz` returns 200 only when the database is reachable and the monitor is active. `/checkpoint` separates scheduler freshness from web readiness.

## Configuration

Required names:

`DATABASE_URL`, `RESEND_API_KEY`, `RESEND_FROM`, `ALERT_TO`, `MONITOR_EXPIRES_AT`, `MONITOR_HISTORICAL_SINCE`

Optional names:

- `REPLIK_ENDPOINT`: defaults to the verified official production SOAP endpoint above.
- `REPLIK_PORTAL_URL`: defaults to verified official public portal landing URL `https://replik.justice.sk/ru-verejnost-web/`.
- `MONITOR_HISTORICAL_BATCH_LIMIT`: default `100`, range `1..500`.
- `POLL_OVERLAP_MINUTES`: default `10`.
- `MONITOR_STALE_AFTER_MINUTES`: default `90`.
- `DRY_RUN`: default `false`.
- `PORT`: default `8080`.

Values belong only in approved deployment configuration, never in this artifact, tickets, shell history, or logs.

## Verified direct proceeding links

On 2026-07-15, safe inspection of the official public REPLIK portal's search results at `https://replik.justice.sk/ru-verejnost-web/pages/searchKonanie.xhtml?query=47251301` returned an official direct proceeding link in this form:

`https://replik.justice.sk/ru-verejnost-web/pages/konanieDetail.xhtml?konanieId=<KonanieId>`

Alerts use that verified direct-detail template, URL-encoding the SOAP `KonanieId`. The SOAP WSDL itself does not document browser URLs; the portal result link is the authoritative public UI evidence for this template. Do not substitute a landing-page fragment or claim a different permalink without re-verifying the public portal.

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

Use `DRY_RUN=true` for an approved preflight poll. It makes SOAP calls but writes no database row and creates no email. It does not call Resend; do not invoke `deliver` until sender and recipient are verified and approved.

## Local verification without external effects

```sh
python -m unittest discover -s tests -v
python -m compileall -q replik_monitor tests
```

The unit suite uses live-contract-shaped SOAP fixtures, mocked HTTP request capture, and fake integration seams. It does not connect to PostgreSQL, Railway, or Resend. The endpoint/WSDL verification is a safe API query with a future `ZmenyOd`, so it returns no records.

## Operations

- Investigate a 503 from `/healthz` before assuming the scheduler is healthy. `/checkpoint` distinguishes missing, fresh, stale, and expired state.
- To stop: disable both cron jobs, wait one overlap interval, inspect `outbox` pending/failed rows, then stop the web service and revoke/remove approved environment values. Retain/delete the database only under its approved retention policy.
- The `sending` state intentionally retries after an interrupted process; Resend receives the durable outbox ID as `Idempotency-Key`.
