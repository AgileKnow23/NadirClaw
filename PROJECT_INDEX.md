# Project Index: NadirClaw

Generated: 2026-04-28 (PM session — post NSSM-ification)
Version: 0.5.0 (CHANGELOG 0.5.1 unreleased)
Fork: `AgileKnow23/NadirClaw` (upstream `doramirdor/NadirClaw`)

> **What this is.** A 3 KB session-priming map of the repo. Read this first instead of crawling files.

---

## Project Structure

```
NadirClaw/
├── nadirclaw/            # main package (FastAPI + CLI + routing)
│   ├── cli.py            # CLI entry point (nadirclaw command)
│   ├── server.py         # FastAPI app, /v1/chat/completions
│   ├── dispatch.py       # provider call layer (Gemini OAuth, Claude/Codex CLI, LiteLLM)
│   ├── routing.py        # model resolution, alias, agentic/reasoning detection, cost
│   ├── classifier.py     # sentence-embedding complexity classifier
│   ├── classifier_v2.py  # next-gen classifier (intent-aware)
│   ├── intent.py         # intent detection
│   ├── orchestrator.py   # multi-step orchestration
│   ├── parallel_dispatch.py  # parallel-model fan-out
│   ├── pipeline.py + pipeline_db.py + pipeline_tracker.py  # pipeline persistence
│   ├── blast.py          # broadcast-to-many models
│   ├── credentials.py    # ~/.nadirclaw/credentials.json store, provider aliases
│   ├── oauth.py + auth.py  # OAuth flows (Gemini, OpenAI, Anthropic, Antigravity)
│   ├── settings.py       # ~/.nadirclaw/.env loader
│   ├── encoder.py prototypes.py knowledge.py events.py
│   ├── role_registry.py  # agent role config
│   ├── service_manager.py  # NSSM helpers (windows_service.ps1 backend)
│   ├── history_middleware.py  # request/response logging
│   ├── report.py setup.py telemetry.py db.py
├── openwebui/            # OpenWebUI plugins (filters + actions)
├── tests/                # pytest suite (15 files)
├── scripts/
│   ├── fix_services_admin.ps1   # 2026-04-28: idempotent NSSM recovery installer
│   ├── windows_service.ps1      # original full-stack NSSM installer (admin)
│   └── codex-review.sh
├── docs/
│   ├── comparison.md     # NadirClaw vs OpenRouter
│   ├── governance/       # AI governance docs (Constitution, Operating Model, etc.)
│   └── ops/              # **operational handbook** (this fork only)
│       ├── README.md HANDBOOK.md FAQ.md BACKLOG.md
│       └── SESSIONS/     # per-session change logs
├── sentinel.py           # ecosystem watchdog (NSSM service NadirClawSentinel)
├── pyproject.toml CHANGELOG.md README.md CONTRIBUTING.md CODEX_CONSTITUTION.md
└── .github/workflows/    # ci.yml + publish.yml
```

---

## Entry Points

| Surface | Command / Path | Description |
|---|---|---|
| CLI | `nadirclaw <subcommand>` → `nadirclaw.cli:main` | `serve`, `setup`, `auth <provider>`, `classify`, `status`, `build-centroids`, `service` |
| HTTP API | `nadirclaw serve` (port **8856**) | OpenAI-compatible `/v1/chat/completions` (SSE streaming, tool calls, vision) |
| Sentinel | NSSM service `NadirClawSentinel` → `sentinel.py` | 60s health poll, auto-restart, Telegram alert |
| Tests | `pytest tests/` | unit + integration (15 files) |

---

## Core Modules

| Module | Role | Key exports |
|---|---|---|
| `cli.py` | CLI dispatcher | `main()` |
| `server.py` | FastAPI app | `app`, `ChatCompletionRequest`, `_RateLimiter`, `ClassifyRequest` |
| `dispatch.py` | LLM provider calls | `RateLimitExhausted`, Gemini cloudcode + LiteLLM call paths |
| `routing.py` | Model selection logic | `resolve_alias`, `detect_agentic`, `detect_reasoning`, `apply_routing_modifiers`, `SessionCache` |
| `classifier.py` | Complexity classifier | `BinaryComplexityClassifier`, `get_binary_classifier`, `warmup` |
| `credentials.py` | Token store | provider-alias resolution (google↔gemini), atomic writes |
| `oauth.py` / `auth.py` | OAuth flows | Gemini PKCE, OpenAI/Codex, Anthropic, Antigravity |
| `pipeline*.py` | Pipeline persistence | request/response audit trail in SurrealDB |
| `parallel_dispatch.py` | Parallel calls | fan-out across providers |
| `blast.py` | Broadcast | one prompt → N models |
| `service_manager.py` | NSSM helpers | `nadirclaw service install/start/stop/status` |
| `sentinel.py` *(top-level)* | Watchdog | monitors 6 services on Bryan's PC, auto-restarts via NSSM |

---

## Operational Layer (this fork)

Bryan's local stack runs every long-lived process as an NSSM Windows service:

| Service | Port | Purpose |
|---|---|---|
| `NadirClaw-Router` | 8856 | This package (`nadirclaw.exe serve`) — **NSSM since 2026-04-28** |
| `NadirClawSentinel` | n/a | `sentinel.py` watchdog (LocalSystem) |
| `SurrealDB` | 8000 | Backing store (rocksdb at `~/.nadirclaw/surrealdb-data`) |
| `Ollama` | 11434 | Local LLM runtime |
| `AkDashboard` | 3000 | Next.js financial dashboard |
| `StatusApp` | 8766 | RAAIDD calls UI |
| `Cloudflared` | 20241 (metrics) | Tunnel daemon |

**When something breaks**: run `~/Desktop/dashboard-health.ps1` first. Symptom→fix lookup in `docs/ops/FAQ.md`. Full reference in `docs/ops/HANDBOOK.md`.

---

## Configuration

| File | Purpose |
|---|---|
| `pyproject.toml` | Package metadata, deps, console entry point |
| `~/.nadirclaw/.env` | Runtime config (model tiers, ports, feature flags) |
| `~/.nadirclaw/credentials.json` | OAuth tokens / API keys |
| `~/.nadirclaw/logs/NadirClaw-Router.log` | NSSM-managed router log (rotated 10 MB) |
| `sentinel_state.json` | Sentinel snapshot per poll (60s) — read by `dashboard-health.ps1` |

---

## Documentation Map

| Doc | When to open |
|---|---|
| `README.md` | Upstream OSS overview (install, providers, OAuth) |
| `CHANGELOG.md` | Version history (latest: 0.5.1 → NSSM-ification) |
| `docs/comparison.md` | NadirClaw vs OpenRouter |
| `docs/ops/README.md` | Ops landing page |
| `docs/ops/HANDBOOK.md` | Service map, ports, logs, restart commands, SurrealDB ref |
| `docs/ops/FAQ.md` | Symptom→fix lookup (1033, rate-limited, NadirClaw down, etc.) |
| `docs/ops/BACKLOG.md` | Prioritized work queue with resume triggers |
| `docs/ops/SESSIONS/2026-04-28-pm-nadirclaw-nssm.md` | Today's PM session — NSSM-ification details |
| `docs/governance/` | AI governance (Constitution, Operating Model, Prompt Templates) |
| `CODEX_CONSTITUTION.md` | Engineering law (binding) |
| `CONTRIBUTING.md` | Contribution guide |

---

## Tests

15 pytest files under `tests/`:

| Test | Covers |
|---|---|
| `test_classifier.py` | Binary complexity classifier accuracy |
| `test_credentials.py` | Atomic credential writes, alias resolution |
| `test_oauth.py` | OAuth PKCE flows |
| `test_routing.py` | Model resolution, agentic detection, cost estimation |
| `test_server.py` | FastAPI endpoints, streaming |
| `test_intent.py` | Intent detection |
| `test_pipeline.py` `test_pipeline_db.py` `test_pipeline_tracker.py` | Pipeline persistence |
| `test_blast.py` | Broadcast to many models |
| `test_service_manager.py` | NSSM helpers |
| `test_setup.py` `test_telemetry.py` `test_report.py` | Setup wizard, telemetry, reporting |

Run: `pytest -q`. CI: `.github/workflows/ci.yml` (Python 3.10/3.11/3.12).

---

## Key Dependencies

| Package | Purpose |
|---|---|
| `fastapi` + `uvicorn` | HTTP server |
| `litellm` | 100+ provider adapter |
| `sentence-transformers` | Embeddings for classifier |
| `numpy` | Centroid math |
| `python-dotenv` | `.env` loader |
| `httpx` | Async HTTP (Gemini cloudcode, OAuth callbacks) |
| `pydantic` | Request validation |
| `surrealdb` | Pipeline persistence (optional) |

Dev: `pytest`, `pytest-asyncio`, `ruff`, `mypy`.

---

## Quick Start (developer)

```bash
git clone https://github.com/AgileKnow23/NadirClaw.git
cd NadirClaw
pip install -e ".[dev]"
nadirclaw setup                # writes ~/.nadirclaw/.env interactively
nadirclaw auth gemini          # OAuth (browser flow)
nadirclaw serve                # http://localhost:8856
pytest -q                      # run tests
```

## Quick Start (operator — Bryan)

```powershell
# Status check
~/Desktop/dashboard-health.ps1

# Recovery (admin)
& 'C:\Users\Agile\Respositories\NadirClaw\scripts\fix_services_admin.ps1'

# Tail logs
Get-Content $HOME\.nadirclaw\logs\NadirClaw-Router.log -Tail 30 -Wait
Get-Content $HOME\Respositories\NadirClaw\sentinel.log -Tail 30 -Wait
```
