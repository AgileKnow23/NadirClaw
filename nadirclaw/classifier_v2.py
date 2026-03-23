"""
NadirClaw Extended Classifier
==============================
Wraps NadirClaw's existing binary classifier and adds:
  - Task type detection (code / reasoning / general / creative / etc.)
  - Privacy sensitivity detection
  - Speed priority detection
  - Multi-tier routing table (6 tiers instead of 2)

Drop this into nadirclaw/ alongside classifier.py.
Import and use ClassifierV2 in server.py instead of the original Classifier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# ── Keyword pattern maps ──────────────────────────────────────────────────────

_CODE_PATTERNS = re.compile(
    r"\b(code|function|class|implement|debug|refactor|unit test|pytest|unittest|"
    r"bug|fix|compile|runtime|algorithm|data structure|api|endpoint|sql|query|"
    r"script|bash|shell|dockerfile|kubernetes|deploy|ci/cd|github actions|"
    r"typescript|javascript|python|rust|go|java|c\+\+|react|django|fastapi)\b",
    re.IGNORECASE,
)

_REASONING_PATTERNS = re.compile(
    r"\b(reason|prove|proof|derive|calculate|solve|math|equation|logic|"
    r"deduce|infer|analyse|analyze|why does|explain why|cause|effect|"
    r"hypothesis|theorem|formal|step by step|think through|decide|decision)\b",
    re.IGNORECASE,
)

_ARCHITECTURE_PATTERNS = re.compile(
    r"\b(design|architect|system design|distributed|microservice|monolith|"
    r"scalab|database schema|data model|infrastructure|cloud|aws|gcp|azure|"
    r"consensus|replication|sharding|caching|message queue|event driven)\b",
    re.IGNORECASE,
)

_SECURITY_PATTERNS = re.compile(
    r"\b(security|vulnerability|exploit|injection|xss|csrf|auth|oauth|jwt|"
    r"encrypt|hash|tls|ssl|firewall|penetration|audit|compliance|gdpr|pii|"
    r"sensitive|confidential|private key|secret)\b",
    re.IGNORECASE,
)

_CREATIVE_PATTERNS = re.compile(
    r"\b(write|story|poem|blog|essay|creative|fiction|narrative|character|"
    r"plot|marketing|copy|advertisement|slogan|pitch|script|dialogue)\b",
    re.IGNORECASE,
)

_PRIVACY_PATTERNS = re.compile(
    r"\b(confidential|private|internal|secret|proprietary|sensitive|pii|"
    r"personal data|gdpr|hipaa|don.t share|keep local|no cloud)\b",
    re.IGNORECASE,
)

_SPEED_PATTERNS = re.compile(
    r"\b(quick|fast|asap|urgent|briefly|short|tl;dr|summary|one.liner|"
    r"in a sentence|give me just)\b",
    re.IGNORECASE,
)


@dataclass
class ClassificationV2:
    """Extended classification result."""
    complexity: float          # 0.0–1.0 from original classifier
    tier: str                  # "trivial" | "simple" | "moderate" | "complex" | "expert"
    task_type: str             # dominant task type
    privacy_required: bool
    speed_priority: bool
    routed_model: str          # final model string to use
    pipeline_recommended: bool


def classify_v2(
    prompt: str,
    base_complexity: float,    # from NadirClaw's existing classifier.py
) -> ClassificationV2:
    """
    Extend the base complexity score with task type + routing decision.
    Call NadirClaw's existing Classifier first to get base_complexity,
    then pass both values here.
    """
    from .role_registry import best_model_for, ROLE_REGISTRY
    from .settings import settings

    AUTO_PIPELINE_COMPLEXITY_THRESHOLD = settings.PIPELINE_V2_THRESHOLD

    # ── Detect task type ────────────────────────────────────────────────────
    task_type = _detect_task_type(prompt)

    # ── Detect modifiers ────────────────────────────────────────────────────
    privacy_required = bool(_PRIVACY_PATTERNS.search(prompt))
    speed_priority = bool(_SPEED_PATTERNS.search(prompt))

    # ── Map complexity to tier ───────────────────────────────────────────────
    if base_complexity < 0.15:
        tier = "trivial"
    elif base_complexity < 0.40:
        tier = "simple"
    elif base_complexity < 0.65:
        tier = "moderate"
    elif base_complexity < 0.82:
        tier = "complex"
    else:
        tier = "expert"

    # ── Effective complexity: speed_priority brings it down slightly ─────────
    effective_complexity = base_complexity
    if speed_priority:
        effective_complexity = max(0.0, base_complexity - 0.15)

    # ── Pick model ───────────────────────────────────────────────────────────
    model = best_model_for(
        task_type=task_type,
        complexity=effective_complexity,
        privacy_required=privacy_required,
        prefer_local=privacy_required or effective_complexity < 0.40,
    )

    pipeline_recommended = effective_complexity >= AUTO_PIPELINE_COMPLEXITY_THRESHOLD

    return ClassificationV2(
        complexity=base_complexity,
        tier=tier,
        task_type=task_type,
        privacy_required=privacy_required,
        speed_priority=speed_priority,
        routed_model=model.model_id,
        pipeline_recommended=pipeline_recommended,
    )


def _detect_task_type(prompt: str) -> str:
    """Return the dominant task type for a prompt."""
    scores = {
        "architecture": len(_ARCHITECTURE_PATTERNS.findall(prompt)) * 1.5,
        "code": len(_CODE_PATTERNS.findall(prompt)),
        "reasoning": len(_REASONING_PATTERNS.findall(prompt)),
        "security_analysis": len(_SECURITY_PATTERNS.findall(prompt)),
        "creative": len(_CREATIVE_PATTERNS.findall(prompt)),
    }
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else "general"
