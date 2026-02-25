"""Multi-model pipeline engine for NadirClaw.

Defines pipeline configurations (intent x role -> model) and a sequential
executor that runs Builder -> Judge -> Compressor for each request.

The compressor step is fire-and-forget: its output goes to SurrealDB memory,
not to the user.  If the judge or compressor fails, the builder output is
returned with status "partial".
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from nadirclaw.events import event_bus
from nadirclaw.settings import settings

logger = logging.getLogger("nadirclaw.pipeline")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class StepResult:
    """Result from a single pipeline step."""
    role: str  # "builder", "judge", "compressor"
    model: str
    content: str
    status: str  # "ok", "error", "skipped"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    error: Optional[str] = None


@dataclass
class PipelineResult:
    """Full result of a pipeline execution."""
    pipeline_id: str
    intent: str
    status: str  # "ok", "partial", "error"
    steps: List[StepResult] = field(default_factory=list)
    final_content: str = ""
    total_latency_ms: int = 0
    user_prompt: str = ""
    blast_applied: bool = False
    blast_latency_ms: int = 0
    execution_plan: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Pipeline configurations — intent x role -> model
# ---------------------------------------------------------------------------

def _get_pipeline_configs() -> Dict[str, Dict[str, Optional[str]]]:
    """Return the model selection matrix (intent x role -> model).

    Cloud models for architecture/strategy tasks, local models for code tasks.
    None means the step is skipped for that intent.
    """
    return {
        "code_generation": {
            "builder": settings.PIPELINE_BUILDER,     # ollama/qwen3-coder:30b
            "judge": settings.PIPELINE_JUDGE,          # ollama/deepseek-r1:8b
            "compressor": settings.PIPELINE_COMPRESSOR,  # ollama/qwen2.5:3b
        },
        "code_review": {
            "builder": settings.PIPELINE_JUDGE,        # reasoning model reviews
            "judge": settings.PIPELINE_BUILDER,         # code model checks
            "compressor": settings.PIPELINE_COMPRESSOR,
        },
        "architecture": {
            "builder": _cloud_or_fallback("anthropic"),
            "judge": _cloud_or_fallback("google"),
            "compressor": settings.PIPELINE_COMPRESSOR,
        },
        "debugging": {
            "builder": settings.PIPELINE_JUDGE,
            "judge": settings.PIPELINE_BUILDER,
            "compressor": None,  # no compression for debugging
        },
        "security_analysis": {
            "builder": settings.PIPELINE_JUDGE,
            "judge": settings.PIPELINE_BUILDER,
            "compressor": settings.PIPELINE_COMPRESSOR,
        },
        "documentation": {
            "builder": settings.SIMPLE_MODEL if "qwen3:8b" in settings.SIMPLE_MODEL else settings.PIPELINE_BUILDER,
            "judge": settings.PIPELINE_JUDGE,
            "compressor": None,
        },
        "general_qa": {
            "builder": settings.SIMPLE_MODEL if "qwen3:8b" in settings.SIMPLE_MODEL else settings.PIPELINE_BUILDER,
            "judge": settings.PIPELINE_JUDGE,
            "compressor": None,
        },
    }


def _cloud_or_fallback(provider: str) -> str:
    """Return a cloud model for a provider, or fall back to local."""
    from nadirclaw.credentials import get_credential

    if provider == "anthropic":
        if get_credential("anthropic"):
            return "claude-sonnet-4-5-20250929"
        return settings.PIPELINE_BUILDER
    elif provider == "google":
        if get_credential("gemini") or get_credential("google"):
            return "gemini-2.5-flash"
        return settings.PIPELINE_JUDGE
    return settings.PIPELINE_BUILDER


# ---------------------------------------------------------------------------
# Prompt templates for each pipeline role
# ---------------------------------------------------------------------------

# Legacy templates kept for backward compatibility and fallback
BUILDER_TEMPLATE = """\
You are an expert assistant. Provide a thorough, high-quality response.

User request:
{user_prompt}"""

JUDGE_TEMPLATE = """\
You are an expert reviewer. Evaluate the following response for correctness, \
completeness, and quality. Point out any errors, missing considerations, or \
improvements. Be specific and constructive.

Original user request:
{user_prompt}

Response to review:
{builder_output}

Provide your review with specific feedback and suggestions."""

COMPRESSOR_TEMPLATE = """\
Extract the key decisions, patterns, and insights from this exchange. \
Output a concise summary suitable for long-term memory storage.

Do NOT include: API keys, tokens, passwords, PII, email addresses, or raw code files.
Only include: decisions made, patterns identified, trade-offs discussed, and key takeaways.

User request:
{user_prompt}

Response:
{builder_output}

Review:
{judge_output}

Summarize the key decisions and patterns (max 300 words):"""


# ---------------------------------------------------------------------------
# Intent-aware prompt builder — injects execution plan into each agent
# ---------------------------------------------------------------------------

def _build_plan_briefing(plan: Optional[Dict], current_role: str) -> str:
    """Build a pipeline briefing block from the execution plan.

    Tells the model: who it is, what other agents are in the chain,
    and what the BLAST analysis found.  Supports both sequential and
    phase-based (concurrent) plans.
    """
    if not plan:
        return ""

    steps = plan.get("steps", [])
    if not steps:
        return ""

    is_concurrent = plan.get("concurrent", False)

    # Find the current agent's action
    my_action = ""
    chain_lines = []

    if is_concurrent:
        # Phase-aware display
        for phase_info in plan.get("phases", []):
            phase_num = phase_info.get("phase", "?")
            phase_name = phase_info.get("name", "?")
            parallel = phase_info.get("parallel", False)
            parallel_tag = " [PARALLEL]" if parallel else ""
            chain_lines.append(f"  Phase {phase_num}: {phase_name}{parallel_tag}")
            for step in phase_info.get("steps", []):
                agent = step.get("agent", "")
                model = step.get("model_short", step.get("model", "?"))
                action = step.get("action", "")
                marker = " ← YOU" if agent == current_role else ""
                chain_lines.append(f"    - {agent} ({model}): {action}{marker}")
                if agent == current_role:
                    my_action = action
    else:
        # Sequential display
        for step in steps:
            agent = step.get("agent", "")
            model = step.get("model_short", step.get("model", "?"))
            action = step.get("action", "")
            marker = " ← YOU" if agent == current_role else ""
            chain_lines.append(f"  {step.get('step', '?')}. {agent} ({model}): {action}{marker}")
            if agent == current_role:
                my_action = action

    summary = plan.get("summary", "")
    total_phases = plan.get("total_phases", len(plan.get("phases", [])))
    briefing = (
        f"--- PIPELINE BRIEFING ---\n"
        f"Intent: {plan.get('intent', '?')}\n"
        f"Task summary: {summary}\n"
        f"Mode: {'Concurrent ({} phases)'.format(total_phases) if is_concurrent else 'Sequential'}\n"
        f"\n"
        f"Execution chain ({len(steps)} agents):\n"
        + "\n".join(chain_lines)
        + "\n\n"
        f"Your role: {current_role}\n"
        f"Your assignment: {my_action}\n"
        f"--- END BRIEFING ---\n"
    )
    return briefing


def _build_builder_prompt(
    user_prompt: str,
    intent: str,
    plan: Optional[Dict] = None,
) -> str:
    """Build the full builder prompt with execution plan context."""
    from nadirclaw.blast import _ROLE_DESCRIPTIONS

    role_descs = _ROLE_DESCRIPTIONS.get(intent, _ROLE_DESCRIPTIONS.get("general_qa", {}))
    action = role_descs.get("builder", "Provide a thorough, high-quality response")

    briefing = _build_plan_briefing(plan, "builder")

    return (
        f"{briefing}"
        f"You are an expert assistant. Your specific assignment: {action}.\n"
        f"Plan first. Verify before marking done. Demand elegance and simplicity.\n\n"
        f"User request:\n{user_prompt}"
    )


def _build_judge_prompt(
    user_prompt: str,
    builder_output: str,
    intent: str,
    plan: Optional[Dict] = None,
) -> str:
    """Build the full judge prompt with execution plan context."""
    from nadirclaw.blast import _ROLE_DESCRIPTIONS

    role_descs = _ROLE_DESCRIPTIONS.get(intent, _ROLE_DESCRIPTIONS.get("general_qa", {}))
    action = role_descs.get("judge", "Evaluate for correctness, completeness, and quality")

    briefing = _build_plan_briefing(plan, "judge")

    return (
        f"{briefing}"
        f"You are an expert reviewer. Your specific assignment: {action}.\n"
        f"Be specific and constructive. Flag anything the builder missed.\n\n"
        f"Original user request:\n{user_prompt}\n\n"
        f"Response to review:\n{builder_output}\n\n"
        f"Provide your review with specific feedback and suggestions."
    )


def _build_compressor_prompt(
    user_prompt: str,
    builder_output: str,
    judge_output: str,
    intent: str,
    plan: Optional[Dict] = None,
) -> str:
    """Build the full compressor prompt with execution plan context."""
    from nadirclaw.blast import _ROLE_DESCRIPTIONS

    role_descs = _ROLE_DESCRIPTIONS.get(intent, _ROLE_DESCRIPTIONS.get("general_qa", {}))
    action = role_descs.get("compressor", "Extract key decisions for long-term memory")

    briefing = _build_plan_briefing(plan, "compressor")

    return (
        f"{briefing}"
        f"Your specific assignment: {action}.\n\n"
        f"Extract the key decisions, patterns, and insights from this exchange. "
        f"Output a concise summary suitable for long-term memory storage.\n\n"
        f"Do NOT include: API keys, tokens, passwords, PII, email addresses, or raw code files.\n"
        f"Only include: decisions made, patterns identified, trade-offs discussed, and key takeaways.\n\n"
        f"User request:\n{user_prompt}\n\n"
        f"Response:\n{builder_output[:4000]}\n\n"
        f"Review:\n{judge_output[:2000]}\n\n"
        f"Summarize the key decisions and patterns (max 300 words):"
    )


def _build_lane_prompt(
    user_prompt: str,
    intent: str,
    lane_role: str,
    lane_action: str,
    plan: Optional[Dict] = None,
) -> str:
    """Build a prompt for a concurrent builder lane.

    Each lane gets the full user request plus its specific focus area.
    """
    briefing = _build_plan_briefing(plan, lane_role)

    return (
        f"{briefing}"
        f"You are an expert assistant working on one part of a parallel pipeline.\n"
        f"Your specific assignment: {lane_action}.\n"
        f"Focus ONLY on your assignment — another agent handles the other aspects.\n"
        f"Plan first. Verify before marking done. Demand elegance and simplicity.\n\n"
        f"User request:\n{user_prompt}"
    )


def _build_synthesizer_prompt(
    user_prompt: str,
    lane_outputs: Dict[str, str],
    intent: str,
    synth_action: str,
    plan: Optional[Dict] = None,
) -> str:
    """Build a prompt for the synthesizer that merges parallel lane outputs.

    The synthesizer sees all lane outputs and must combine them coherently.
    """
    briefing = _build_plan_briefing(plan, "synthesizer")

    output_sections = []
    for lane_name, output in lane_outputs.items():
        output_sections.append(f"--- Output from {lane_name} ---\n{output}")

    outputs_block = "\n\n".join(output_sections)

    return (
        f"{briefing}"
        f"You are a synthesis expert. Your assignment: {synth_action}.\n"
        f"You are receiving outputs from parallel agents that worked independently.\n"
        f"Merge their work into a single, coherent, high-quality response.\n"
        f"Resolve any conflicts or redundancies. Maintain the best parts of each.\n\n"
        f"Original user request:\n{user_prompt}\n\n"
        f"Parallel outputs to merge:\n\n{outputs_block}\n\n"
        f"Provide the unified, synthesized response:"
    )


# ---------------------------------------------------------------------------
# Pipeline executor
# ---------------------------------------------------------------------------

async def execute_pipeline(
    intent: str,
    messages: List[Dict[str, str]],
    pipeline_id: Optional[str] = None,
    model_override: Optional[str] = None,
) -> PipelineResult:
    """Execute the multi-model pipeline for a classified intent.

    Steps:
    1. BUILDER — generates content
    2. JUDGE — reviews/critiques
    3. COMPRESSOR — extracts decisions for memory (async, non-blocking)

    Returns PipelineResult with builder+judge output.
    """
    from nadirclaw.dispatch import dispatch_raw, RateLimitExhausted

    if pipeline_id is None:
        pipeline_id = str(uuid.uuid4())

    start_time = time.time()

    # Extract user prompt from messages
    user_prompt = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            user_prompt = m.get("content", "")
            break

    result = PipelineResult(
        pipeline_id=pipeline_id,
        intent=intent,
        status="ok",
        user_prompt=user_prompt,
    )

    # Get pipeline config for this intent
    configs = _get_pipeline_configs()
    config = configs.get(intent)
    if not config:
        # Unknown intent — use code_generation defaults
        config = configs["code_generation"]

    # Apply model override — user wants a specific model for the builder
    if model_override and model_override not in ("auto", "pipeline", "nadirclaw/pipeline"):
        config = dict(config)  # don't mutate the shared config
        config["builder"] = model_override
        logger.info("Pipeline builder overridden to: %s", model_override)

    # ── BLAST optimization ───────────────────────────────────────────
    blast_result = None
    if settings.BLAST_ENABLED:
        skip = settings.BLAST_SKIP_SIMPLE and intent == "simple_qa"
        if not skip:
            try:
                from nadirclaw.blast import get_blast_optimizer
                blast = get_blast_optimizer()
                blast_result = await blast.optimize(user_prompt, intent)
                user_prompt = blast_result.enhanced_prompt
                logger.info(
                    "BLAST applied (intent=%s, llm=%s, %dms)",
                    intent, blast_result.used_llm, blast_result.latency_ms,
                )
            except Exception as e:
                logger.warning("BLAST optimization failed (non-fatal): %s", e)

    if blast_result:
        result.blast_applied = True
        result.blast_latency_ms = blast_result.latency_ms

    # ── Build execution plan ─────────────────────────────────────────
    from nadirclaw.blast import build_execution_plan
    plan = build_execution_plan(
        intent=intent,
        sections=blast_result.sections if blast_result else {},
        pipeline_config=config,
        blast_model=settings.BLAST_MODEL,
        used_llm=blast_result.used_llm if blast_result else False,
    )
    result.execution_plan = plan
    if blast_result:
        blast_result.execution_plan = plan

    logger.info(
        "Execution plan: %d agents, %d phases — %s",
        plan["total_agents"],
        plan.get("total_phases", 0),
        " → ".join(s["agent"] for s in plan["steps"]),
    )

    # ── Create pipeline tracker ─────────────────────────────────────
    from nadirclaw.pipeline_tracker import (
        PipelineTracker, register_tracker, unregister_tracker,
    )
    tracker = PipelineTracker(pipeline_id, intent, plan, user_prompt)
    register_tracker(tracker)
    await tracker.start()

    # ── Check for concurrent execution ──────────────────────────────
    from nadirclaw.blast import get_concurrent_phases
    concurrent = get_concurrent_phases(intent)

    if concurrent:
        try:
            return await _execute_concurrent_pipeline(
                intent=intent,
                user_prompt=user_prompt,
                config=config,
                plan=plan,
                concurrent=concurrent,
                result=result,
                pipeline_id=pipeline_id,
                start_time=start_time,
                tracker=tracker,
            )
        finally:
            unregister_tracker(pipeline_id)

    # ── Sequential pipeline (default) ───────────────────────────────

    try:
        # Step 1: BUILDER
        builder_model = config["builder"]
        builder_prompt = _build_builder_prompt(user_prompt, intent, plan)
        builder_step = await _execute_step(
            role="builder",
            model=builder_model,
            messages=[{"role": "user", "content": builder_prompt}],
            pipeline_id=pipeline_id,
            tracker=tracker,
        )
        result.steps.append(builder_step)

        if builder_step.status == "error":
            result.status = "error"
            result.final_content = builder_step.error or "Builder step failed."
            result.total_latency_ms = int((time.time() - start_time) * 1000)
            await tracker.finish(result.status, error=builder_step.error)
            return result

        builder_output = builder_step.content

        # Step 2: JUDGE
        judge_model = config["judge"]
        judge_prompt = _build_judge_prompt(user_prompt, builder_output, intent, plan)
        judge_step = await _execute_step(
            role="judge",
            model=judge_model,
            messages=[{"role": "user", "content": judge_prompt}],
            pipeline_id=pipeline_id,
            tracker=tracker,
        )
        result.steps.append(judge_step)

        if judge_step.status == "error":
            # Judge failed — return builder output with partial status
            result.status = "partial"
            result.final_content = builder_output
        else:
            # Combine builder + judge output
            result.final_content = (
                f"{builder_output}\n\n"
                f"---\n\n"
                f"**Review notes:**\n{judge_step.content}"
            )

        result.total_latency_ms = int((time.time() - start_time) * 1000)

        # Step 3: COMPRESSOR (fire-and-forget)
        compressor_model = config.get("compressor")
        if compressor_model:
            judge_output = judge_step.content if judge_step.status == "ok" else ""
            asyncio.create_task(_run_compressor(
                model=compressor_model,
                user_prompt=user_prompt,
                builder_output=builder_output,
                judge_output=judge_output,
                pipeline_id=pipeline_id,
                intent=intent,
                plan=plan,
            ))

        await tracker.finish(result.status)
        return result
    finally:
        unregister_tracker(pipeline_id)


async def _execute_concurrent_pipeline(
    intent: str,
    user_prompt: str,
    config: Dict[str, Optional[str]],
    plan: Dict[str, Any],
    concurrent: Any,  # ConcurrentPhases
    result: PipelineResult,
    pipeline_id: str,
    start_time: float,
    tracker=None,
) -> PipelineResult:
    """Execute the pipeline with concurrent builder lanes.

    Phase 1: Run parallel lanes (e.g. impl + tests) simultaneously
    Phase 2: Synthesize parallel outputs into a unified response
    Phase 3: Judge reviews the synthesized output
    Phase 4: Compressor extracts decisions for memory (fire-and-forget)
    """
    logger.info("Executing concurrent pipeline (%d lanes) for %s",
                len(concurrent.lanes), intent)

    # ── Phase 1: Parallel lanes ─────────────────────────────────────
    lane_tasks = []
    lane_names = []
    for lane in concurrent.lanes:
        lane_role = f"builder:{lane.role_suffix}"
        lane_model = config.get(lane.config_key, config.get("builder", ""))
        lane_prompt = _build_lane_prompt(
            user_prompt=user_prompt,
            intent=intent,
            lane_role=lane_role,
            lane_action=lane.action,
            plan=plan,
        )
        lane_names.append(lane_role)
        lane_tasks.append(_execute_step(
            role=lane_role,
            model=lane_model,
            messages=[{"role": "user", "content": lane_prompt}],
            pipeline_id=pipeline_id,
            tracker=tracker,
        ))

    # Run all lanes concurrently
    lane_results = await asyncio.gather(*lane_tasks, return_exceptions=True)

    # Collect lane outputs
    lane_outputs: Dict[str, str] = {}
    all_lanes_ok = True
    for lane_name, lane_result in zip(lane_names, lane_results):
        if isinstance(lane_result, Exception):
            logger.error("Concurrent lane %s raised exception: %s", lane_name, lane_result)
            error_step = StepResult(
                role=lane_name,
                model="unknown",
                content="",
                status="error",
                error=str(lane_result),
            )
            result.steps.append(error_step)
            all_lanes_ok = False
        else:
            result.steps.append(lane_result)
            if lane_result.status == "ok":
                lane_outputs[lane_name] = lane_result.content
            else:
                all_lanes_ok = False

    if not lane_outputs:
        # All lanes failed
        result.status = "error"
        result.final_content = "All concurrent builder lanes failed."
        result.total_latency_ms = int((time.time() - start_time) * 1000)
        if tracker:
            await tracker.finish(result.status, error="All concurrent builder lanes failed")
        return result

    if not all_lanes_ok:
        logger.warning("Some concurrent lanes failed, synthesizing with partial outputs")

    # ── Phase 2: Synthesizer ────────────────────────────────────────
    synth_model = config.get("builder", "")
    synth_prompt = _build_synthesizer_prompt(
        user_prompt=user_prompt,
        lane_outputs=lane_outputs,
        intent=intent,
        synth_action=concurrent.synth_action,
        plan=plan,
    )
    synth_step = await _execute_step(
        role="synthesizer",
        model=synth_model,
        messages=[{"role": "user", "content": synth_prompt}],
        pipeline_id=pipeline_id,
        tracker=tracker,
    )
    result.steps.append(synth_step)

    synth_ok = True
    if synth_step.status == "error":
        # Synthesizer failed — concatenate lane outputs as fallback
        logger.warning("Synthesizer failed, falling back to concatenated lane outputs")
        synth_ok = False
        parts = [f"## {name}\n{output}" for name, output in lane_outputs.items()]
        synthesized_output = "\n\n---\n\n".join(parts)
    else:
        synthesized_output = synth_step.content

    # ── Phase 3: Judge ──────────────────────────────────────────────
    judge_ok = True
    judge_model = config.get("judge")
    if judge_model:
        judge_prompt = _build_judge_prompt(user_prompt, synthesized_output, intent, plan)
        judge_step = await _execute_step(
            role="judge",
            model=judge_model,
            messages=[{"role": "user", "content": judge_prompt}],
            pipeline_id=pipeline_id,
            tracker=tracker,
        )
        result.steps.append(judge_step)

        if judge_step.status == "error":
            judge_ok = False
            result.final_content = synthesized_output
        else:
            result.final_content = (
                f"{synthesized_output}\n\n"
                f"---\n\n"
                f"**Review notes:**\n{judge_step.content}"
            )
    else:
        result.final_content = synthesized_output

    # Determine final status: "ok" only if all stages succeeded
    if all_lanes_ok and synth_ok and judge_ok:
        result.status = "ok"
    else:
        result.status = "partial"

    result.total_latency_ms = int((time.time() - start_time) * 1000)

    # ── Phase 4: Compressor (fire-and-forget) ───────────────────────
    compressor_model = config.get("compressor")
    if compressor_model:
        judge_output = ""
        if judge_model:
            for step in result.steps:
                if step.role == "judge" and step.status == "ok":
                    judge_output = step.content
        asyncio.create_task(_run_compressor(
            model=compressor_model,
            user_prompt=user_prompt,
            builder_output=synthesized_output,
            judge_output=judge_output,
            pipeline_id=pipeline_id,
            intent=intent,
            plan=plan,
        ))

    if tracker:
        await tracker.finish(result.status)

    return result


async def _execute_step(
    role: str,
    model: str,
    messages: List[Dict[str, str]],
    pipeline_id: str,
    tracker=None,
) -> StepResult:
    """Execute a single pipeline step with event publishing."""
    from nadirclaw.dispatch import dispatch_raw, RateLimitExhausted

    # Publish step start event
    await event_bus.publish({
        "event_type": "pipeline_step_start",
        "pipeline_id": pipeline_id,
        "role": role,
        "model": model,
    })

    if tracker:
        tracker.step_started(role, model)

    start = time.time()

    try:
        response = await dispatch_raw(model, messages)
        latency_ms = int((time.time() - start) * 1000)

        step = StepResult(
            role=role,
            model=model,
            content=response["content"],
            status="ok",
            prompt_tokens=response.get("prompt_tokens", 0),
            completion_tokens=response.get("completion_tokens", 0),
            latency_ms=latency_ms,
        )

    except RateLimitExhausted as e:
        latency_ms = int((time.time() - start) * 1000)
        logger.warning("Pipeline step %s rate limited: %s", role, e)
        step = StepResult(
            role=role,
            model=model,
            content="",
            status="error",
            latency_ms=latency_ms,
            error=f"Rate limit: {e}",
        )

    except Exception as e:
        latency_ms = int((time.time() - start) * 1000)
        logger.error("Pipeline step %s failed: %s", role, e, exc_info=True)
        step = StepResult(
            role=role,
            model=model,
            content="",
            status="error",
            latency_ms=latency_ms,
            error=str(e),
        )

    # Publish step complete event
    await event_bus.publish({
        "event_type": "pipeline_step_complete",
        "pipeline_id": pipeline_id,
        "role": role,
        "model": model,
        "status": step.status,
        "latency_ms": step.latency_ms,
        "prompt_tokens": step.prompt_tokens,
        "completion_tokens": step.completion_tokens,
    })

    if tracker:
        tracker.step_completed(role, status=step.status, latency_ms=step.latency_ms, error=step.error)

    return step


async def _run_compressor(
    model: str,
    user_prompt: str,
    builder_output: str,
    judge_output: str,
    pipeline_id: str,
    intent: str,
    plan: Optional[Dict] = None,
) -> None:
    """Run the compressor step in the background and store result in SurrealDB."""
    from nadirclaw.dispatch import dispatch_raw

    try:
        prompt = _build_compressor_prompt(
            user_prompt=user_prompt,
            builder_output=builder_output,
            judge_output=judge_output,
            intent=intent,
            plan=plan,
        )

        response = await dispatch_raw(model, [{"role": "user", "content": prompt}])
        summary = response["content"]

        # Store in SurrealDB decision table
        try:
            from nadirclaw.pipeline_db import insert_decision
            await insert_decision(
                summary=summary,
                intent=intent,
                pipeline_id=pipeline_id,
                user_prompt_preview=user_prompt[:200],
            )
        except Exception as e:
            logger.debug("Failed to store compressor output in DB: %s", e)

    except Exception as e:
        logger.debug("Compressor step failed (non-fatal): %s", e)


# ---------------------------------------------------------------------------
# Pipeline trace cache (in-memory, for /v1/pipeline/latest)
# ---------------------------------------------------------------------------

_latest_results: List[PipelineResult] = []
_MAX_CACHED = 100


def cache_pipeline_result(result: PipelineResult) -> None:
    """Cache a pipeline result for the /v1/pipeline/latest endpoint."""
    _latest_results.append(result)
    if len(_latest_results) > _MAX_CACHED:
        _latest_results.pop(0)


def get_latest_pipeline() -> Optional[PipelineResult]:
    """Get the most recent pipeline result."""
    return _latest_results[-1] if _latest_results else None


def get_pipeline_by_id(pipeline_id: str) -> Optional[PipelineResult]:
    """Get a cached pipeline result by ID."""
    for r in reversed(_latest_results):
        if r.pipeline_id == pipeline_id:
            return r
    return None


def pipeline_result_to_dict(result: PipelineResult) -> Dict[str, Any]:
    """Serialize a PipelineResult to a JSON-friendly dict."""
    return {
        "pipeline_id": result.pipeline_id,
        "intent": result.intent,
        "status": result.status,
        "total_latency_ms": result.total_latency_ms,
        "blast_applied": result.blast_applied,
        "blast_latency_ms": result.blast_latency_ms,
        "execution_plan": result.execution_plan,
        "steps": [
            {
                "role": s.role,
                "model": s.model,
                "status": s.status,
                "latency_ms": s.latency_ms,
                "prompt_tokens": s.prompt_tokens,
                "completion_tokens": s.completion_tokens,
                "error": s.error,
            }
            for s in result.steps
        ],
    }
