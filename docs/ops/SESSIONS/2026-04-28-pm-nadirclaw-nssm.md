# Session 2026-04-28 PM — NadirClaw NSSM-ification + AkDashboard Recovery

Short follow-up session triggered by a Sentinel Telegram alert: `nadirclaw is DOWN but rate-limited (4/hr exceeded)`. Closes the original P1 backlog item ("NSSM-ify NadirClaw") that was deferred from the AM session.

## What was broken

- **NadirClaw (port 8856)** had been down for hours. Sentinel ran `Start-Process python.exe -c "...nadirclaw.cli:main"` from LocalSystem context every ~2 minutes; the child python crashed silently — no PATH for user-profile site-packages, stderr swallowed by `Start-Process -WindowStyle Hidden`. Recovery budget burned 4/hr indefinitely; only signal was the rate-limit Telegram alert.
- **AkDashboard service** also flipped to Stopped during the recovery window (collateral). Sentinel rate limit on `ak_dashboard` was also exhausted — both services were stuck in the "down + rate-limited" state.

## What shipped

| Artifact | Location | Why |
|---|---|---|
| New NSSM service `NadirClaw-Router` | NSSM-managed Windows service, auto-start | Same model as `AkDashboard` — survives reboots, NSSM auto-restarts on crash, dedicated log file |
| `scripts/fix_services_admin.ps1` | NadirClaw repo | Idempotent recovery installer. Stops anything on :8856, removes stale service, reinstalls clean, starts NadirClaw-Router + AkDashboard, restarts NadirClawSentinel |
| `sentinel.py:restart_nadirclaw()` patch | NadirClaw repo (uncommitted) | Now calls `_restart_windows_service("NadirClaw-Router")` — same pattern as `restart_ak_dashboard` and `restart_status_app`. No more LocalSystem Python spawn |
| Service env vars | `NADIRCLAW_USER_HOME`, `USERPROFILE`, `HOME` all pinned to `C:\Users\Agile` | NadirClaw resolves user-profile paths via `USER_HOME` constant. Sentinel runs as LocalSystem, so without these the process would resolve `~/.nadirclaw/.env` to `C:\Windows\System32\config\systemprofile` — same root cause as the 2026-04-22 AkDashboard fix |
| Logs | `~/.nadirclaw/logs/NadirClaw-Router.log` + `NadirClaw-Router-error.log` (NSSM-rotated at 10 MB) | Stop guessing why it died — read the file |

## Verification

After the elevated installer ran (UAC-approved):
- `Get-Service NadirClaw-Router, AkDashboard, NadirClawSentinel, SurrealDB` → all `Running, Automatic`
- `curl http://127.0.0.1:8856/` → `{"name":"NadirClaw","version":"0.5.0","status":"ok"}`
- Sentinel log at 13:23:17 → `nadirclaw=ok surrealdb=ok ak_dashboard=ok ollama=ok status_app=ok cloudflared=ok`
- All 6 monitored services green; rate limiter cleared.

## Docs updated this session

- `CHANGELOG.md` — added `[0.5.1] - 2026-04-28` entry
- `docs/ops/HANDBOOK.md` — service map row for NadirClaw flipped from "bare process" to NSSM; added router log path
- `docs/ops/FAQ.md` — replaced "URLError: WinError 10061" entry with the post-fix recovery procedure (`Restart-Service NadirClaw-Router` + reinstall via `fix_services_admin.ps1`)
- `docs/ops/BACKLOG.md` — removed P1 NSSM-ify NadirClaw item; updated header date; added PM session to the Done section
- `docs/ops/SESSIONS/2026-04-28.md` — flipped NadirClaw NSSM-ification from "Pending" to "Done"
- Memory file `~/.claude/projects/.../memory/project_nadirclaw_sentinel.md` — added "Fix shipped 2026-04-28" section

## Pending

- **Commit + push NadirClaw changes.** `sentinel.py` patch + `scripts/fix_services_admin.ps1` are still uncommitted. Suggested commit message:
  ```
  reliability: promote NadirClaw to own NSSM service

  - Adds scripts/fix_services_admin.ps1 (idempotent recovery installer)
  - sentinel.py:restart_nadirclaw() now uses nssm restart NadirClaw-Router
    instead of LocalSystem-spawned python.exe (which crashed silently)
  - Updates ops handbook, FAQ, changelog, backlog
  ```
- **Run `/sc:index-repo`** on NadirClaw to refresh the SuperClaude codemap with the new service architecture (Bryan asked for this).

## Lessons re-learned

- **LocalSystem child processes need explicit `USERPROFILE`/`HOME`/`NADIRCLAW_USER_HOME` env vars.** This is the same trap as the 2026-04-22 `Path.home()` fix in sentinel itself. Any code that resolves `~/...` paths must be told where the user profile lives, otherwise it lands in `C:\Windows\System32\config\systemprofile` and silently fails.
- **`Start-Process -WindowStyle Hidden` swallows stderr.** Three days of restart attempts produced zero diagnostic output. Sentinel's spawn-based restart was effectively a black box. NSSM is the right answer for any port-bound process.
- **Rate-limit Telegram alerts are the canary, not the diagnosis.** "X is DOWN but rate-limited" means sentinel gave up — the underlying failure happened 4 polls ago. Trust the alert, but go read the actual service log, not sentinel.log.
