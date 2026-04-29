# Backlog — Bryan's Local Stack

Ordered by leverage. Each item has the why, the what-to-do, an effort
estimate, and the exact resume trigger to say in a new Claude session.

Last updated: 2026-04-28 PM after the NadirClaw NSSM-ification
(uncommitted on NadirClaw — see `scripts/fix_services_admin.ps1` and the `restart_nadirclaw()` patch in `sentinel.py`).

---

## P0 — Statement-driven /notas (high leverage, mostly Bryan-side work)

### P0-1. Download the 86 missing card-statement PDFs
**Why**: until each card's monthly PDFs land in `Source Statements/`, the /notas page shows "no statement" amber chips for every card account. The Tiller side is already filtered down to bank-statement-only when those PDFs exist.

**What to do** (Bryan, ~30-60 min total across 5 portals):

| Portal | Account last4 | Months needed |
|---|---|---|
| americanexpress.com | 3007 (Platinum) | 2025 Jan-Dec, 2026 Jan-Mar |
| chase.com | 0988 (CC M.Agosto) | 2025 Jan-Dec, 2026 Jan-Mar |
| chase.com | 8146 (CC B.Agosto) | 2025 Jan-Nov |
| citi.com | 8178 (787 Farms CC) | 2025 Jan-Dec, 2026 Jan-Mar |
| citi.com | 8471 (AAdvantage Biz) | 2025 Mar-Nov, 2026 Mar |
| cards.barclaycardus.com | 5689 (AAdvantage Aviator) | 2025 Jan-Dec, 2026 Jan-Mar |
| discover.com | 1509 (Discover it) | 2025 Jun, Aug, Sep, Nov |
| discover.com | 7383 (Discover it old) | 2025 Jan, Feb |

Drop into `~/Documents/Agile Know/Finance/{year}/Monthly Reconciliation/Source Statements/` using the convention `Account Statement - {Month} {Year} - Acct Ending {last4}.pdf`.

**Resume trigger**: "I dropped the Amex statements" / "I dropped the Chase statements" / etc. Claude writes the per-issuer parser, runs the ETL, verifies the chips flip from amber to green.

### P0-2. Per-issuer statement parsers (one per portal, ~20 min each with PDF in hand)
**Why**: each bank's PDF layout is different; `parse_statements.py` dispatches by `last4`. Today only `9125` (AMEX National Bank Business Checking) has a parser.

**What to do** (Claude, after P0-1 lands per issuer): write a parser function in `~/reconcile/parse_statements.py`, register it in the `PARSERS` dict, run `python etl_to_surrealdb.py --year 2026`, verify match rate via `getStatementCoverage`.

**Resume trigger**: implied by P0-1 — Claude does both as one unit.

---

## P1 — Hostinger migration (60-90 min, focused)

**Why**: removes PC dependency entirely. PC can be off; dashboard, pipeline, SurrealDB, reconcile cron all run on a $8/mo Hostinger KVM 2 VPS. See the prior chat where we evaluated paths A/B/C — Path A (all-in-one VPS) was recommended.

**Open question to answer first**: does the RAAIDD `auto_record.py` need a local microphone? If yes, it stays on PC and pushes audio events to remote SurrealDB. If no, all of RAAIDD moves too.

**What to do** (Claude, when Bryan provisions VPS + sends IP):
1. SSH in, install Node 20 + Python 3.12 + SurrealDB binary
2. rsync the SurrealDB rocksdb dir from `~/.nadirclaw/surrealdb-data` to VPS
3. Clone both repos, install deps, drop env files / Google OAuth tokens / surrealdb creds
4. Set up systemd units: `surrealdb.service`, `akdashboard.service`, `pipeline-viewer.service`
5. Set up cron for nightly Tiller pull + monthly accountant job
6. Repoint Cloudflare DNS for both subdomains at the VPS IP (still proxied so Cloudflare Access keeps working)
7. Smoke-test, hand back

**Resume trigger**: "I provisioned the Hostinger VPS, IP is X.X.X.X" / "let's start the Hostinger migration"

---

## P2 — Refactor `nadirclaw/server.py` (1950 → 964 → ~500-600)

**Why**: server.py blew past every Power-of-Ten ceiling. The file was 1950 lines, `chat_completions` alone is 534 lines (the rule says 60), and there's a 269-line block of Gemini/LiteLLM dispatch code that duplicates `dispatch.py`. Hard to navigate, harder to test, and any future contributor (or me at 11pm) is going to make a regression.

### Phase A — safe extractions

| # | Commit | What moved | Landed at | Status |
|---|---|---|---|---|
| A1 | `31411fa` refactor(server): extract Pydantic request models | 6 BaseModel classes (47 lines) | `nadirclaw/api_models.py` (64 lines) | **DONE** 2026-04-28 |
| A2 | `6b1d5b2` refactor(server): extract blast endpoint | 1 endpoint (51 lines) | `nadirclaw/routes/blast.py` (61 lines) | **DONE** 2026-04-28 |
| A3 | `9c589e4` refactor(server): extract pipeline endpoints | 5 endpoints (230 lines) | `nadirclaw/routes/pipeline.py` (251 lines) | **DONE** 2026-04-28 |
| A4a | `e5456dc` refactor(server): extract log_request | `_log_request` (40 lines) + module Lock | `nadirclaw/logging.py` (56 lines) | **DONE** 2026-04-29 |
| A4b | `bc14401` refactor(server): move smart_route helpers | `_smart_route_analysis` + `_smart_route_full` (~70 lines) | promoted to public `smart_route_analysis` / `smart_route_full` in `nadirclaw/routing.py` | **DONE** 2026-04-29 |
| A4c | `efd6407` refactor(server): extract classify endpoints | `/v1/classify` + `/v1/classify/batch` (49 lines, 2 endpoints) | `nadirclaw/routes/classify.py` (68 lines) | **DONE** 2026-04-29 |
| A4d | `d3d223c` refactor(server): extract observability endpoints | logs / events / dashboard / search / history / knowledge×2 / analytics (8 endpoints, 154 lines) | `nadirclaw/routes/observability.py` (175 lines) | **DONE** 2026-04-29 |
| A5 | `5f36d15` refactor: dedupe Gemini/LiteLLM helpers with dispatch.py | `_call_gemini`, `_call_litellm`, `_dispatch_model`, `_call_with_fallback`, `_strip_gemini_prefix`, `_get_gemini_client`, `_rate_limit_error_response` (~410 lines) | Consolidated into `dispatch.py`; server uses new `call_with_tier_fallback` | **DONE** 2026-04-29 |

**Progress**: server.py 1950 → 964 (−986 lines, −51%). 306 tests green throughout, zero regressions.

**Phase A landed**. server.py ≈ 964 lines now (mostly `lifespan`, `chat_completions` + force-model branch, `_RateLimiter`, app factory, `_build_streaming_response`, `/v1/models` + `/health` + `/`). Slightly over the 800 ceiling — Phase B will cut the rest.

**Resume trigger**: "let's start the chat_completions refactor — write B0 golden fixtures first" (Phase B).

### Phase B — `chat_completions` refactor (separate ~2hr session)

**This is the hot path. 534 lines that handle: auth, validation, rate-limiting, classification, routing, parallel-dispatch, fallback, telemetry, streaming, history-logging, error responses. Splitting it without contract tests is how you ship a regression.**

**Pre-work (mandatory before touching the function)**:
- B0: Write **golden-fixture tests** capturing today's behavior. 6-8 fixtures: streaming-success, buffered-success, rate-limit-fallback, parallel-dispatch, validation-error, model-not-found, downstream-429, agentic-detection. Snapshot input → output, commit the snapshots.

**Then the actual extraction (~5 commits)**:
- B1: extract `preprocess_request()` (auth + validation + metadata) → ~80 lines out
- B2: extract `select_route(request, analysis)` (smart_route + agentic + reasoning detection) → ~60 lines out
- B3: extract `dispatch_with_policy(...)` (rate-limit + parallel decision + fallback) → ~120 lines out
- B4: extract `build_response(...)` (streaming vs buffered + history log + telemetry) → ~150 lines out
- B5: chat_completions becomes ~80 lines orchestrating those four

**After Phase B**: server.py ≈ 500-600 lines, under the ceiling. Each helper testable in isolation.

**Risks I'm not waving away**:
1. `chat_completions` reads as one continuous flow with shared state (`request_id`, `analysis_info`, `selected_model`, `provider`, `start_time`). Naïve extraction explodes the parameter list. The right boundary is probably a `RequestContext` dataclass.
2. A5 (dedupe with dispatch.py) needs careful side-by-side diff — the two implementations may have drifted.
3. `pytest --cov=nadirclaw.server` should run BEFORE any Phase B work, so we know what's actually exercised.

**Resume trigger**: "let's do the chat_completions refactor — start with B0 golden fixtures"

---

## P3 — Nice-to-haves

### P3-1. April 2026 statements (~5 min, after May 5-15)
After each card's April cycle closes, download April PDFs into `Source Statements/`. Re-run ETL. /notas April page lights up.
**Resume trigger**: "I downloaded the April statements"

### P3-2. In-dashboard `/documentation` route (~30 min)
Render `docs/ops/*.md` as a Next.js page at `akdashboard.agent-buddy.ai/documentation`. Markdown-it or react-markdown. Pros: works without GitHub access. Cons: another thing to maintain.
**Resume trigger**: "build the in-dashboard docs page"

### P3-3. Statement-coverage chips on Home page (~15 min)
Surface the same per-account match rate from /notas at the top of Home. Quick visual signal of "is my data complete this month?"
**Resume trigger**: "add coverage chips to home"

### P3-4. Receipt OCR backfill (separate, ask Bryan)
The receipts page exists but no recent automation context — likely candidate for review.
**Resume trigger**: "what's the state of receipts?"

---

## Done in 2026-04-28 sessions (for reference, not a task)

**AM session** — see [SESSIONS/2026-04-28.md](SESSIONS/2026-04-28.md). 8 commits across 3 repos. Reliability patch (NSSM + sentinel deep monitors). Bank-statement filter for /notas.

**PM session** — see [SESSIONS/2026-04-28-pm-nadirclaw-nssm.md](SESSIONS/2026-04-28-pm-nadirclaw-nssm.md). NadirClaw promoted to its own NSSM service (`NadirClaw-Router`); sentinel patched to use `nssm restart` for nadirclaw; AkDashboard recovered; rate limits cleared. All 6 monitored services back to green. Closes the original P1 backlog item.
