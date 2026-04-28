# FAQ — Symptom to Fix Lookup

If you see a symptom on this page, the line below it is the fix. If your symptom isn't here, run `dashboard-health.ps1` first.

---

## "Cloudflare 1033 — error tunnel cannot resolve hostname"
**Cause**: cloudflared is running but its connections to the Cloudflare edge dropped. Process up, tunnel down.
**Fix**: `Restart-Service Cloudflared -Force` in an admin shell. Sentinel will also do this automatically within 60 seconds.
**Prevention**: already in place — sentinel polls `http://127.0.0.1:20241/ready` and restarts cloudflared when `readyConnections` drops to 0.

---

## "No data yet for 2026" on the AK dashboard home page
**Cause** (today, after the empty-state fix): SurrealDB query returned no rows. Two distinct sub-cases:
- *Couldn't reach the database*: page shows red text — SurrealDB is down. `Restart-Service SurrealDB`.
- *No data yet for {year}*: data genuinely doesn't exist in the DB for that year/tenant. Run the ETL: `cd ~/reconcile && python fetch_data.py --year 2026 && python etl_to_surrealdb.py --year 2026`.
**Old cause** (pre-fix): the empty-state branch fired for both errors and empty alike. If you see *that* version of the message, you're on stale code — `Restart-Service AkDashboard`.

---

## Dashboard URL returns nothing / browser hangs
**Cause**: `AkDashboard` service stopped or port 3000 isn't bound.
**Diagnose**: `Get-Service AkDashboard` and `netstat -ano | findstr :3000`.
**Fix**: `Restart-Service AkDashboard`. If that fails, run `~/Desktop/fix-akdashboard.bat` (right-click -> Run as administrator).

---

## "AkDashboard service couldn't be started"
**Cause**: usually NSSM has the wrong `AppDirectory` or port 3000 is held by a stale `node.exe` from a manual `npm start`.
**Fix**: `~/Desktop/fix-akdashboard.bat` (right-click -> Run as administrator). It frees port 3000, re-applies the full NSSM config, and restarts.

---

## NSSM commands return "Access is denied"
**Cause**: the shell isn't elevated. Self-elevating .bat files are unreliable on UAC-restricted accounts.
**Fix**: don't use the .bat. Open Start -> type `powershell` -> right-click -> **Run as administrator** -> click YES -> then run the .ps1 directly:
```powershell
& 'C:\Users\Agile\Respositories\NadirClaw\fix-akdashboard.ps1'
```

---

## /notas page shows "no statement" chips on every account
**Cause**: bank-statement parser hasn't been run for the current month, or the issuer's PDFs haven't been downloaded yet.
**Fix**:
1. Download the PDF(s) into `Documents\Agile Know\Finance\{year}\Monthly Reconciliation\Source Statements\` using the naming convention `Account Statement - {Month} {Year} - Acct Ending {last4}.pdf`
2. If this is a new issuer, write a parser function in `~/reconcile/parse_statements.py` (see [HANDBOOK](HANDBOOK.md#adding-a-new-bank-statement-parser))
3. Re-run `python ~/reconcile/etl_to_surrealdb.py --year 2026`
4. Hard-refresh /notas

---

## Sentinel state file is older than 5 minutes
**Cause**: `NadirClawSentinel` is hung or crashed.
**Fix**: `Restart-Service NadirClawSentinel -Force` in admin shell. Check `sentinel.log` for the exception that killed it.

---

## "RATE LIMITED — X is down but recovery budget exhausted"
**Cause**: Sentinel tried 4 restarts in the last hour and they all failed. It backs off to avoid restart loops.
**Fix**: figure out *why* the service is failing (check its log), fix the root cause, then `Restart-Service NadirClawSentinel -Force` to clear the rate limiter.

---

## Telegram alert says "Accountant Monthly: monthly package did NOT land"
**Cause**: the cron-driven `monthly_job.py` failed silently; no heartbeat written.
**Fix**: `python C:/Users/Agile/reconcile/accountant_monthly.py` — runs the full pipeline manually. Check the output for the actual error (Tiller auth expired? Outlook unavailable? Schwab fetch failed?).

---

## "URLError: WinError 10061" in sentinel.log for nadirclaw
**Cause**: NadirClaw (port 8856) isn't running.
**Fix** (since 2026-04-28, NadirClaw runs as NSSM service `NadirClaw-Router`):
```powershell
Restart-Service NadirClaw-Router         # admin shell
# or, if rate limit is also hit:
Restart-Service NadirClawSentinel -Force # also clears in-memory rate limiter
```
**If restart keeps failing**: read `C:\Users\Agile\.nadirclaw\logs\NadirClaw-Router-error.log` for the actual error (missing module, port collision, OAuth token expired). Last-resort manual launch:
```powershell
cd C:\Users\Agile\Respositories\NadirClaw
python -c "import sys; sys.argv=['nadirclaw','serve']; from nadirclaw.cli import main; main()"
```
**Reinstall the service** (admin shell):
```powershell
& 'C:\Users\Agile\Respositories\NadirClaw\scripts\fix_services_admin.ps1'
```
That script is idempotent — stops anything on :8856, removes the old service, reinstalls clean.

---

## ETL fails with "Specify a namespace to use"
**Cause**: SurrealDB v3+ moved the namespace selector from request body to a header. Old clients pass `NS:` instead of `surreal-ns:`.
**Fix**: use `surreal-ns:` and `surreal-db:` headers:
```bash
curl -s -X POST http://127.0.0.1:8000/sql \
  -H "surreal-ns: agileknow" \
  -H "surreal-db: ak" \
  -u "root:root" \
  --data "INFO FOR DB;"
```

---

## PowerShell script throws "Unexpected token" or "The '<' operator is reserved"
**Cause**: the script contains non-ASCII characters (em-dash, smart quotes) or literal `<-` arrows. Windows PowerShell 5.1 reads BOM-less .ps1 as Windows-1252 and mangles them.
**Fix**: open the file, replace non-ASCII chars with ASCII equivalents (`-` for em-dash, `--` for arrows). All scripts in this repo are pre-validated with `[Parser]::ParseFile()`.

---

## Tiller cache has April 27 transactions but they don't show in the dashboard
**Cause**: those rows might be filtered out at the AK ETL stage (e.g., personal account transactions like SBA loan / mortgage / insurance debits). The AK DB only ingests `is_ak`, `is_9125`, or `is_ak_card` rows.
**Fix**: switch to the Personal tenant in the dashboard (top-right dropdown) to see them, OR run the personal ETL: `python etl_to_surrealdb.py --year 2026 --db personal`.

---

## "Group used for deny only" appears next to BUILTIN\Administrators in `whoami /groups`
**Not a problem.** That's standard UAC behavior on consumer Windows — your account *is* an admin, but every shell starts with a filtered token. You have to explicitly elevate (right-click -> Run as administrator) to get the full token. Self-elevating .bat files often misbehave because of this; right-click + Run as administrator is the reliable path.
