"""Minimal env-based configuration for NadirClaw."""

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
        return os.getenv("NADIRCLAW_SURREALDB_URL", "ws://localhost:8000")

    @property
    def SURREALDB_NS(self) -> str:
        return os.getenv("NADIRCLAW_SURREALDB_NS", "nadirclaw")

    @property
    def SURREALDB_DB(self) -> str:
        return os.getenv("NADIRCLAW_SURREALDB_DB", "nadirclaw")

    @property
    def SURREALDB_USER(self) -> str:
        return os.getenv("NADIRCLAW_SURREALDB_USER", "root")

    @property
    def SURREALDB_PASS(self) -> str:
        return os.getenv("NADIRCLAW_SURREALDB_PASS", "root")

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
