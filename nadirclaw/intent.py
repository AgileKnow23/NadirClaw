"""Multi-category intent classifier for NadirClaw pipeline routing.

Extends the binary simple/complex classifier to detect 8 intent categories
using the same sentence-transformer approach (cosine similarity to pre-computed
centroids) plus keyword overlay regex for signal boosting.

Intent categories:
    code_generation, code_review, architecture, debugging,
    security_analysis, documentation, general_qa, simple_qa
"""

import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("nadirclaw.intent")

_PKG_DIR = os.path.dirname(__file__)

# All recognized intent categories
INTENT_CATEGORIES = [
    "code_generation",
    "code_review",
    "architecture",
    "debugging",
    "security_analysis",
    "documentation",
    "general_qa",
    "simple_qa",
]

# Intents that trigger the multi-model pipeline
PIPELINE_INTENTS = {
    "code_generation",
    "code_review",
    "architecture",
    "debugging",
    "security_analysis",
    "documentation",
    "general_qa",
}


@dataclass
class IntentResult:
    """Result of intent classification."""
    intent: str
    confidence: float
    needs_pipeline: bool
    scores: Dict[str, float] = field(default_factory=dict)
    keyword_boost: Optional[str] = None
    latency_ms: int = 0


# ---------------------------------------------------------------------------
# Keyword overlay patterns — boost specific intents when keywords match
# ---------------------------------------------------------------------------

_KEYWORD_PATTERNS: Dict[str, re.Pattern] = {
    "code_generation": re.compile(
        r"\b("
        r"write (?:a |the |an )?(?:function|class|module|script|program|code|api|endpoint|component|test)"
        r"|implement (?:a |the |an )?"
        r"|create (?:a |the |an )?(?:function|class|module|script|api|endpoint|component|hook|decorator)"
        r"|build (?:a |the |an )?"
        r"|generate (?:code|function|class)"
        r"|code (?:for|that|to|which)"
        r")\b",
        re.IGNORECASE,
    ),
    "code_review": re.compile(
        r"\b("
        r"review (?:this|the|my) (?:code|pull request|pr|implementation|function|commit)"
        r"|check (?:this|the|my) (?:code|function|implementation) for"
        r"|code review"
        r"|pull request review"
        r"|review (?:for|the) (?:issues|problems|bugs|security)"
        r"|analyze (?:this|the|my) code"
        r")\b",
        re.IGNORECASE,
    ),
    "architecture": re.compile(
        r"\b("
        r"(?:design|architect|plan) (?:a |the |an )?(?:system|architecture|infrastructure|platform|schema)"
        r"|system design"
        r"|microservices? architecture"
        r"|data model"
        r"|database schema"
        r"|migration strategy"
        r"|technology stack"
        r"|scalab(?:le|ility)"
        r"|distributed system"
        r")\b",
        re.IGNORECASE,
    ),
    "debugging": re.compile(
        r"\b("
        r"debug (?:this|the|why|a)"
        r"|find (?:the )?(?:bug|root cause|issue|problem|error|leak)"
        r"|(?:why (?:is|does|do|did)|what (?:is causing|causes)) .{0,30}(?:error|fail|crash|hang|slow|break)"
        r"|root cause"
        r"|troubleshoot"
        r"|investigate (?:why|the|this|a)"
        r"|fix (?:this|the) (?:bug|error|crash|issue)"
        r"|memory leak"
        r"|race condition"
        r"|stack trace"
        r")\b",
        re.IGNORECASE,
    ),
    "security_analysis": re.compile(
        r"\b("
        r"security (?:audit|review|analysis|assessment|scan)"
        r"|vulnerabilit(?:y|ies)"
        r"|OWASP"
        r"|penetration test"
        r"|threat model"
        r"|(?:SQL |XSS |CSRF |injection|privilege escalation)"
        r"|security (?:best practices|compliance|hardening)"
        r"|audit (?:the|this|for) (?:security|auth)"
        r")\b",
        re.IGNORECASE,
    ),
    "documentation": re.compile(
        r"\b("
        r"(?:write|create|generate|update) (?:the )?(?:documentation|docs|readme|changelog|guide|runbook|spec)"
        r"|document (?:the|this|how)"
        r"|API (?:documentation|docs|reference)"
        r"|technical (?:writing|specification|spec)"
        r"|onboarding (?:guide|documentation|docs)"
        r")\b",
        re.IGNORECASE,
    ),
}


class IntentClassifier:
    """Multi-category intent classifier using semantic prototype centroids.

    Falls back to the binary simple/complex classification when:
    - Centroid files are not available
    - Confidence is below threshold
    - Intent is 'simple_qa'
    """

    def __init__(self):
        from nadirclaw.encoder import get_shared_encoder_sync
        self.encoder = get_shared_encoder_sync()
        self._centroids = self._load_centroids()
        if self._centroids:
            logger.info("IntentClassifier ready (%d categories)", len(self._centroids))
        else:
            logger.warning("IntentClassifier: no centroid files found. Run 'nadirclaw build-intent-centroids'.")

    @staticmethod
    def _load_centroids() -> Dict[str, np.ndarray]:
        """Load pre-computed centroid vectors for each intent category."""
        centroids = {}
        for cat in INTENT_CATEGORIES:
            path = os.path.join(_PKG_DIR, f"intent_{cat}_centroid.npy")
            if os.path.exists(path):
                centroids[cat] = np.load(path)
        return centroids

    def classify(self, prompt: str) -> IntentResult:
        """Classify a prompt into one of the 8 intent categories.

        Returns IntentResult with intent name, confidence, and whether
        the pipeline should be triggered.
        """
        from nadirclaw.settings import settings

        start = time.time()

        if not self._centroids:
            # No centroids available — fall back to simple_qa
            return IntentResult(
                intent="simple_qa",
                confidence=0.0,
                needs_pipeline=False,
                latency_ms=int((time.time() - start) * 1000),
            )

        # Encode prompt
        emb = self.encoder.encode([prompt], show_progress_bar=False)[0]
        emb = emb / np.linalg.norm(emb)

        # Compute cosine similarity to each centroid
        scores: Dict[str, float] = {}
        for cat, centroid in self._centroids.items():
            scores[cat] = float(np.dot(emb, centroid))

        # Apply keyword boost (+0.08 for matching patterns)
        keyword_boost = None
        for cat, pattern in _KEYWORD_PATTERNS.items():
            if cat in scores and pattern.search(prompt):
                scores[cat] += 0.08
                keyword_boost = cat

        # Find best match
        best_intent = max(scores, key=scores.get)
        best_score = scores[best_intent]

        # Compute confidence as gap between best and second-best
        sorted_scores = sorted(scores.values(), reverse=True)
        if len(sorted_scores) >= 2:
            confidence = sorted_scores[0] - sorted_scores[1]
        else:
            confidence = best_score

        # Determine if pipeline is needed
        threshold = settings.INTENT_CONFIDENCE_THRESHOLD
        if confidence < threshold:
            # Low confidence — fall back to simple routing
            needs_pipeline = False
        elif best_intent == "simple_qa":
            needs_pipeline = False
        elif best_intent in PIPELINE_INTENTS:
            needs_pipeline = settings.PIPELINE_ENABLED
        else:
            needs_pipeline = False

        latency_ms = int((time.time() - start) * 1000)

        return IntentResult(
            intent=best_intent,
            confidence=confidence,
            needs_pipeline=needs_pipeline,
            scores=scores,
            keyword_boost=keyword_boost,
            latency_ms=latency_ms,
        )


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_singleton: Optional[IntentClassifier] = None


def get_intent_classifier() -> IntentClassifier:
    """Return the singleton IntentClassifier instance."""
    global _singleton
    if _singleton is None:
        _singleton = IntentClassifier()
    return _singleton
