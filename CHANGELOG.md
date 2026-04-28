# Changelog

All notable changes to NadirClaw will be documented in this file.

## [0.5.1] - 2026-04-28

### Fixed
- **NadirClaw is now its own NSSM service (`NadirClaw-Router`)**. Previously the sentinel relaunched it via `Start-Process python.exe -c "...cli.main()"` from LocalSystem context — the child crashed silently (no PATH for user-profile site-packages, stderr swallowed). The recovery budget burned 4/hr for hours, producing rate-limit Telegram alerts. Service now runs `nadirclaw.exe serve` directly with `USERPROFILE`/`HOME`/`NADIRCLAW_USER_HOME` pinned to `C:\Users\Agile`.
- `sentinel.py:restart_nadirclaw()` now calls `_restart_windows_service("NadirClaw-Router")` instead of spawning Python — same pattern as `restart_ak_dashboard` and `restart_status_app`.

### Added
- `scripts/fix_services_admin.ps1` — idempotent recovery installer (stops :8856, removes old service, reinstalls, starts NadirClaw-Router + AkDashboard, restarts NadirClawSentinel).
- Logs: `~/.nadirclaw/logs/NadirClaw-Router.log` and `NadirClaw-Router-error.log` (NSSM-rotated at 10 MB).

## [0.3.0] - 2025-02-14

### Added
- OAuth login for all major providers: OpenAI, Anthropic, Google Gemini, Google Antigravity
- Interactive Anthropic login — choose between setup token or API key
- Gemini OAuth PKCE flow with browser-based authorization
- Antigravity OAuth with hardcoded public client credentials (matching OpenClaw)
- Provider-specific token refresh (OpenAI, Anthropic, Gemini, Antigravity)
- Atomic credential file writes to prevent corruption
- Port-in-use error handling for OAuth callback server
- Test suite with pytest (credentials, OAuth, classifier, server)
- CONTRIBUTING.md and CHANGELOG.md

### Changed
- Version is now single source of truth in `nadirclaw/__init__.py`
- Credential file writes use atomic temp-file-and-rename pattern
- Token refresh failures return `None` instead of silently returning stale tokens
- OAuth callback server binds to `localhost` (was `127.0.0.1`)

### Fixed
- Version mismatch between `__init__.py`, `cli.py`, `server.py`, and `pyproject.toml`
- README references to `nadirclaw auth gemini-cli` (now `nadirclaw auth gemini`)
- OAuth callback server getting stuck (now uses `serve_forever()`)

## [0.2.0] - 2025-01-20

### Added
- OpenAI OAuth login via Codex CLI
- Credential storage in `~/.nadirclaw/credentials.json`
- Environment variable fallback for API keys
- `nadirclaw auth` command group

## [0.1.0] - 2025-01-10

### Added
- Initial release
- Binary complexity classifier with sentence embeddings
- Smart routing between simple and complex models
- OpenAI-compatible API (`/v1/chat/completions`)
- SSE streaming support
- Rate limit fallback between tiers
- Gemini native SDK integration
- LiteLLM support for 100+ providers
- CLI: `serve`, `classify`, `status`, `build-centroids`
- OpenClaw and Codex onboarding commands
