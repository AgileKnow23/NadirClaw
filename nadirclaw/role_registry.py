"""
NadirClaw Role Registry
=======================
Maps each available model to its specialty domains and confidence weights.
Used by the PipelineOrchestrator to assign the best model to each subtask.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ModelRole:
    """Defines a model's identity, backend, and specialty confidence scores."""
    model_id: str                        # litellm / ollama model string
    display_name: str
    backend: str                         # "litellm" | "ollama" | "gemini" | "claude_cli" | "codex_cli"
    local: bool                          # True = runs on-device, no API cost
    privacy_safe: bool                   # True = can handle sensitive data
    max_complexity: float                # 0.0–1.0 upper bound this model handles well
    # Specialty confidence per domain (0.0 = avoid, 1.0 = best-in-class)
    specialties: Dict[str, float] = field(default_factory=dict)
    # Soft latency estimate in seconds (used for pipeline scheduling)
    latency_hint_s: float = 5.0
    # Hard token limit for a single call
    context_window: int = 8192


# ─── Registry ────────────────────────────────────────────────────────────────

ROLE_REGISTRY: Dict[str, ModelRole] = {

    # ── Trivial / ultra-fast local ──────────────────────────────────────────
    "ollama/qwen2.5:3b": ModelRole(
        model_id="ollama/qwen2.5:3b",
        display_name="Qwen 2.5 3B (local)",
        backend="ollama",
        local=True,
        privacy_safe=True,
        max_complexity=0.25,
        latency_hint_s=4.0,
        context_window=4096,
        specialties={
            "general": 0.45,
            "summarization": 0.50,
            "classification": 0.55,
            "trivial": 0.90,
        },
    ),

    # ── General local ───────────────────────────────────────────────────────
    "ollama/qwen3:8b": ModelRole(
        model_id="ollama/qwen3:8b",
        display_name="Qwen 3 8B (local)",
        backend="ollama",
        local=True,
        privacy_safe=True,
        max_complexity=0.55,
        latency_hint_s=12.0,
        context_window=8192,
        specialties={
            "general": 0.70,
            "summarization": 0.68,
            "creative": 0.60,
            "classification": 0.65,
            "planning": 0.55,
        },
    ),

    # ── Local reasoning specialist ──────────────────────────────────────────
    "ollama/deepseek-r1:8b": ModelRole(
        model_id="ollama/deepseek-r1:8b",
        display_name="DeepSeek R1 8B (local reasoning)",
        backend="ollama",
        local=True,
        privacy_safe=True,
        max_complexity=0.70,
        latency_hint_s=18.0,
        context_window=8192,
        specialties={
            "reasoning": 0.85,
            "math": 0.82,
            "logic": 0.80,
            "security_analysis": 0.68,
            "critique": 0.72,
            "general": 0.58,
        },
    ),

    # ── Local code specialist ───────────────────────────────────────────────
    "ollama/qwen3-coder:30b": ModelRole(
        model_id="ollama/qwen3-coder:30b",
        display_name="Qwen 3 Coder 30B (local)",
        backend="ollama",
        local=True,
        privacy_safe=True,
        max_complexity=0.75,
        latency_hint_s=25.0,
        context_window=16384,
        specialties={
            "code": 0.88,
            "code_review": 0.84,
            "testing": 0.80,
            "debugging": 0.82,
            "architecture": 0.70,
            "general": 0.62,
        },
    ),

    # ── Fast cloud (Gemini Flash) ───────────────────────────────────────────
    "gemini/gemini-2.0-flash": ModelRole(
        model_id="gemini/gemini-2.0-flash",
        display_name="Gemini 2.0 Flash",
        backend="gemini",
        local=False,
        privacy_safe=False,
        max_complexity=0.80,
        latency_hint_s=2.0,
        context_window=32768,
        specialties={
            "general": 0.78,
            "summarization": 0.82,
            "creative": 0.75,
            "planning": 0.80,
            "decomposition": 0.85,   # great at breaking tasks down
            "synthesis": 0.78,
            "classification": 0.80,
        },
    ),

    # ── Cloud code agent (Codex CLI) ────────────────────────────────────────
    "openai-codex/gpt-5-codex": ModelRole(
        model_id="openai-codex/gpt-5-codex",
        display_name="GPT-5 Codex (Codex CLI)",
        backend="codex_cli",
        local=False,
        privacy_safe=False,
        max_complexity=0.95,
        latency_hint_s=3.0,
        context_window=32768,
        specialties={
            "code": 0.93,
            "testing": 0.90,
            "debugging": 0.91,
            "code_review": 0.88,
            "agentic_code": 0.95,
            "architecture": 0.82,
        },
    ),

    # ── Premium cloud (Claude Opus) ─────────────────────────────────────────
    "claude-opus-4-6": ModelRole(
        model_id="claude-opus-4-6",
        display_name="Claude Opus 4.6",
        backend="claude_cli",
        local=False,
        privacy_safe=False,
        max_complexity=1.0,
        latency_hint_s=20.0,
        context_window=200000,
        specialties={
            "architecture": 0.95,
            "reasoning": 0.92,
            "security_analysis": 0.90,
            "security_review": 0.92,
            "synthesis": 0.95,
            "tech_lead_review": 0.95,
            "critique": 0.90,
            "planning": 0.92,
            "general": 0.88,
            "creative": 0.85,
            "code": 0.85,
            "code_review": 0.90,
        },
    ),
}


# ─── Helpers ─────────────────────────────────────────────────────────────────

TASK_TYPES = [
    "general",
    "code",
    "reasoning",
    "math",
    "architecture",
    "security_analysis",
    "security_review",
    "testing",
    "debugging",
    "code_review",
    "tech_lead_review",
    "creative",
    "summarization",
    "planning",
    "decomposition",
    "synthesis",
    "critique",
    "classification",
    "trivial",
    "agentic_code",
]


def best_model_for(
    task_type: str,
    complexity: float,
    privacy_required: bool = False,
    prefer_local: bool = False,
    exclude: Optional[List[str]] = None,
) -> ModelRole:
    """
    Pick the best model for a given task type and complexity score.

    Priority:
      1. Filter by privacy_safe if required
      2. Filter by max_complexity >= complexity
      3. If prefer_local, try local-only first
      4. Score = specialty_confidence * (1 - complexity_gap_penalty)
      5. Return highest scorer
    """
    exclude = exclude or []
    candidates = [
        m for m in ROLE_REGISTRY.values()
        if m.model_id not in exclude
        and m.max_complexity >= complexity
        and (not privacy_required or m.privacy_safe)
    ]

    if prefer_local:
        local_candidates = [m for m in candidates if m.local]
        if local_candidates:
            candidates = local_candidates

    if not candidates:
        # Fallback: return the most capable model
        return ROLE_REGISTRY["claude-opus-4-6"]

    def score(model: ModelRole) -> float:
        specialty = model.specialties.get(task_type, model.specialties.get("general", 0.3))
        # Small penalty if the task is near or above the model's comfort zone
        headroom = model.max_complexity - complexity
        headroom_bonus = min(headroom * 0.2, 0.1)
        return specialty + headroom_bonus

    return max(candidates, key=score)
