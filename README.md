# Replik monitor — official public SOAP client, Railway artifact

This is a bounded Python monitor for the official public IS REPLIK SOAP 1.1 contract. It contains no credentials, sender/recipient values, Railway target, deployment command, or scheduled-job creation. Deployment and outbound-email approval remain human operations. Naturally, the difficult part is not a Dockerfile.

## Official contract verified on 2026-07-15

- Production WSDL: `https://replik-ws.justice.sk/ru-verejnost-ws/konanieService.wsdl`
- Production SOAP endpoint: `https://replik-ws.justice.sk/ru-verejnost-ws/`
- Binding: SOAP 1.1 document/literal, `Content-Type: text/xml; charset=utf-8`, `SOAPAction: ""`.
- IČO discovery request: `getKonaniePodlaICORequest` with `Ico`, zero-based `Stranka`, `VysledkovNaStranku` (maximum `100`), and `TypTriedenia=DatumPoslednejUdalosti`.
- IČO discovery response: `KonanieInfoList/KonanieInfo` plus `VysledkovCelkom`; each info contains `Id`, `DlznikIco`, and `DatumPoslednejUdalosti` (`xsd:date`).
- Detail request: `getKonanieDetailRequest` with `KonanieId` remains available, but is not used to pre-filter a global feed.

The client reconciles every stable, paginated `getKonaniePodlaICO` page for the configured exact debtor IČO (`47251301`). It never uses the global `vyhladajPoslednuZmenuOd` feed, so unrelated market-wide changes cannot trigger a 500-result abort. A durable identity consists of `KonanieInfo/Id` and a hash of its complete published state (including last-event date); this catches later amendments to an already-known proceeding even though the list contract exposes a date rather than a timestamp.

## Safety controls

- PostgreSQL owns migrations, an advisory poll lock, source-identity deduplication, durable outbox retry, and a stable per-outbox delivery key. The HTTP adapter sends that key as Resend's `Idempotency-Key`; the optional SMTP adapter sends the exact same value as Resend's documented `Resend-Idempotency-Key` custom SMTP header, while retaining a deterministic `Message-ID` for correlation.
- `MONITOR_HISTORICAL_BATCH_LIMIT` controls `VysledkovNaStranku` (default `100`, range `1..100`). Every `getKonaniePodlaICO` page is reconciled; `VysledkovCelkom` may exceed 500 without being treated as an error. The client accepts a snapshot only after two full collections have identical ordered proceeding identity/state sequences; page-total, duplicate, short-page, or cross-collection mismatch retries a bounded number of times and then fails closed.
- The first successful poll transactionally claims one historical baseline. It creates one durable historical outbox item per at-most-100-record digest, in deterministic sorted order; all records are persisted before delivery. Each item retries with its own durable outbox ID, and incremental polling remains blocked until every historical item is sent—there is no unbounded initial email and no silent omission.
- `changes` deduplicates on proceeding ID plus a canonical public-state marker, so overlap polling cannot resend an unchanged state but does preserve later proceeding events.
- `MONITOR_EXPIRES_AT` must be future and within 31 days. Its first accepted value is persisted, so changing an environment value cannot extend an active monitor.
- `/healthz` returns 200 only when the database is reachable and the monitor is active. `/checkpoint` separates scheduler freshness from web readiness.

## Configuration

Required names:

`DATABASE_URL`, `RESEND_API_KEY`, `RESEND_FROM`, `ALERT_TO`, `MONITOR_EXPIRES_AT`, `MONITOR_HISTORICAL_SINCE`

Optional names:

- `REPLIK_ENDPOINT`: defaults to the verified official production SOAP endpoint above.
- `REPLIK_PORTAL_URL`: defaults to verified official public portal landing URL `https://replik.justice.sk/ru-verejnost-web/`.
- `MONITOR_HISTORICAL_BATCH_LIMIT`: default `100`, range `1..100`.
- `POLL_OVERLAP_MINUTES`: default `10`.
- `MONITOR_STALE_AFTER_MINUTES`: default `90`.
- `DRY_RUN`: default `false`.
- `PORT`: default `8080`.
- `EMAIL_TRANSPORT`: default `api`. Set to `smtp` only to use Resend SMTP submission instead of the HTTP API.
- `RESEND_SMTP_HOST`: default `smtp.resend.com`.
- `RESEND_SMTP_PORT`: default `465`; only `25`, `465`, `587`, `2465`, and `2587` are accepted.
- `RESEND_SMTP_SECURITY`: default `implicit_tls`, which requires port `465` or `2465`. Set `starttls` only with port `25`, `587`, or `2587`.

Resend's authoritative [SMTP documentation](https://resend.com/docs/send-with-smtp) (accessed 2026-07-16) specifies host `smtp.resend.com`, username `resend`, and the existing Resend API key as the SMTP password. It classifies ports `465`/`2465` as implicit TLS and `25`/`587`/`2587` as STARTTLS. No additional credential is configured or logged.

`EMAIL_TRANSPORT=api` remains the safe backward-compatible default. SMTP is an explicit deployment choice for environments where the HTTP API is unavailable. Resend documents `Resend-Idempotency-Key` as the SMTP equivalent of its HTTP `Idempotency-Key`; the adapter sends the exact durable outbox ID in that header for provider-side deduplication and retains a stable RFC `Message-ID` for correlation. The durable retry loop intentionally treats any SMTP exception or recipient refusal as unsent and retries it.

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

The default suite uses live-contract-shaped SOAP fixtures, mocked HTTP request capture, and fake integration seams. It does not connect to PostgreSQL, Railway, or Resend. `tests/test_migration_compatibility.py` also preserves and checks the exact published 0.2.1 schema and migration ordering without a database. For an integration-grade PostgreSQL upgrade and fresh-schema check, provide an isolated, non-production database URL whose role may create and drop schemas:

```sh
REPLIK_TEST_DATABASE_URL='postgresql://…' python -m unittest discover -s tests -p 'test_migration_compatibility.py' -v
```

No local PostgreSQL service is required for the standard suite; the real-database tests skip with their reason when that explicit URL is absent. The endpoint/WSDL verification is a safe API query with a future `ZmenyOd`, so it returns no records.

## Operations

- Investigate a 503 from `/healthz` before assuming the scheduler is healthy. `/checkpoint` distinguishes missing, fresh, stale, and expired state.
- To stop: disable both cron jobs, wait one overlap interval, inspect `outbox` pending/failed rows, then stop the web service and revoke/remove approved environment values. Retain/delete the database only under its approved retention policy.
- The `sending` state intentionally retries after an interrupted process; Resend receives the durable outbox ID as `Idempotency-Key`.
