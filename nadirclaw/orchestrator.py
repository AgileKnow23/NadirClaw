"""
NadirClaw Pipeline Orchestrator
================================
Decomposes complex prompts into subtasks, assigns models by specialty,
runs phases (sequential or parallel), applies weighted merging, then
passes through the Sr. Tech Lead → Security → QA review chain.

Drop this file into nadirclaw/ and wire it into server.py (see README).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .role_registry import ROLE_REGISTRY, ModelRole, best_model_for
from .settings import settings

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

AUTO_PIPELINE_COMPLEXITY_THRESHOLD = settings.PIPELINE_V2_THRESHOLD
MANUAL_PIPELINE_PREFIXES = ["@pipeline", "@team", "@fullstack"]

# ─── Data classes ─────────────────────────────────────────────────────────────


class PhaseMode(str, Enum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"


@dataclass
class SubTask:
    id: str
    description: str          # what this subtask must produce
    task_type: str            # from TASK_TYPES
    assigned_model: ModelRole
    depends_on: List[str] = field(default_factory=list)   # ids of prerequisite subtasks
    mode: PhaseMode = PhaseMode.PARALLEL
    result: Optional[str] = None
    error: Optional[str] = None
    latency_s: float = 0.0


@dataclass
class PipelinePlan:
    original_prompt: str
    subtasks: List[SubTask]
    review_chain: List[str]   # ordered list of model_ids for final review
    privacy_required: bool = False
    triggered_by: str = "auto"   # "auto" | "manual"


@dataclass
class PipelineResult:
    final_response: str
    plan: PipelinePlan
    subtask_results: List[SubTask]
    review_outputs: List[Dict[str, str]]
    total_latency_s: float
    models_used: List[str]


# ─── Dispatcher: calls any model backend ─────────────────────────────────────


# Shared httpx client for all orchestrator calls (created once, reused)
_shared_http_client: Optional[httpx.AsyncClient] = None


def _get_http_client() -> httpx.AsyncClient:
    """Get or create the shared httpx.AsyncClient."""
    global _shared_http_client
    if _shared_http_client is None or _shared_http_client.is_closed:
        _shared_http_client = httpx.AsyncClient(timeout=120.0)
    return _shared_http_client


async def _call_model(
    model: ModelRole,
    messages: List[Dict[str, str]],
    system: Optional[str] = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    nadirclaw_base_url: str = "http://localhost:8856/v1",
) -> str:
    """
    Dispatch a single call through NadirClaw's existing /v1/chat/completions
    endpoint, forcing a specific model by passing it explicitly.
    This reuses all of NadirClaw's auth/credential/fallback machinery.
    """
    payload_messages = []
    if system:
        payload_messages.append({"role": "system", "content": system})
    payload_messages.extend(messages)

    payload = {
        "model": model.model_id,
        "messages": payload_messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
        # Tell NadirClaw not to re-route this (bypass classifier)
        "x_nadirclaw_force_model": True,
    }

    client = _get_http_client()
    resp = await client.post(
        f"{nadirclaw_base_url}/chat/completions",
        json=payload,
        headers={"Content-Type": "application/json", "Authorization": "Bearer local"},
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


# ─── Task Decomposer ─────────────────────────────────────────────────────────


DECOMPOSE_SYSTEM = """You are a senior AI pipeline architect.
Your job: analyse an incoming user request and decompose it into the minimum
set of subtasks needed to produce the best possible answer.

For each subtask return a JSON object. Return ONLY a JSON array — no prose, no markdown fences.

Schema per subtask:
{
  "id": "t1",                          // short unique id
  "description": "...",               // what this subtask must produce (1-2 sentences)
  "task_type": "...",                 // one of: general|code|reasoning|math|architecture|
                                      //   security_analysis|security_review|testing|debugging|
                                      //   code_review|tech_lead_review|creative|summarization|
                                      //   planning|decomposition|synthesis|critique|classification|
                                      //   trivial|agentic_code
  "depends_on": [],                   // list of ids this task needs first ([] = can run in parallel)
  "mode": "parallel"                  // "parallel" or "sequential"
}

Rules:
- 2–6 subtasks maximum. Do not over-decompose.
- Coding tasks always get a dedicated "testing" subtask.
- Architecture or system design always gets a "security_analysis" subtask.
- The last subtask should always be "synthesis" — merging all outputs.
- Never include the final review chain steps (tech_lead_review, security_review) — those are added automatically.
"""


async def _decompose_prompt(
    prompt: str,
    decomposer_model: ModelRole,
    nadirclaw_base_url: str,
) -> List[Dict[str, Any]]:
    """Ask the decomposer model to break the prompt into subtasks."""
    raw = await _call_model(
        model=decomposer_model,
        messages=[{"role": "user", "content": prompt}],
        system=DECOMPOSE_SYSTEM,
        temperature=0.2,
        max_tokens=1024,
        nadirclaw_base_url=nadirclaw_base_url,
    )

    # Strip markdown fences if present
    clean = re.sub(r"```(?:json)?|```", "", raw).strip()
    try:
        subtasks_raw = json.loads(clean)
        if not isinstance(subtasks_raw, list):
            raise ValueError("Expected JSON array")
        return subtasks_raw
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("Decomposer output parse failed (%s). Using single-task fallback.", e)
        return [
            {
                "id": "t1",
                "description": prompt,
                "task_type": "general",
                "depends_on": [],
                "mode": "parallel",
            },
            {
                "id": "t2",
                "description": "Synthesise and format the final answer.",
                "task_type": "synthesis",
                "depends_on": ["t1"],
                "mode": "sequential",
            },
        ]


# ─── Plan Builder ─────────────────────────────────────────────────────────────


def _build_plan(
    original_prompt: str,
    subtasks_raw: List[Dict[str, Any]],
    complexity: float,
    privacy_required: bool,
    triggered_by: str,
) -> PipelinePlan:
    """
    Assign models to each subtask using the role registry,
    then append the fixed review chain.
    """
    subtasks: List[SubTask] = []
    assigned_ids: List[str] = []

    for raw in subtasks_raw:
        task_type = raw.get("task_type", "general")
        model = best_model_for(
            task_type=task_type,
            complexity=complexity,
            privacy_required=privacy_required,
            exclude=assigned_ids,   # light diversity — prefer different models per phase
        )
        # Allow re-use for synthesis/review
        if task_type not in ("synthesis", "critique"):
            assigned_ids.append(model.model_id)

        subtasks.append(SubTask(
            id=raw.get("id", f"t{len(subtasks)+1}"),
            description=raw.get("description", ""),
            task_type=task_type,
            assigned_model=model,
            depends_on=raw.get("depends_on", []),
            mode=PhaseMode(raw.get("mode", "parallel")),
        ))

    # Fixed review chain (always appended after synthesis)
    review_chain = [
        "claude-opus-4-6",      # Sr. Tech Lead
        "ollama/deepseek-r1:8b",  # Security / Logic auditor
        "openai-codex/gpt-5-codex",    # QA / Test writer
    ]
    if privacy_required:
        # Keep review fully local if privacy flag is set
        review_chain = ["ollama/deepseek-r1:8b", "ollama/qwen3-coder:30b"]

    return PipelinePlan(
        original_prompt=original_prompt,
        subtasks=subtasks,
        review_chain=review_chain,
        privacy_required=privacy_required,
        triggered_by=triggered_by,
    )


# ─── Executor ─────────────────────────────────────────────────────────────────


async def _execute_subtask(
    task: SubTask,
    context: Dict[str, str],   # id → result of completed tasks
    original_prompt: str,
    nadirclaw_base_url: str,
) -> SubTask:
    """Run a single subtask, injecting context from dependencies."""
    dependency_context = ""
    for dep_id in task.depends_on:
        if dep_id in context:
            dependency_context += f"\n\n--- Output from step {dep_id} ---\n{context[dep_id]}"

    system = f"""You are a specialist completing ONE subtask as part of a larger pipeline.
Your subtask: {task.description}
Task type: {task.task_type}

Original user request (for context only):
{original_prompt}
{dependency_context}

Produce ONLY the output for your subtask. Be thorough and precise."""

    t0 = time.time()
    try:
        result = await _call_model(
            model=task.assigned_model,
            messages=[{"role": "user", "content": task.description}],
            system=system,
            nadirclaw_base_url=nadirclaw_base_url,
        )
        task.result = result
    except Exception as exc:
        logger.error("Subtask %s failed: %s", task.id, exc)
        task.error = str(exc)
        task.result = f"[Error in {task.id}: {exc}]"
    task.latency_s = time.time() - t0
    return task


async def _execute_plan(
    plan: PipelinePlan,
    nadirclaw_base_url: str,
) -> List[SubTask]:
    """
    Execute all subtasks respecting dependency order.
    Independent subtasks run in parallel.
    """
    completed: Dict[str, str] = {}   # id → result
    remaining = list(plan.subtasks)
    finished: List[SubTask] = []

    while remaining:
        # Find tasks whose dependencies are all satisfied
        ready = [
            t for t in remaining
            if all(dep in completed for dep in t.depends_on)
        ]
        if not ready:
            logger.error("Dependency deadlock — forcing remaining tasks sequential.")
            ready = [remaining[0]]

        # Run ready tasks concurrently
        results = await asyncio.gather(*[
            _execute_subtask(t, completed, plan.original_prompt, nadirclaw_base_url)
            for t in ready
        ])

        for task in results:
            completed[task.id] = task.result or ""
            finished.append(task)
            remaining.remove(task)

    return finished


# ─── Weighted Merger ──────────────────────────────────────────────────────────


async def _weighted_merge(
    subtask_results: List[SubTask],
    plan: PipelinePlan,
    nadirclaw_base_url: str,
) -> str:
    """
    Merge parallel outputs weighted by each model's specialty confidence,
    using the synthesis subtask result as the base if available.
    """
    # Find synthesis output if it exists
    synthesis_task = next(
        (t for t in subtask_results if t.task_type == "synthesis"), None
    )
    if synthesis_task and synthesis_task.result:
        return synthesis_task.result

    # Otherwise merge all non-error results via a merge call
    combined = "\n\n".join(
        f"--- {t.task_type.upper()} (model: {t.assigned_model.display_name}, "
        f"confidence: {t.assigned_model.specialties.get(t.task_type, 0.5):.2f}) ---\n{t.result}"
        for t in subtask_results
        if not t.error
    )

    merge_model = ROLE_REGISTRY["gemini/gemini-2.0-flash"]
    merged = await _call_model(
        model=merge_model,
        messages=[{"role": "user", "content": combined}],
        system=f"""You are a synthesis engine. Below are outputs from multiple specialist models,
each labelled with their domain and confidence score. Produce a single, coherent, high-quality
response to the original request. Weight more heavily the outputs with higher confidence scores.
Preserve all code, tests, and security notes.

Original request: {plan.original_prompt}""",
        temperature=0.2,
        nadirclaw_base_url=nadirclaw_base_url,
    )
    return merged


# ─── Review Chain ─────────────────────────────────────────────────────────────


REVIEW_ROLES = {
    "claude-opus-4-6": {
        "role": "Sr. Tech Lead",
        "system": """You are a senior technical lead reviewing a draft response.
Check for: architectural soundness, completeness, clarity, best practices.
Add any missing context. Flag any concerns. Then produce an improved final draft.""",
    },
    "ollama/deepseek-r1:8b": {
        "role": "Security & Logic Auditor",
        "system": """You are a security engineer and logic auditor.
Review the draft for: security vulnerabilities, edge cases, logical flaws,
missing error handling, and privacy concerns. Annotate issues inline and
produce a hardened version of the response.""",
    },
    "openai-codex/gpt-5-codex": {
        "role": "QA Engineer",
        "system": """You are a QA engineer. Review the response and:
1. Add or improve tests for any code present.
2. Verify edge cases are handled.
3. Add usage examples if missing.
4. Produce the final complete response with tests included.""",
    },
    "ollama/qwen3-coder:30b": {
        "role": "Code Reviewer",
        "system": """You are a senior code reviewer.
Review all code in the response. Improve style, add docstrings,
check for bugs. Produce the final polished code response.""",
    },
}


async def _run_review_chain(
    draft: str,
    plan: PipelinePlan,
    nadirclaw_base_url: str,
) -> Tuple[str, List[Dict[str, str]]]:
    """
    Pass the merged draft through each reviewer sequentially.
    Each reviewer improves on the previous output.
    """
    current = draft
    review_log: List[Dict[str, str]] = []

    for model_id in plan.review_chain:
        model = ROLE_REGISTRY.get(model_id)
        if not model:
            logger.warning("Review chain model %s not in registry, skipping.", model_id)
            continue

        role_info = REVIEW_ROLES.get(model_id, {
            "role": "Reviewer",
            "system": "Review and improve this response. Produce the improved version.",
        })

        try:
            reviewed = await _call_model(
                model=model,
                messages=[{"role": "user", "content": current}],
                system=f"{role_info['system']}\n\nOriginal request: {plan.original_prompt}",
                temperature=0.2,
                nadirclaw_base_url=nadirclaw_base_url,
            )
            review_log.append({"reviewer": role_info["role"], "model": model_id, "output": reviewed})
            current = reviewed
        except Exception as exc:
            logger.error("Review step %s failed: %s — continuing with previous draft.", model_id, exc)
            review_log.append({"reviewer": role_info["role"], "model": model_id, "error": str(exc)})

    return current, review_log


# ─── Main Orchestrator ────────────────────────────────────────────────────────


class PipelineOrchestrator:
    """
    Main entry point. Wire this into NadirClaw's server.py before
    the standard single-model dispatch.

    Usage in server.py:
        orchestrator = PipelineOrchestrator(base_url="http://localhost:8856/v1")

        # In your request handler:
        if orchestrator.should_pipeline(prompt, complexity_score):
            result = await orchestrator.run(prompt, complexity_score, privacy=False)
            return result.final_response
        else:
            # existing single-model routing
    """

    def __init__(self, base_url: str = "http://localhost:8856/v1"):
        self.base_url = base_url
        # Decomposer: use gemini-flash (fast, good at decomposition)
        self.decomposer = ROLE_REGISTRY["gemini/gemini-2.0-flash"]

    def should_pipeline(self, prompt: str, complexity: float) -> bool:
        """Return True if this request should use the multi-model pipeline."""
        # Manual override via prefix
        if any(prompt.strip().startswith(prefix) for prefix in MANUAL_PIPELINE_PREFIXES):
            return True
        # Auto-trigger on high complexity
        return complexity >= AUTO_PIPELINE_COMPLEXITY_THRESHOLD

    def strip_pipeline_prefix(self, prompt: str) -> Tuple[str, str]:
        """Remove @pipeline/@team prefix and return (clean_prompt, trigger_type)."""
        for prefix in MANUAL_PIPELINE_PREFIXES:
            if prompt.strip().startswith(prefix):
                return prompt.strip()[len(prefix):].strip(), "manual"
        return prompt, "auto"

    async def run(
        self,
        prompt: str,
        complexity: float = 0.9,
        privacy_required: bool = False,
    ) -> PipelineResult:
        t_start = time.time()
        clean_prompt, triggered_by = self.strip_pipeline_prefix(prompt)

        logger.info(
            "🔀 Pipeline triggered (%s) | complexity=%.2f | privacy=%s",
            triggered_by, complexity, privacy_required,
        )

        # 1. Decompose
        logger.info("📋 Decomposing prompt...")
        subtasks_raw = await _decompose_prompt(clean_prompt, self.decomposer, self.base_url)
        logger.info("  → %d subtasks planned", len(subtasks_raw))

        # 2. Build plan (assign models)
        plan = _build_plan(clean_prompt, subtasks_raw, complexity, privacy_required, triggered_by)
        for st in plan.subtasks:
            logger.info(
                "  [%s] %s → %s (%s)",
                st.id, st.task_type, st.assigned_model.display_name, st.mode.value,
            )

        # 3. Execute subtasks
        logger.info("⚡ Executing subtasks...")
        subtask_results = await _execute_plan(plan, self.base_url)

        # 4. Weighted merge
        logger.info("🔀 Merging outputs...")
        draft = await _weighted_merge(subtask_results, plan, self.base_url)

        # 5. Review chain
        logger.info("🔍 Running review chain: %s", plan.review_chain)
        final_response, review_log = await _run_review_chain(draft, plan, self.base_url)

        total_latency = time.time() - t_start
        models_used = list({st.assigned_model.model_id for st in subtask_results})
        models_used += plan.review_chain

        logger.info("✅ Pipeline complete in %.1fs | models used: %s", total_latency, models_used)

        return PipelineResult(
            final_response=final_response,
            plan=plan,
            subtask_results=subtask_results,
            review_outputs=review_log,
            total_latency_s=total_latency,
            models_used=models_used,
        )
