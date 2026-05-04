"""
NadirClaw — Lightweight LLM router server.

Routes simple prompts to cheap/local models and complex prompts to premium models.
OpenAI-compatible API at /v1/chat/completions.
"""

import asyncio
import collections
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional, Union

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from nadirclaw import __version__
from nadirclaw.auth import UserSession, validate_local_auth
from nadirclaw.events import event_bus
from nadirclaw.settings import settings

# ── Pipeline V2 Extension ────────────────────────────────────────────────────
from nadirclaw.orchestrator import PipelineOrchestrator
from nadirclaw.classifier_v2 import classify_v2

# ── Parallel Dispatch Extension ──────────────────────────────────────────────
from nadirclaw.parallel_dispatch import (
    should_parallel_dispatch, parallel_dispatch, get_model_pair, format_parallel_response,
)

_pipeline_orchestrator = PipelineOrchestrator(
    base_url=f"http://localhost:{settings.PORT}/v1"
)
# ──────────────────────────────────────────────────────────────────────────────

logger = logging.getLogger("nadirclaw")


# ---------------------------------------------------------------------------
# Request rate limiter (in-memory, per user)
# ---------------------------------------------------------------------------

_MAX_CONTENT_LENGTH = 1_000_000  # 1 MB total across all messages


class _RateLimiter:
    """Sliding-window rate limiter keyed by user ID."""

    def __init__(self, max_requests: int = 120, window_seconds: int = 60):
        self._max = max_requests
        self._window = window_seconds
        self._hits: Dict[str, collections.deque] = {}

    def check(self, key: str) -> Optional[int]:
        """Return seconds until retry if rate-limited, else None."""
        now = time.time()
        q = self._hits.setdefault(key, collections.deque())

        # Evict timestamps outside the window
        while q and q[0] <= now - self._window:
            q.popleft()

        if len(q) >= self._max:
            retry_after = int(q[0] + self._window - now) + 1
            return retry_after

        q.append(now)
        return None


_rate_limiter = _RateLimiter()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.SURREALDB_ENABLED:
        from nadirclaw.db import init_db
        await init_db()
        # Crash recovery: mark stale running/pending pipelines as interrupted
        from nadirclaw.pipeline_db import mark_interrupted_pipelines
        interrupted = await mark_interrupted_pipelines()
        if interrupted:
            logger.info("Crash recovery: marked %d stale pipeline(s) as interrupted", interrupted)
    # Initialize session history schema (SurrealDB action tables)
    try:
        from nadirclaw.history_middleware import ensure_schema
        ensure_schema()
        logger.info("Session history schema initialized")
    except Exception as e:
        logger.warning("Session history schema init failed (non-fatal): %s", e)
    yield
    if settings.SURREALDB_ENABLED:
        from nadirclaw.db import close_db
        await close_db()

app = FastAPI(
    title="NadirClaw",
    version=__version__,
    description="Open-source LLM router — simple prompts to free models, complex to premium",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount feature-coherent route modules (extracted from this file).
from nadirclaw.routes import blast as _blast_routes  # noqa: E402
from nadirclaw.routes import classify as _classify_routes  # noqa: E402
from nadirclaw.routes import observability as _observability_routes  # noqa: E402
from nadirclaw.routes import pipeline as _pipeline_routes  # noqa: E402
app.include_router(_blast_routes.router)
app.include_router(_classify_routes.router)
app.include_router(_observability_routes.router)
app.include_router(_pipeline_routes.router)


# ---------------------------------------------------------------------------
# Validation error handler — log request body for debugging
# ---------------------------------------------------------------------------

from fastapi.exceptions import RequestValidationError


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    body = await request.body()
    logger.error(
        "Validation error on %s %s: %s\nBody: %s",
        request.method,
        request.url.path,
        exc.errors(),
        body[:2000].decode("utf-8", errors="replace"),
    )
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

from nadirclaw.api_models import (
    ChatCompletionRequest,
    PipelineRequest,
)


# ---------------------------------------------------------------------------
# Logging helper
# ---------------------------------------------------------------------------

from nadirclaw.logging import log_request  # noqa: E402  (re-export for callers)


def _extract_request_metadata(request: ChatCompletionRequest) -> Dict[str, Any]:
    """Extract structured metadata from a ChatCompletionRequest for logging."""
    messages = request.messages
    system_msgs = [m for m in messages if m.role in ("system", "developer")]
    has_system = bool(system_msgs)
    system_len = sum(len(m.text_content()) for m in system_msgs) if has_system else 0

    # Tool definitions from model_extra (OpenAI-style "tools" field)
    extra = request.model_extra or {}
    tool_defs = extra.get("tools") or []
    # Tool-role messages (tool results in conversation)
    tool_msgs = [m for m in messages if m.role == "tool"]
    tool_count = len(tool_defs) + len(tool_msgs)

    system_text = " ".join(m.text_content() for m in system_msgs) if has_system else ""

    return {
        "stream": bool(request.stream),
        "message_count": len(messages),
        "has_system_prompt": has_system,
        "system_prompt_length": system_len,
        "system_prompt_text": system_text,
        "has_tools": tool_count > 0,
        "tool_count": tool_count,
        "requested_model": request.model,
    }


# ---------------------------------------------------------------------------
# Request preprocessing — rate-limit, size guard, id/timestamp, metadata
# ---------------------------------------------------------------------------

from dataclasses import dataclass  # noqa: E402


@dataclass
class _RequestContext:
    """Per-request scratch space shared between routing/dispatch/response stages."""
    request_id: str
    start_time: float
    prompt_text: str
    req_meta: Dict[str, Any]


def _preprocess_request(
    request: ChatCompletionRequest, current_user: UserSession
) -> _RequestContext:
    """Run pre-flight checks and gather data needed by every downstream stage.

    Raises ``HTTPException(429)`` when the per-user rate limit is exceeded
    and ``HTTPException(413)`` when total message content exceeds
    ``_MAX_CONTENT_LENGTH``.
    """
    retry_after = _rate_limiter.check(current_user.id)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Retry after {retry_after}s.",
            headers={"Retry-After": str(retry_after)},
        )

    total_content_len = sum(len(m.text_content()) for m in request.messages)
    if total_content_len > _MAX_CONTENT_LENGTH:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Request content too large ({total_content_len:,} chars). "
                f"Maximum is {_MAX_CONTENT_LENGTH:,} chars."
            ),
        )

    user_msgs = [m.text_content() for m in request.messages if m.role == "user"]
    prompt_text = user_msgs[-1] if user_msgs else ""

    return _RequestContext(
        request_id=str(uuid.uuid4()),
        start_time=time.time(),
        prompt_text=prompt_text,
        req_meta=_extract_request_metadata(request),
    )


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    log_dir = settings.LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    request_log = log_dir / "requests.jsonl"

    logger.info("=" * 60)
    logger.info("NadirClaw starting...")
    logger.info("Log file: %s", request_log.resolve())
    logger.info("=" * 60)

    # Optional OpenTelemetry
    from nadirclaw.telemetry import instrument_fastapi, setup_telemetry

    if setup_telemetry("nadirclaw"):
        instrument_fastapi(app)

    # Warm up the binary classifier
    try:
        from nadirclaw.classifier import warmup
        logger.info("Warming up binary classifier...")
        warmup()
        logger.info("Binary classifier ready")
    except Exception as e:
        logger.error("Failed to warm binary classifier: %s", e)
        raise

    # Show config
    try:
        import litellm
        litellm.set_verbose = False
        logger.info("Simple model:  %s", settings.SIMPLE_MODEL)
        logger.info("Complex model: %s", settings.COMPLEX_MODEL)
        if settings.has_explicit_tiers:
            logger.info("Tier config:   explicit (env vars)")
        else:
            logger.info("Tier config:   derived from NADIRCLAW_MODELS")
        logger.info("Ollama base:   %s", settings.OLLAMA_API_BASE)
        token = settings.AUTH_TOKEN
        if token:
            logger.info("Auth:          %s***", token[:6] if len(token) >= 6 else token)
        else:
            logger.info("Auth:          disabled (local-only)")
        # Log credential status
        from nadirclaw.credentials import detect_provider, get_credential_source

        for model in settings.tier_models:
            provider = detect_provider(model)
            if provider and provider != "ollama":
                source = get_credential_source(provider)
                if source:
                    logger.info("Credential:    %s → %s", provider, source)
                else:
                    logger.warning("Credential:    %s → NOT CONFIGURED", provider)

    except Exception as e:
        logger.warning("LiteLLM setup issue: %s", e)

    logger.info("Ready! Listening for requests...")
    logger.info("=" * 60)

    # Periodic pipeline state cleanup (every 6 hours)
    if settings.SURREALDB_ENABLED:
        asyncio.create_task(_periodic_state_cleanup())


async def _periodic_state_cleanup():
    """Periodically clean up old pipeline state records."""
    while True:
        await asyncio.sleep(6 * 3600)  # every 6 hours
        try:
            from nadirclaw.pipeline_db import cleanup_old_pipeline_states
            deleted = await cleanup_old_pipeline_states(72)
            if deleted:
                logger.info("Pipeline state cleanup: removed %d old records", deleted)
        except Exception as e:
            logger.debug("Pipeline state cleanup error: %s", e)


# ---------------------------------------------------------------------------
# Smart routing internals (re-exported from routing.py)
# ---------------------------------------------------------------------------

from nadirclaw.routing import (  # noqa: E402
    apply_routing_modifiers,
    get_session_cache,
    resolve_alias,
    resolve_profile,
)
from nadirclaw.routing import smart_route_full as _smart_route_full  # noqa: E402


# ---------------------------------------------------------------------------
# Route selection — picks the model + builds analysis_info
# ---------------------------------------------------------------------------

async def _try_parallel_dispatch(
    request: ChatCompletionRequest,
    ctx: "_RequestContext",
    analysis_info: Dict[str, Any],
) -> Optional[Any]:
    """Run parallel-dispatch when its gate triggers and return the response.

    Gate: complexity_score >= 0.40 AND ``should_parallel_dispatch`` returns
    True for the prompt's classifier_v2 verdict. On success, returns the
    OpenAI-compatible response (or an SSE stream when ``request.stream``).
    On gate-closed or any in-flight error, returns ``None`` so the caller
    falls through to single-model dispatch.
    """
    complexity_score_pd = analysis_info.get("complexity_score", 0) or 0
    if complexity_score_pd < 0.40:
        return None

    clf2_pd = classify_v2(ctx.prompt_text, complexity_score_pd)
    if not should_parallel_dispatch(
        complexity=clf2_pd.complexity,
        tier=clf2_pd.tier,
        privacy_required=clf2_pd.privacy_required,
        speed_priority=clf2_pd.speed_priority,
    ):
        return None

    model_a, model_b = get_model_pair(clf2_pd.tier, clf2_pd.task_type)
    logger.info(
        "Parallel dispatch | tier=%s | task_type=%s | A=%s | B=%s",
        clf2_pd.tier, clf2_pd.task_type, model_a, model_b,
    )
    try:
        raw_messages = [
            {"role": m.role, "content": m.content} for m in request.messages
        ]
        pd_result = await parallel_dispatch(
            messages=raw_messages,
            model_a=model_a,
            model_b=model_b,
            judge_model=settings.PARALLEL_JUDGE_MODEL,
            prompt_text=ctx.prompt_text,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )
        combined_content = format_parallel_response(pd_result)
        elapsed_ms = int((time.time() - ctx.start_time) * 1000)

        log_request({
            "type": "completion",
            "request_id": ctx.request_id,
            "prompt": ctx.prompt_text,
            "selected_model": "nadirclaw/parallel",
            "tier": clf2_pd.tier,
            "task_type": clf2_pd.task_type,
            "complexity_score": clf2_pd.complexity,
            "total_latency_ms": elapsed_ms,
            "parallel_dispatch": True,
            "model_a": model_a,
            "model_b": model_b,
            "preferred": pd_result.preferred,
            "latency_a_ms": pd_result.latency_a_ms,
            "latency_b_ms": pd_result.latency_b_ms,
            "status": "ok",
            **ctx.req_meta,
        })

        pd_metadata = {
            "request_id": ctx.request_id,
            "response_time_ms": elapsed_ms,
            "parallel_dispatch": True,
            "model_a": model_a,
            "model_b": model_b,
            "preferred": pd_result.preferred_model,
            "latency_a_ms": pd_result.latency_a_ms,
            "latency_b_ms": pd_result.latency_b_ms,
            "tier": clf2_pd.tier,
            "task_type": clf2_pd.task_type,
        }

        if request.stream:
            fake_response = {
                "content": combined_content,
                "finish_reason": "stop",
                "prompt_tokens": 0,
                "completion_tokens": 0,
            }
            return _build_streaming_response(
                ctx.request_id, "nadirclaw/parallel", fake_response,
                {"strategy": "parallel_dispatch"}, elapsed_ms,
            )

        return {
            "id": ctx.request_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "nadirclaw/parallel",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": combined_content},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "nadirclaw_metadata": pd_metadata,
        }
    except Exception as exc:
        logger.error("Parallel dispatch failed (%s) — falling back to single-model.", exc)
        return None


async def _try_pipeline_v2(
    request: ChatCompletionRequest,
    ctx: "_RequestContext",
    analysis_info: Dict[str, Any],
) -> Optional[Any]:
    """Run Pipeline V2 when enabled and the orchestrator wants it.

    Returns the OpenAI-compatible response (or SSE stream) on success,
    or ``None`` if V2 is disabled, ``should_pipeline`` declines, or
    ``orchestrator.run`` raises.
    """
    if not settings.PIPELINE_V2_ENABLED:
        return None

    complexity_score = analysis_info.get("complexity_score", 0) or 0
    if not _pipeline_orchestrator.should_pipeline(ctx.prompt_text, complexity_score):
        return None

    clf2 = classify_v2(ctx.prompt_text, complexity_score)
    logger.info(
        "Pipeline V2 activated | task_type=%s | complexity=%.2f | tier=%s",
        clf2.task_type, clf2.complexity, clf2.tier,
    )
    try:
        pipeline_result = await _pipeline_orchestrator.run(
            prompt=ctx.prompt_text,
            complexity=clf2.complexity,
            privacy_required=clf2.privacy_required,
        )
        elapsed_ms = int((time.time() - ctx.start_time) * 1000)

        log_request({
            "type": "completion",
            "request_id": ctx.request_id,
            "prompt": ctx.prompt_text,
            "selected_model": "nadirclaw/pipeline-v2",
            "tier": clf2.tier,
            "task_type": clf2.task_type,
            "complexity_score": clf2.complexity,
            "total_latency_ms": elapsed_ms,
            "pipeline_v2": True,
            "models_used": pipeline_result.models_used,
            "subtasks": len(pipeline_result.subtask_results),
            "status": "ok",
            **ctx.req_meta,
        })

        if request.stream:
            fake_response = {
                "content": pipeline_result.final_response,
                "finish_reason": "stop",
                "prompt_tokens": 0,
                "completion_tokens": 0,
            }
            return _build_streaming_response(
                ctx.request_id, "nadirclaw/pipeline-v2", fake_response,
                {"strategy": "pipeline_v2"}, elapsed_ms,
            )

        return {
            "id": ctx.request_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "nadirclaw/pipeline-v2",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": pipeline_result.final_response},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "nadirclaw_metadata": {
                "request_id": ctx.request_id,
                "response_time_ms": elapsed_ms,
                "pipeline_v2": True,
                "models_used": pipeline_result.models_used,
                "subtask_count": len(pipeline_result.subtask_results),
                "task_type": clf2.task_type,
                "tier": clf2.tier,
            },
        }
    except Exception as exc:
        logger.error("Pipeline V2 failed (%s) — falling back to single-model routing.", exc)
        return None


async def _finalize_response(
    *,
    request: ChatCompletionRequest,
    ctx: "_RequestContext",
    response_data: Dict[str, Any],
    selected_model: str,
    analysis_info: Dict[str, Any],
    provider: Optional[str],
    elapsed_ms: int,
) -> Any:
    """Wrap a completed dispatch into a client-facing response.

    Performs three side effects (in order):
      1. Build + write the enriched log entry via ``log_request``.
      2. Fire-and-forget the session-history write (``log_completion_async``).
      3. Fire-and-forget the live-dashboard event publish.

    Returns the OpenAI-compatible JSON dict, or an ``EventSourceResponse``
    when ``request.stream`` is true.
    """
    from nadirclaw.routing import estimate_cost

    total_tokens = response_data["prompt_tokens"] + response_data["completion_tokens"]

    log_entry: Dict[str, Any] = {
        "type": "completion",
        "request_id": ctx.request_id,
        "prompt": ctx.prompt_text,
        "selected_model": selected_model,
        "provider": provider,
        "tier": analysis_info.get("tier"),
        "confidence": analysis_info.get("confidence"),
        "complexity_score": analysis_info.get("complexity_score"),
        "classifier_latency_ms": analysis_info.get("classifier_latency_ms"),
        "total_latency_ms": elapsed_ms,
        "prompt_tokens": response_data["prompt_tokens"],
        "completion_tokens": response_data["completion_tokens"],
        "total_tokens": total_tokens,
        "response_preview": (response_data["content"] or "")[:100],
        "fallback_used": analysis_info.get("fallback_from"),
        "status": "ok",
        **ctx.req_meta,
    }

    if settings.LOG_RAW:
        log_entry["raw_messages"] = [
            {"role": m.role, "content": m.text_content()} for m in request.messages
        ]
        log_entry["raw_response"] = response_data.get("content", "")

    log_entry["estimated_cost_usd"] = estimate_cost(
        selected_model,
        response_data["prompt_tokens"],
        response_data["completion_tokens"],
    )
    log_entry["messages"] = [
        {"role": m.role, "content": m.text_content()} for m in request.messages
    ]
    log_entry["response_text"] = response_data.get("content", "")
    log_entry["system_prompt"] = next(
        (m.text_content() for m in request.messages if m.role == "system"), ""
    )

    log_request(log_entry)

    # Session history (SurrealDB + vector embedding) — fire and forget.
    try:
        from nadirclaw.history_middleware import log_completion_async
        asyncio.create_task(log_completion_async(
            request_id=ctx.request_id,
            messages=[{"role": m.role, "content": m.text_content()} for m in request.messages],
            model=selected_model,
            provider=provider,
            tier=analysis_info.get("tier", "unknown"),
            response_text=response_data.get("content", ""),
            prompt_tokens=response_data["prompt_tokens"],
            completion_tokens=response_data["completion_tokens"],
            latency_ms=elapsed_ms,
            stream=request.stream,
        ))
    except Exception:
        pass  # Never break the main flow.

    # Live-dashboard event — fire and forget.
    try:
        asyncio.create_task(event_bus.publish({
            "event_type": "routing_decision",
            "request_id": ctx.request_id,
            "tier": analysis_info.get("tier"),
            "selected_model": selected_model,
            "strategy": analysis_info.get("strategy"),
            "confidence": analysis_info.get("confidence"),
            "complexity_score": analysis_info.get("complexity_score"),
            "classifier_latency_ms": analysis_info.get("classifier_latency_ms"),
            "total_latency_ms": elapsed_ms,
            "prompt_tokens": response_data["prompt_tokens"],
            "completion_tokens": response_data["completion_tokens"],
            "prompt_preview": ctx.prompt_text[:80],
            "agentic": bool(analysis_info.get("routing_modifiers", {}).get("agentic", {}).get("is_agentic")),
            "reasoning": bool(analysis_info.get("routing_modifiers", {}).get("reasoning", {}).get("is_reasoning")),
            "fallback_used": analysis_info.get("fallback_from"),
            "status": "ok",
        }))
    except Exception:
        pass  # Never break the main flow.

    if request.stream:
        return _build_streaming_response(
            ctx.request_id, selected_model, response_data, analysis_info, elapsed_ms,
        )

    return {
        "id": ctx.request_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": selected_model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": response_data["content"]},
            "finish_reason": response_data["finish_reason"],
        }],
        "usage": {
            "prompt_tokens": response_data["prompt_tokens"],
            "completion_tokens": response_data["completion_tokens"],
            "total_tokens": total_tokens,
        },
        "nadirclaw_metadata": {
            "request_id": ctx.request_id,
            "response_time_ms": elapsed_ms,
            "routing": analysis_info,
        },
    }


async def _select_route(
    request: ChatCompletionRequest,
    current_user: UserSession,
    ctx: "_RequestContext",
) -> tuple:
    """Decide which model to dispatch to and return ``(model, analysis_info)``.

    Resolves in priority order:
      1. Routing profile (eco / premium / free / reasoning)
      2. Model alias (mapped via ``MODEL_ALIASES``)
      3. Direct model (any explicit model string that isn't ``"auto"``)
      4. Smart routing — session cache hit, then classifier + routing modifiers

    Mutates ``ctx.req_meta`` to inject ``complexity_score`` for downstream
    modifier logic. Caller is responsible for the pipeline pseudo-model
    short-circuit (``request.model in ("pipeline", "nadirclaw/pipeline")``);
    this function never returns an HTTP response, only a routing decision.
    """
    profile = resolve_profile(request.model)

    if profile == "eco":
        return settings.SIMPLE_MODEL, {
            "strategy": "profile:eco",
            "selected_model": settings.SIMPLE_MODEL,
            "tier": "simple",
            "confidence": 1.0,
            "complexity_score": 0,
        }
    if profile == "premium":
        return settings.COMPLEX_MODEL, {
            "strategy": "profile:premium",
            "selected_model": settings.COMPLEX_MODEL,
            "tier": "complex",
            "confidence": 1.0,
            "complexity_score": 0,
        }
    if profile == "free":
        return settings.FREE_MODEL, {
            "strategy": "profile:free",
            "selected_model": settings.FREE_MODEL,
            "tier": "free",
            "confidence": 1.0,
            "complexity_score": 0,
        }
    if profile == "reasoning":
        return settings.REASONING_MODEL, {
            "strategy": "profile:reasoning",
            "selected_model": settings.REASONING_MODEL,
            "tier": "reasoning",
            "confidence": 1.0,
            "complexity_score": 0,
        }

    if request.model and request.model != "auto" and profile is None:
        resolved = resolve_alias(request.model)
        if resolved:
            return resolved, {
                "strategy": "alias",
                "selected_model": resolved,
                "alias_from": request.model,
                "tier": "direct",
                "confidence": 1.0,
                "complexity_score": 0,
            }
        return request.model, {
            "strategy": "direct",
            "selected_model": request.model,
            "tier": "direct",
            "confidence": 1.0,
            "complexity_score": 0,
        }

    # Smart routing — auto / unspecified
    session_cache = get_session_cache()
    cached = session_cache.get(request.messages)
    if cached:
        cached_model, cached_tier = cached
        logger.debug("Session cache hit: model=%s tier=%s", cached_model, cached_tier)
        return cached_model, {
            "strategy": "session-cache",
            "selected_model": cached_model,
            "tier": cached_tier,
            "confidence": 1.0,
            "complexity_score": 0,
        }

    selected_model, analysis_info = await _smart_route_full(
        request.messages, current_user
    )

    ctx.req_meta["complexity_score"] = analysis_info.get("complexity_score", 0.5)

    selected_model, final_tier, routing_info = apply_routing_modifiers(
        base_model=selected_model,
        base_tier=analysis_info.get("tier", "simple"),
        request_meta=ctx.req_meta,
        messages=request.messages,
        simple_model=settings.SIMPLE_MODEL,
        complex_model=settings.COMPLEX_MODEL,
        reasoning_model=settings.REASONING_MODEL,
        free_model=settings.FREE_MODEL,
        local_reasoning_model=settings.LOCAL_REASONING_MODEL,
    )
    analysis_info["tier"] = final_tier
    analysis_info["selected_model"] = selected_model
    analysis_info["routing_modifiers"] = routing_info

    session_cache.put(request.messages, selected_model, final_tier)

    return selected_model, analysis_info


# ---------------------------------------------------------------------------
# Model dispatch — provider-specific call paths live in nadirclaw.dispatch.
# ---------------------------------------------------------------------------

from nadirclaw.dispatch import call_with_tier_fallback  # noqa: E402


def _request_to_messages(request: "ChatCompletionRequest") -> List[Dict[str, str]]:
    """Convert a Pydantic ChatCompletionRequest into raw role/content dicts."""
    return [{"role": m.role, "content": m.text_content()} for m in request.messages]


# ---------------------------------------------------------------------------
# /v1/chat/completions — full completion with routing
# ---------------------------------------------------------------------------

@app.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    current_user: UserSession = Depends(validate_local_auth),
):
    ctx = _preprocess_request(request, current_user)
    request_id = ctx.request_id
    start_time = ctx.start_time

    # --- Pipeline V2: force_model bypass (prevents re-routing for orchestrator self-calls) ---
    extra = request.model_extra or {}
    force_model = extra.get("x_nadirclaw_force_model", False)

    if force_model and request.model:
        from nadirclaw.credentials import detect_provider
        from nadirclaw.telemetry import record_llm_call, trace_span

        provider = detect_provider(request.model)
        try:
            response_data, _, _ = await call_with_tier_fallback(
                request.model,
                _request_to_messages(request),
                provider,
                {"strategy": "force_model", "tier": "direct"},
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                top_p=request.top_p,
            )
            elapsed_ms = int((time.time() - start_time) * 1000)
            content = response_data.get("content", "")

            if request.stream:
                return _build_streaming_response(
                    request_id, request.model, response_data,
                    {"strategy": "force_model"}, elapsed_ms,
                )

            return {
                "id": request_id,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": request.model,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": response_data.get("finish_reason", "stop"),
                }],
                "usage": {
                    "prompt_tokens": response_data.get("prompt_tokens", 0),
                    "completion_tokens": response_data.get("completion_tokens", 0),
                    "total_tokens": response_data.get("prompt_tokens", 0) + response_data.get("completion_tokens", 0),
                },
            }
        except Exception as e:
            logger.error("Force-model dispatch failed for %s: %s", request.model, e)
            raise HTTPException(status_code=500, detail=f"Force-model dispatch failed: {e}")

    # --- Pipeline pseudo-model bypass (delegate to /v1/pipeline) ---
    if request.model in ("pipeline", "nadirclaw/pipeline"):
        pipeline_req = PipelineRequest(
            messages=request.messages,
            model=None,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )
        return await pipeline_endpoint(pipeline_req, current_user)

    try:
        prompt_text = ctx.prompt_text
        req_meta = ctx.req_meta

        selected_model, analysis_info = await _select_route(request, current_user, ctx)

        # Short-circuit dispatchers — return a full response when their gate
        # triggers, or None to fall through to single-model dispatch below.
        parallel_response = await _try_parallel_dispatch(request, ctx, analysis_info)
        if parallel_response is not None:
            return parallel_response

        v2_response = await _try_pipeline_v2(request, ctx, analysis_info)
        if v2_response is not None:
            return v2_response

        # Resolve provider credential
        from nadirclaw.credentials import detect_provider, get_credential

        provider = detect_provider(selected_model)

        # ------------------------------------------------------------------
        # Call model — with automatic fallback on rate limit
        # ------------------------------------------------------------------
        from nadirclaw.telemetry import record_llm_call, trace_span

        with trace_span("chat_completion", {"nadirclaw.tier": analysis_info.get("tier")}) as span:
            response_data, selected_model, analysis_info = await call_with_tier_fallback(
                selected_model,
                _request_to_messages(request),
                provider,
                analysis_info,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                top_p=request.top_p,
            )

            elapsed_ms = int((time.time() - start_time) * 1000)

            record_llm_call(
                span,
                model=selected_model,
                provider=provider,
                prompt_tokens=response_data["prompt_tokens"],
                completion_tokens=response_data["completion_tokens"],
                tier=analysis_info.get("tier"),
                latency_ms=elapsed_ms,
            )

        return await _finalize_response(
            request=request,
            ctx=ctx,
            response_data=response_data,
            selected_model=selected_model,
            analysis_info=analysis_info,
            provider=provider,
            elapsed_ms=elapsed_ms,
        )

    except HTTPException:
        raise  # Re-raise FastAPI HTTP exceptions as-is
    except Exception as e:
        elapsed_ms = int((time.time() - start_time) * 1000)
        logger.error("Completion error: %s", e, exc_info=True)
        log_request({
            "type": "completion",
            "request_id": request_id,
            "status": "error",
            "error": str(e),
            "total_latency_ms": elapsed_ms,
        })
        raise HTTPException(
            status_code=500,
            detail=f"An internal error occurred. Request ID: {request_id}",
        )


def _build_streaming_response(
    request_id: str,
    model: str,
    response_data: Dict[str, Any],
    analysis_info: Dict[str, Any],
    elapsed_ms: int,
) -> EventSourceResponse:
    """Wrap a completed response as an OpenAI-compatible SSE stream.

    Sends the full content as a single chunk, then a finish chunk, then [DONE].
    This is a "fake" stream that converts a batch response into SSE format
    so streaming-only clients (like OpenClaw) can consume it.
    """

    async def event_generator():
        created = int(time.time())
        content = response_data.get("content", "") or ""

        # Chunk 1: the content
        chunk = {
            "id": request_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": content},
                    "finish_reason": None,
                }
            ],
        }
        yield {"data": json.dumps(chunk)}

        # Chunk 2: finish reason + usage
        finish_chunk = {
            "id": request_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": response_data.get("finish_reason", "stop"),
                }
            ],
            "usage": {
                "prompt_tokens": response_data.get("prompt_tokens", 0),
                "completion_tokens": response_data.get("completion_tokens", 0),
                "total_tokens": response_data.get("prompt_tokens", 0) + response_data.get("completion_tokens", 0),
            },
        }
        yield {"data": json.dumps(finish_chunk)}

        # Final: [DONE] sentinel
        yield {"data": "[DONE]"}

    return EventSourceResponse(event_generator(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# /v1/models & /health
# ---------------------------------------------------------------------------

@app.get("/v1/models")
async def list_models(
    current_user: UserSession = Depends(validate_local_auth),
) -> Dict[str, Any]:
    now = int(time.time())
    # Routing profiles (virtual models that NadirClaw routes automatically)
    routing_profiles = [
        {"id": "auto", "object": "model", "created": now, "owned_by": "nadirclaw"},
        {"id": "eco", "object": "model", "created": now, "owned_by": "nadirclaw"},
        {"id": "premium", "object": "model", "created": now, "owned_by": "nadirclaw"},
        {"id": "reasoning", "object": "model", "created": now, "owned_by": "nadirclaw"},
        {"id": "pipeline", "object": "model", "created": now, "owned_by": "nadirclaw",
         "description": "Multi-model pipeline (Builder → Judge → Compressor)"},
        {"id": "nadirclaw/pipeline", "object": "model", "created": now, "owned_by": "nadirclaw",
         "description": "Multi-model pipeline (Builder → Judge → Compressor)"},
    ]
    # Backend models
    backend_models = [
        {
            "id": m,
            "object": "model",
            "created": now,
            "owned_by": m.split("/")[0] if "/" in m else "api",
        }
        for m in settings.tier_models
    ]
    return {
        "object": "list",
        "data": routing_profiles + backend_models,
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": __version__,
        "simple_model": settings.SIMPLE_MODEL,
        "complex_model": settings.COMPLEX_MODEL,
    }


@app.get("/")
async def root():
    return {
        "name": "NadirClaw",
        "version": __version__,
        "description": "Open-source LLM router",
        "status": "ok",
    }
