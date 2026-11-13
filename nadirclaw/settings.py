"""Minimal env-based configuration for NadirClaw."""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from ~/.nadirclaw/.env if it exists
_nadirclaw_dir = Path.home() / ".nadirclaw"
_env_file = _nadirclaw_dir / ".env"
if _env_file.exists():
    load_dotenv(_env_file)
else:
    # Fallback to current directory .env
    load_dotenv()


#: One SurrealDB for the machine, at an explicit address. "localhost" is not
#: used: it resolved to a second server on [::1] on this machine.
DEFAULT_SURREALDB_URL = "ws://127.0.0.1:8000"
_SURREAL_CONFIG_MAX_BYTES = 64 * 1024


def surreal_private_config() -> dict:
    """The private SurrealDB config shared by local clients (outside Git):
    SURREALDB_CONFIG or ~/.gcreds/surrealdb.json. Its ``clients.nadirclaw``
    entry wins over top-level keys. {} when absent or unreadable; values are
    never logged."""
    path = Path(os.getenv("SURREALDB_CONFIG") or (Path.home() / ".gcreds" / "surrealdb.json"))
    try:
        if not path.is_file() or path.stat().st_size > _SURREAL_CONFIG_MAX_BYTES:
            return {}
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    clients = data.get("clients")
    entry = clients.get("nadirclaw") if isinstance(clients, dict) else None
    merged = {k: v for k, v in data.items() if k != "clients"}
    merged.update(entry if isinstance(entry, dict) else {})
    return merged


def _ws_url(url: str) -> str:
    url = url.rstrip("/")
    if url.startswith("http://"):
        return "ws://" + url[len("http://"):]
    if url.startswith("https://"):
        return "wss://" + url[len("https://"):]
    return url


class Settings:
    """All configuration from environment variables."""

    @property
    def AUTH_TOKEN(self) -> str:
        return os.getenv("NADIRCLAW_AUTH_TOKEN", "")

    @property
    def SIMPLE_MODEL(self) -> str:
        """Model for simple prompts. Falls back to last model in MODELS list."""
        explicit = os.getenv("NADIRCLAW_SIMPLE_MODEL", "")
        if explicit:
            return explicit
        models = self.MODELS
        return models[-1] if models else "gemini-3-flash-preview"

    @property
    def COMPLEX_MODEL(self) -> str:
        """Model for complex prompts. Falls back to first model in MODELS list."""
        explicit = os.getenv("NADIRCLAW_COMPLEX_MODEL", "")
        if explicit:
            return explicit
        models = self.MODELS
        return models[0] if models else "openai-codex/gpt-5.3-codex"

    @property
    def MODELS(self) -> list[str]:
        raw = os.getenv(
            "NADIRCLAW_MODELS",
            "openai-codex/gpt-5.3-codex,gemini-3-flash-preview",
        )
        return [m.strip() for m in raw.split(",") if m.strip()]

    @property
    def ANTHROPIC_API_KEY(self) -> str:
        return os.getenv("ANTHROPIC_API_KEY", "")

    @property
    def OPENAI_API_KEY(self) -> str:
        return os.getenv("OPENAI_API_KEY", "")

    @property
    def GEMINI_API_KEY(self) -> str:
        return os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", "")

    @property
    def OLLAMA_API_BASE(self) -> str:
        return os.getenv("OLLAMA_API_BASE", "http://localhost:11434")

    @property
    def CONFIDENCE_THRESHOLD(self) -> float:
        return float(os.getenv("NADIRCLAW_CONFIDENCE_THRESHOLD", "0.06"))

    @property
    def PORT(self) -> int:
        return int(os.getenv("NADIRCLAW_PORT", "8856"))

    @property
    def LOG_RAW(self) -> bool:
        """When True, log full raw request messages and response content."""
        return os.getenv("NADIRCLAW_LOG_RAW", "").lower() in ("1", "true", "yes")

    @property
    def LOG_DIR(self) -> Path:
        return Path(os.getenv("NADIRCLAW_LOG_DIR", "~/.nadirclaw/logs")).expanduser()

    @property
    def CREDENTIALS_FILE(self) -> Path:
        return Path.home() / ".nadirclaw" / "credentials.json"

    @property
    def REASONING_MODEL(self) -> str:
        """Model for reasoning tasks. Falls back to COMPLEX_MODEL."""
        return os.getenv("NADIRCLAW_REASONING_MODEL", "") or self.COMPLEX_MODEL

    @property
    def FREE_MODEL(self) -> str:
        """Free fallback model. Falls back to SIMPLE_MODEL."""
        return os.getenv("NADIRCLAW_FREE_MODEL", "") or self.SIMPLE_MODEL

    @property
    def SURREALDB_URL(self) -> str:
        url = os.getenv("NADIRCLAW_SURREALDB_URL") or surreal_private_config().get("url") or DEFAULT_SURREALDB_URL
        return _ws_url(str(url))

    @property
    def SURREALDB_HTTP_URL(self) -> str:
        """Same server as SURREALDB_URL, for the HTTP /sql endpoint."""
        url = self.SURREALDB_URL
        for ws, http in (("wss://", "https://"), ("ws://", "http://")):
            if url.startswith(ws):
                url = http + url[len(ws):]
        return url.removesuffix("/rpc")

    @property
    def SURREALDB_NS(self) -> str:
        return os.getenv("NADIRCLAW_SURREALDB_NS", "nadirclaw")

    @property
    def SURREALDB_DB(self) -> str:
        return os.getenv("NADIRCLAW_SURREALDB_DB", "nadirclaw")

    @property
    def SURREALDB_USER(self) -> str:
        """Env, then the private config; no built-in default."""
        return os.getenv("NADIRCLAW_SURREALDB_USER") or str(surreal_private_config().get("user") or "")

    @property
    def SURREALDB_PASS(self) -> str:
        """Env, then the private config; no built-in default."""
        return os.getenv("NADIRCLAW_SURREALDB_PASS") or str(surreal_private_config().get("password") or "")

    @property
    def SURREALDB_ENABLED(self) -> bool:
        return os.getenv("NADIRCLAW_SURREALDB_ENABLED", "true").lower() in ("1", "true", "yes")

    # ------------------------------------------------------------------
    # BLAST prompt optimizer settings
    # ------------------------------------------------------------------

    @property
    def BLAST_ENABLED(self) -> bool:
        """Enable BLAST prompt restructuring before pipeline builder step."""
        return os.getenv("NADIRCLAW_BLAST_ENABLED", "true").lower() in ("1", "true", "yes")

    @property
    def BLAST_SKIP_SIMPLE(self) -> bool:
        """Skip BLAST optimization for simple_qa intent (too trivial to restructure)."""
        return os.getenv("NADIRCLAW_BLAST_SKIP_SIMPLE", "true").lower() in ("1", "true", "yes")

    @property
    def BLAST_MODEL(self) -> str:
        """Local model used for BLAST prompt restructuring. Should be fast."""
        return os.getenv("NADIRCLAW_BLAST_MODEL", "") or self.SIMPLE_MODEL

    # ------------------------------------------------------------------
    # Pipeline settings
    # ------------------------------------------------------------------

    @property
    def PIPELINE_ENABLED(self) -> bool:
        """Enable the multi-model pipeline (Builder → Judge → Compressor)."""
        return os.getenv("NADIRCLAW_PIPELINE_ENABLED", "true").lower() in ("1", "true", "yes")

    @property
    def PIPELINE_BUILDER(self) -> str:
        """Default builder model for pipeline execution."""
        return os.getenv("NADIRCLAW_PIPELINE_BUILDER", "") or self.COMPLEX_MODEL

    @property
    def PIPELINE_JUDGE(self) -> str:
        """Default judge model for pipeline execution."""
        return os.getenv("NADIRCLAW_PIPELINE_JUDGE", "") or self.REASONING_MODEL

    @property
    def PIPELINE_COMPRESSOR(self) -> str:
        """Default compressor model for pipeline execution (small, fast model)."""
        return os.getenv("NADIRCLAW_PIPELINE_COMPRESSOR", "") or self.SIMPLE_MODEL

    @property
    def PIPELINE_MAX_STEPS(self) -> int:
        """Maximum pipeline steps before termination."""
        return int(os.getenv("NADIRCLAW_PIPELINE_MAX_STEPS", "5"))

    @property
    def INTENT_CONFIDENCE_THRESHOLD(self) -> float:
        """Minimum confidence for intent classification before falling back."""
        return float(os.getenv("NADIRCLAW_INTENT_CONFIDENCE_THRESHOLD", "0.10"))

    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Pipeline V2 (multi-model orchestrator) settings
    # ------------------------------------------------------------------

    @property
    def PIPELINE_V2_ENABLED(self) -> bool:
        """Enable Pipeline V2 orchestrator for high-complexity requests."""
        return os.getenv("NADIRCLAW_PIPELINE_V2_ENABLED", "false").lower() in ("1", "true", "yes")

    @property
    def PIPELINE_V2_THRESHOLD(self) -> float:
        """Complexity score threshold to auto-trigger Pipeline V2."""
        return float(os.getenv("NADIRCLAW_PIPELINE_V2_THRESHOLD", "0.82"))

    @property
    def TRIVIAL_MODEL(self) -> str:
        """Ultra-fast trivial model."""
        return os.getenv("NADIRCLAW_TRIVIAL_MODEL", "ollama/qwen2.5:3b")

    @property
    def CODE_MODEL(self) -> str:
        """Local code specialist model."""
        return os.getenv("NADIRCLAW_CODE_MODEL", "ollama/qwen3-coder:30b")

    @property
    def FAST_CLOUD_MODEL(self) -> str:
        """Fast cloud model (Gemini Flash etc.)."""
        return os.getenv("NADIRCLAW_FAST_CLOUD_MODEL", "gemini/gemini-2.0-flash")

    @property
    def CODE_CLOUD_MODEL(self) -> str:
        """Cloud code agent model (Codex CLI)."""
        return os.getenv("NADIRCLAW_CODE_CLOUD_MODEL", "openai-codex/gpt-5-codex")

    @property
    def LOCAL_REASONING_MODEL(self) -> str:
        """Local reasoning model for low-complexity reasoning tasks."""
        return os.getenv("NADIRCLAW_LOCAL_REASONING_MODEL", "ollama/deepseek-r1:8b")

    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Parallel dispatch settings
    # ------------------------------------------------------------------

    @property
    def PARALLEL_DISPATCH_ENABLED(self) -> bool:
        """Enable parallel multi-model dispatch for moderate/complex tiers."""
        return os.getenv("NADIRCLAW_PARALLEL_DISPATCH_ENABLED", "false").lower() in ("1", "true", "yes")

    @property
    def PARALLEL_JUDGE_MODEL(self) -> str:
        """Fast local model used to judge parallel responses."""
        return os.getenv("NADIRCLAW_PARALLEL_JUDGE_MODEL", "") or self.TRIVIAL_MODEL

    # ------------------------------------------------------------------

    @property
    def has_explicit_tiers(self) -> bool:
        """True if SIMPLE_MODEL and COMPLEX_MODEL are explicitly set via env."""
        return bool(
            os.getenv("NADIRCLAW_SIMPLE_MODEL") and os.getenv("NADIRCLAW_COMPLEX_MODEL")
        )

    @property
    def tier_models(self) -> list[str]:
        """Deduplicated list of [COMPLEX_MODEL, SIMPLE_MODEL]."""
        models = [self.COMPLEX_MODEL]
        if self.SIMPLE_MODEL != self.COMPLEX_MODEL:
            models.append(self.SIMPLE_MODEL)
        return models


settings = Settings()
