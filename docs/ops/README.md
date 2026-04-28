# Bryan's Stack — Operations Index

Operational documentation for the local-first stack running on Bryan's
PC. Distinct from the upstream NadirClaw OSS docs in `../comparison.md`
etc — anything in `docs/ops/` is fork-specific and operational.

## When something is broken

1. **`dashboard-health.ps1`** on your desktop — run first. Three-second visual report of every service + public URL. Offers a one-click force-heal if anything's red.
2. If health check shows a service down, see [FAQ.md](FAQ.md) — symptoms → fixes for the most common failures (1033, "no data for 2026", port 3000 squatter, NSSM access denied).
3. If you need to understand *what* something is or *where* it lives, see [HANDBOOK.md](HANDBOOK.md) — every service, port, log location, restart command.

## Documents in this folder

| Doc | Purpose | When to open |
|---|---|---|
| [HANDBOOK.md](HANDBOOK.md) | Full reference: every service, every port, every log, every command | Onboarding a new machine, planning a change, "what's running where?" |
| [FAQ.md](FAQ.md) | Symptom → fix lookup. Cloudflare 1033, dashboard empty, sentinel hung, etc. | Something is broken right now and you want a one-line fix |
| [BACKLOG.md](BACKLOG.md) | Prioritized work queue with effort estimates and resume triggers | Starting a new Claude session with "what should I work on?" |
| [SESSIONS/](SESSIONS/) | Per-session change logs. Captures what shipped, why, and what's still pending | After a long session, before going to bed, to refresh memory next morning |

## Reading on your phone

This folder is on GitHub at `AgileKnow23/NadirClaw` under `docs/ops/`. Open it from any browser when you're away from your PC and need to remember what to do.

## Where the operational scripts live

| Script | Purpose | How to run |
|---|---|---|
| `~/Desktop/dashboard-health.ps1` | Status check + optional force-heal | Right-click → Run with PowerShell |
| `~/Desktop/install-reliability-patch.bat` | First-time installer for the AkDashboard NSSM service + sentinel monitors | Right-click → Run as administrator (one-time) |
| `~/Desktop/fix-akdashboard.bat` | Recovery: re-applies AkDashboard NSSM config + restarts | Right-click → Run as administrator |
| `~/Respositories/NadirClaw/sentinel.py` | The watchdog itself (runs as `NadirClawSentinel` NSSM service) | Read for understanding, don't run by hand |

## Tooling principles

- Every long-running process is a Windows service (NSSM-managed) — survives sleep, reboot, and crashes.
- The sentinel polls every 60 seconds and self-heals; manual intervention is only needed when sentinel itself is broken or rate-limited.
- Every restart triggers a Telegram alert to `@Agile_Claude_bot`.
- `dashboard-health.ps1` is the *human* observability layer — you don't need to read logs to know if things are up.
