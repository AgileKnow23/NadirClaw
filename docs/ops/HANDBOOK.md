# Operations Handbook

Single-page reference: every service, port, log file, restart command.

## Table of contents
1. [Service map](#service-map)
2. [Public URLs](#public-urls)
3. [How to restart anything](#how-to-restart-anything)
4. [Where logs live](#where-logs-live)
5. [Sentinel: what it does and how to read it](#sentinel-what-it-does-and-how-to-read-it)
6. [SurrealDB quick reference](#surrealdb-quick-reference)
7. [AK Dashboard quick reference](#ak-dashboard-quick-reference)
8. [Reconcile pipeline (Tiller -> SurrealDB)](#reconcile-pipeline-tiller---surrealdb)
9. [RAAIDD / Transcribe pipeline](#raaidd--transcribe-pipeline)
10. [Adding a new bank-statement parser](#adding-a-new-bank-statement-parser)
11. [Cloudflare tunnel](#cloudflare-tunnel)

---

## Service map

Every always-on process is a Windows service (NSSM-managed unless noted). NSSM auto-starts them on boot and restarts them on crash within ~5 seconds.

| Service name | Port | Manager | What it is |
|---|---|---|---|
| `SurrealDB` | 8000 | NSSM | The database backing AK dashboard, RAAIDD, agent memory |
| `NadirClaw-Router` | 8856 | NSSM | LLM router (`nadirclaw.exe serve`) — converted to NSSM service 2026-04-28 |
| `Ollama` | 11434 | Windows service | Local LLM runtime |
| `AkDashboard` | 3000 | NSSM | Next.js financial dashboard (`akdashboard.agent-buddy.ai`) |
| `StatusApp` | 8766 | NSSM | RAAIDD "Calls" UI (`pipeline.agent-buddy.ai`) |
| `Cloudflared` | 20241 (metrics) | Windows service | Tunnel daemon — exposes the two public URLs above |
| `AutoRecord` | n/a | NSSM | RAAIDD audio recorder |
| `CoachStreamer` | n/a | NSSM | RAAIDD coach stream |
| `TranscribeWatcher` | n/a | NSSM | RAAIDD transcription pipeline |
| `TelegramDaemon` | n/a | NSSM | Telegram bot (`@Agile_Claude_bot`) |
| `NadirClawSentinel` | n/a | NSSM | The watchdog that monitors all of the above |

## Public URLs

Both routed through the same Cloudflare tunnel (`924dbad3-37b5-4c69-92ac-7ae9b79dbe97`). Cloudflare Access SSO gates entry.

| URL | Routes to | Purpose |
|---|---|---|
| `https://akdashboard.agent-buddy.ai/` | `localhost:3000` | AK financial dashboard |
| `https://pipeline.agent-buddy.ai/` | `localhost:8766` | RAAIDD calls/transcribe UI |

## How to restart anything

**First reflex**: do nothing. Sentinel polls every 60 seconds; if a service is down it'll restart it within 90 seconds and Telegram-alert you.

**Manual restart of a service** (need admin shell):
```powershell
Restart-Service AkDashboard          # or any service name above
```

**Force a Sentinel re-check** (run all health checks immediately + restart anything down):
```powershell
Restart-Service NadirClawSentinel -Force
```

**Restart the cloudflared tunnel** (when you see Cloudflare 1033):
```powershell
Restart-Service Cloudflared -Force
```
Note: sentinel does this automatically when `cloudflared`'s `/ready` endpoint reports zero edge connections.

**One-glance status from the desktop**: run `dashboard-health.ps1`.

## Where logs live

| Service | Log path |
|---|---|
| AkDashboard | `C:\Users\Agile\ak_dashboard\service.log` (rotated at 10 MB) |
| NadirClaw-Router | `C:\Users\Agile\.nadirclaw\logs\NadirClaw-Router.log` + `NadirClaw-Router-error.log` (rotated at 10 MB) |
| StatusApp | NSSM redirects per its config — `nssm get StatusApp AppStdout` |
| AutoRecord / CoachStreamer / TranscribeWatcher | `C:\transcribe\*.log` |
| Sentinel | `C:\Users\Agile\Respositories\NadirClaw\sentinel.log` |
| SurrealDB | check NSSM: `nssm get SurrealDB AppStdout` |
| Cloudflared (Windows event log) | `Get-EventLog -LogName Application -Source Cloudflared -Newest 20` |

## Sentinel: what it does and how to read it

`NadirClawSentinel` is `sentinel.py` running as an NSSM service under LocalSystem (so it has admin rights). It polls 6 services every 60 seconds.

**State file**: `C:\Users\Agile\Respositories\NadirClaw\sentinel_state.json` — updated every poll. This is what `dashboard-health.ps1` reads. Sample:

```json
{
  "services": {
    "ak_dashboard": { "healthy": true,  "reason": "ok" },
    "cloudflared":  { "healthy": false, "reason": "readyConnections=0 < 1" }
  },
  "all_healthy": false,
  "recovery_count_since_start": 0,
  "assessed_at": "2026-04-28T17:18:19+00:00",
  "sentinel_pid": 82332
}
```

**If `assessed_at` is older than 5 minutes**, sentinel itself is hung — `Restart-Service NadirClawSentinel -Force`.

**Rate limit**: 4 recoveries per hour per service. After 4 attempts, sentinel stops trying and Telegram-alerts so you know to investigate manually.

**Adding a new monitored service**: edit `sentinel.py`, add to the `SERVICES` dict (port, health_url, optional health_key/value), add a `restart_<name>()` function, register it in `RESTART_FNS`. Restart the service.

## SurrealDB quick reference

- Port `8000`, RocksDB persistence at `C:\Users\Agile\.nadirclaw\surrealdb-data`
- Credentials: `~/.gcreds/surrealdb.json`
- Namespaces and databases:

| Namespace | Databases | Purpose |
|---|---|---|
| `agileknow` | `ak`, `personal`, `farms_787` | AK dashboard, one DB per tenant |
| `memory` | `kb` | Agent cross-session memory |
| various | per-business | clarityflow, outcomefocus, etc. |

**Quick query** (from any shell):
```bash
curl -s -X POST http://127.0.0.1:8000/sql \
  -H "Accept: application/json" \
  -H "surreal-ns: agileknow" \
  -H "surreal-db: ak" \
  -u "root:root" \
  --data "SELECT yr, count() AS n FROM monthly_summary GROUP BY yr;"
```

**Surreal v2.6 gotchas**:
- Field name `year` is reserved — use `yr` everywhere.
- `ORDER BY DESC` is broken on numeric columns. Sort in JS/Python after the query.
- `SELECT DISTINCT` is not supported — use `GROUP BY`.
- Datetime fields come back as Date objects via the WS client, strings via the HTTP `/sql` endpoint. Normalize before string operations.

## AK Dashboard quick reference

| Thing | Location |
|---|---|
| Repo | `C:\Users\Agile\ak_dashboard` and GitHub `AgileKnow23/ak_dashboard` |
| Service | `AkDashboard` NSSM service, port 3000 |
| Build command | `npm run build` (then service restart picks it up) |
| Pages | Home, Trends, Where, Transactions, Notes for Accountant (was "Gueto"), Ask, Audit, Cards, Receipts, Savings |
| Tenant cookie | `ak_tenant=ak` / `personal` / `farms_787`, stored client-side |
| Year selector | Home page, top-right; URL param `?year=2026` |

**To deploy a code change**: `npm run build` then `Restart-Service AkDashboard` (or just push to GitHub and pull when convenient — service auto-loads on every restart).

**Bank-statement filter on /notas**: default ON for AK tenant. Reads from `statement_line` table (populated by reconcile ETL). Toggle via URL `?source=all` to see every Tiller row.

## Reconcile pipeline (Tiller -> SurrealDB)

Repo: `C:\Users\Agile\reconcile`

**Daily / on-demand refresh** (after Tiller has new transactions):
```bash
cd C:/Users/Agile/reconcile
python fetch_data.py --year 2026               # Tiller -> cache
python etl_to_surrealdb.py --year 2026         # cache -> AK SurrealDB
python etl_to_surrealdb.py --year 2026 --db personal   # cache -> personal SurrealDB
```

**Bank-statement parser**: `parse_statements.py` — looks at PDFs in `Documents\Agile Know\Finance\{year}\Monthly Reconciliation\Source Statements\`. Parsers are dispatched by account last4 in the `PARSERS` dict. Today only `9125` (AMEX National Bank Business Checking) is wired up.

**Monthly accountant package** (cron on the 3rd):
```bash
python monthly_job.py                          # prior month, full pipeline
python monthly_job.py --year 2026 --month 3    # specific month
```
This runs Tiller fetch -> build report -> render xlsx/PDF -> ETL to SurrealDB -> email accountant. Heartbeat written to `Logs/accountant_monthly_heartbeat.json`. Sentinel checks the heartbeat from the 4th onward; if missing or stale it Telegram-alerts daily until fixed.

## RAAIDD / Transcribe pipeline

Folder: `C:\transcribe`

| Service | Script | Purpose |
|---|---|---|
| `AutoRecord` | `auto_record.py` | Listens to local mic, splits on silence, saves audio chunks |
| `TranscribeWatcher` | `watch_and_transcribe.py` | Watches the chunk folder, transcribes via Whisper |
| `CoachStreamer` | `coach_streamer.py` | Streams coach analysis live |
| `StatusApp` | `status_app.py` | FastAPI viewer on port 8766 (`pipeline.agent-buddy.ai`) |

State + chunks live under `C:\transcribe\state\` and `C:\transcribe\completed\`. SurrealDB stores extracted call records.

## Adding a new bank-statement parser

When you download a new card issuer's monthly PDF:

1. Save it as `Account Statement - {Month} {Year} - Acct Ending {last4}.pdf` in `Documents\Agile Know\Finance\{year}\Monthly Reconciliation\Source Statements\`
2. Open `C:\Users\Agile\reconcile\parse_statements.py`
3. Inspect the new PDF format with `python parse_statements.py --file <path-to-pdf>` (it'll fail with "no parser for account ending XXXX" — that's expected)
4. Write a parser function modeled on `parse_amex_business_checking()`. Each issuer's layout differs.
5. Register it in the `PARSERS = {...}` dict, keyed by last4
6. Re-run `python etl_to_surrealdb.py --year 2026`
7. Refresh `/notas` — the chip for that account flips from amber "no statement" to green "N/M".

## Cloudflare tunnel

| Property | Value |
|---|---|
| Tunnel UUID | `924dbad3-37b5-4c69-92ac-7ae9b79dbe97` |
| Tunnel name | `ak-dashboard` |
| Mode | Dashboard-managed (`--token` startup) |
| Credentials | `C:\Users\Agile\.cloudflared\924dbad3-37b5-4c69-92ac-7ae9b79dbe97.json` |
| Local config (reference only) | `C:\Users\Agile\.cloudflared\config.yml` |
| Health endpoint | `http://127.0.0.1:20241/ready` (returns `{readyConnections: N}`) |
| API token | `~/.gcreds/cloudflare_api_token.txt` (env var `CLOUDFLARE_API_TOKEN`) |

**To inspect ingress rules via API**:
```bash
TOKEN=$(cat ~/.gcreds/cloudflare_api_token.txt)
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://api.cloudflare.com/client/v4/accounts/08547c875b5f518acbccff12799236c6/cfd_tunnel/924dbad3-37b5-4c69-92ac-7ae9b79dbe97/configurations" | python -m json.tool
```

**To check tunnel connection status**:
```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://api.cloudflare.com/client/v4/accounts/08547c875b5f518acbccff12799236c6/cfd_tunnel/924dbad3-37b5-4c69-92ac-7ae9b79dbe97" | python -c "import sys, json; r=json.loads(sys.stdin.read())['result']; print('status:', r['status'], 'connections:', len(r.get('connections',[])))"
```

Status `healthy` = 4 connections. `degraded` = 1-3. `down` = 0 (this is what produces the public 1033 error).
