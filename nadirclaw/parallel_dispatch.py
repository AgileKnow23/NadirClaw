"""Parallel multi-model dispatch for NadirClaw.

For moderate and complex prompts, dispatches to two models in parallel,
collects both responses, runs a fast local judge to pick the best,
and formats a combined response with the preferred answer first.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from nadirclaw.dispatch import RateLimitExhausted, dispatch_raw
from nadirclaw.settings import settings

logger = logging.getLogger("nadirclaw.parallel")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ParallelResult:
    """Holds both responses, judgment, and metadata from a parallel dispatch."""

    response_a: str = ""
    response_b: str = ""
    model_a: str = ""
    model_b: str = ""
    preferred: str = ""          # "A" or "B"
    rationale: str = ""
    strengths_a: str = ""
    strengths_b: str = ""
    latency_a_ms: int = 0
    latency_b_ms: int = 0
    tokens_a: Dict[str, int] = field(default_factory=lambda: {"prompt": 0, "completion": 0})
    tokens_b: Dict[str, int] = field(default_factory=lambda: {"prompt": 0, "completion": 0})
    error_a: Optional[str] = None
    error_b: Optional[str] = None
    judge_failed: bool = False

    @property
    def preferred_model(self) -> str:
        return self.model_a if self.preferred == "A" else self.model_b

    @property
    def preferred_response(self) -> str:
        return self.response_a if self.preferred == "A" else self.response_b

    @property
    def alternative_response(self) -> str:
        return self.response_b if self.preferred == "A" else self.response_a

    @property
    def alternative_model(self) -> str:
        return self.model_b if self.preferred == "A" else self.model_a


# ---------------------------------------------------------------------------
# Model pairing matrix
# ---------------------------------------------------------------------------

def _friendly_name(model: str) -> str:
    """Human-readable model name for display."""
    parts = model.split("/")
    return parts[-1] if parts else model


def get_model_pair(tier: str, task_type: str) -> Tuple[str, str]:
    """Return (model_a, model_b) for the given tier and task type.

    model_a is typically local, model_b is cloud (for moderate tier).
    For complex tier, both are cloud-class models.
    """
    _MODERATE_PAIRS: Dict[str, Tuple[str, str]] = {
        "general":      (settings.SIMPLE_MODEL,          settings.FAST_CLOUD_MODEL),
        "code":         (settings.CODE_MODEL,            settings.CODE_CLOUD_MODEL),
        "reasoning":    (settings.LOCAL_REASONING_MODEL,  settings.FAST_CLOUD_MODEL),
        "creative":     (settings.SIMPLE_MODEL,          settings.FAST_CLOUD_MODEL),
        "architecture": (settings.SIMPLE_MODEL,          settings.FAST_CLOUD_MODEL),
    }

    _COMPLEX_PAIRS: Dict[str, Tuple[str, str]] = {
        "general":   (settings.COMPLEX_MODEL,     settings.FAST_CLOUD_MODEL),
        "code":      (settings.CODE_CLOUD_MODEL,  settings.COMPLEX_MODEL),
        "reasoning": (settings.CODE_CLOUD_MODEL,  settings.COMPLEX_MODEL),
        "creative":  (settings.COMPLEX_MODEL,     settings.FAST_CLOUD_MODEL),
    }

    if tier == "moderate":
        return _MODERATE_PAIRS.get(task_type, (settings.SIMPLE_MODEL, settings.FAST_CLOUD_MODEL))
    elif tier == "complex":
        return _COMPLEX_PAIRS.get(task_type, (settings.COMPLEX_MODEL, settings.FAST_CLOUD_MODEL))
    else:
        # Fallback for unknown tiers
        return (settings.SIMPLE_MODEL, settings.FAST_CLOUD_MODEL)


# ---------------------------------------------------------------------------
# Judge
# ---------------------------------------------------------------------------

_JUDGE_SYSTEM_PROMPT = """\
You are a response quality judge. You will be given two responses (A and B) \
to the same user prompt. Pick the better response.

Output ONLY valid JSON with these exact keys:
{"preferred": "A", "rationale": "brief reason", "strengths_a": "...", "strengths_b": "..."}

Rules:
- "preferred" must be "A" or "B"
- Be concise (each field under 80 chars)
- Judge on accuracy, completeness, clarity, and relevance
- If both are equal quality, prefer the more concise one"""


async def _run_judge(
    prompt: str,
    response_a: str,
    response_b: str,
    judge_model: str,
) -> Dict[str, str]:
    """Run the judge model to pick the better response.

    Returns {"preferred": "A"|"B", "rationale": "...", "strengths_a": "...", "strengths_b": "..."}.
    On failure returns a neutral fallback.
    """
    user_content = (
        f"**User prompt:**\n{prompt[:2000]}\n\n"
        f"**Response A:**\n{response_a[:3000]}\n\n"
        f"**Response B:**\n{response_b[:3000]}"
    )

    messages = [
        {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    try:
        result = await dispatch_raw(
            judge_model, messages, temperature=0.1, max_tokens=256,
        )
        raw = result.get("content", "").strip()
        # Extract JSON from response (handle markdown code blocks)
        if "```" in raw:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                raw = raw[start:end]
        parsed = json.loads(raw)
        preferred = parsed.get("preferred", "A").upper()
        if preferred not in ("A", "B"):
            preferred = "A"
        return {
            "preferred": preferred,
            "rationale": str(parsed.get("rationale", ""))[:200],
            "strengths_a": str(parsed.get("strengths_a", ""))[:200],
            "strengths_b": str(parsed.get("strengths_b", ""))[:200],
        }
    except Exception as exc:
        logger.warning("Judge failed (%s)  -- defaulting to A", exc)
        return {
            "preferred": "A",
            "rationale": "Judge unavailable",
            "strengths_a": "",
            "strengths_b": "",
        }


# ---------------------------------------------------------------------------
# Parallel dispatch
# ---------------------------------------------------------------------------

async def parallel_dispatch(
    messages: List[Dict[str, str]],
    model_a: str,
    model_b: str,
    judge_model: str,
    prompt_text: str = "",
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> ParallelResult:
    """Dispatch two models in parallel, judge, and return combined result.

    Raises Exception only if BOTH models fail.
    """
    result = ParallelResult(model_a=model_a, model_b=model_b)

    # --- Dispatch both models concurrently ---
    async def _call_model(model: str) -> Tuple[Dict[str, Any], int]:
        t0 = time.monotonic()
        resp = await dispatch_raw(model, messages, temperature, max_tokens)
        elapsed = int((time.monotonic() - t0) * 1000)
        return resp, elapsed

    task_a = asyncio.create_task(_call_model(model_a))
    task_b = asyncio.create_task(_call_model(model_b))

    results = await asyncio.gather(task_a, task_b, return_exceptions=True)
    resp_a, resp_b = results

    # --- Process model A result ---
    if isinstance(resp_a, Exception):
        result.error_a = str(resp_a)
        logger.warning("Parallel model A (%s) failed: %s", model_a, resp_a)
    else:
        data_a, latency_a = resp_a
        result.response_a = data_a.get("content", "")
        result.latency_a_ms = latency_a
        result.tokens_a = {
            "prompt": data_a.get("prompt_tokens", 0),
            "completion": data_a.get("completion_tokens", 0),
        }

    # --- Process model B result ---
    if isinstance(resp_b, Exception):
        result.error_b = str(resp_b)
        logger.warning("Parallel model B (%s) failed: %s", model_b, resp_b)
    else:
        data_b, latency_b = resp_b
        result.response_b = data_b.get("content", "")
        result.latency_b_ms = latency_b
        result.tokens_b = {
            "prompt": data_b.get("prompt_tokens", 0),
            "completion": data_b.get("completion_tokens", 0),
        }

    # --- Handle failure cases ---
    if result.error_a and result.error_b:
        raise RuntimeError(
            f"Both parallel models failed. A ({model_a}): {result.error_a}; "
            f"B ({model_b}): {result.error_b}"
        )

    if result.error_a:
        # Only B succeeded
        result.preferred = "B"
        result.rationale = f"{_friendly_name(model_a)} failed  -- using {_friendly_name(model_b)}"
        result.judge_failed = True
        return result

    if result.error_b:
        # Only A succeeded
        result.preferred = "A"
        result.rationale = f"{_friendly_name(model_b)} failed  -- using {_friendly_name(model_a)}"
        result.judge_failed = True
        return result

    # --- Both succeeded  -- run judge ---
    judgment = await _run_judge(
        prompt_text or "N/A",
        result.response_a,
        result.response_b,
        judge_model,
    )
    result.preferred = judgment["preferred"]
    result.rationale = judgment["rationale"]
    result.strengths_a = judgment["strengths_a"]
    result.strengths_b = judgment["strengths_b"]
    result.judge_failed = judgment["rationale"] == "Judge unavailable"

    return result


# ---------------------------------------------------------------------------
# Response formatter
# ---------------------------------------------------------------------------

def format_parallel_response(result: ParallelResult) -> str:
    """Format a ParallelResult into user-facing markdown.

    Preferred response first (undecorated), then judgment section,
    then alternative in a collapsible <details> block.
    """
    parts = []

    # Preferred response (shown naturally)
    parts.append(result.preferred_response)

    # Judgment separator
    judge_model_name = _friendly_name(settings.PARALLEL_JUDGE_MODEL)
    pref_name = _friendly_name(result.preferred_model)
    alt_name = _friendly_name(result.alternative_model)

    parts.append("\n\n---")
    if result.judge_failed and result.error_a or result.error_b:
        # One model failed  -- simple note
        parts.append(f"**Note:** {result.rationale}")
    else:
        parts.append(f"**Judgment** ({judge_model_name})")
        parts.append(f"Preferred: **{pref_name}**  -- {result.rationale}")

    # Alternative response in collapsible block (only if both succeeded)
    if not result.error_a and not result.error_b:
        alt_latency = result.latency_b_ms if result.preferred == "A" else result.latency_a_ms
        parts.append("")
        parts.append(
            f"<details><summary>Alternative response from {alt_name} "
            f"({alt_latency}ms)</summary>\n"
        )
        parts.append(result.alternative_response)
        parts.append("\n</details>")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Gate function
# ---------------------------------------------------------------------------

def should_parallel_dispatch(
    complexity: float,
    tier: str,
    privacy_required: bool = False,
    speed_priority: bool = False,
) -> bool:
    """Decide whether to use parallel dispatch for this request.

    Returns True when: feature enabled AND tier is moderate/complex
    AND not privacy/speed flagged.
    """
    if not settings.PARALLEL_DISPATCH_ENABLED:
        return False
    if tier not in ("moderate", "complex"):
        return False
    if privacy_required:
        return False
    if speed_priority:
        return False
    return True
