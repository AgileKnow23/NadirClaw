"""Multi-model pipeline execution endpoints.

Extracted from server.py during the A3 refactor (BACKLOG P2).

The cluster is five endpoints:
- POST /v1/pipeline           — execute (Builder -> Judge -> Compressor)
- GET  /v1/pipeline/latest    — most recent trace
- GET  /v1/pipeline/stats     — per-intent / per-role stats
- GET  /v1/pipeline/{id}/progress — live tracker -> SurrealDB fallback
- GET  /v1/pipeline/{id}      — full trace by request id

`pipeline_endpoint` falls back to chat_completions when intent.needs_pipeline
is False — the import is lazy to avoid a circular dependency with server.py.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from nadirclaw.api_models import ChatCompletionRequest, PipelineRequest
from nadirclaw.auth import UserSession, validate_local_auth
from nadirclaw.settings import settings

router = APIRouter()
logger = logging.getLogger("nadirclaw")


@router.post("/v1/pipeline")
async def pipeline_endpoint(
    request: PipelineRequest,
    current_user: UserSession = Depends(validate_local_auth),
):
    """Execute a multi-model pipeline (Builder -> Judge -> Compressor).

    For simple queries, falls back to the standard chat completions endpoint.
    Returns OpenAI-compatible response with nadirclaw_metadata.pipeline.
    """
    # Lazy server-module imports to avoid circular dependency on app load.
    from nadirclaw.server import _log_request, _rate_limiter, chat_completions

    if not settings.PIPELINE_ENABLED:
        raise HTTPException(400, "Pipeline is disabled. Set NADIRCLAW_PIPELINE_ENABLED=true.")

    start_time = time.time()
    request_id = str(uuid.uuid4())

    retry_after = _rate_limiter.check(current_user.id)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Retry after {retry_after}s.",
            headers={"Retry-After": str(retry_after)},
        )

    try:
        from nadirclaw.intent import get_intent_classifier

        user_msgs = [m.text_content() for m in request.messages if m.role == "user"]
        prompt_text = user_msgs[-1] if user_msgs else ""

        classifier = get_intent_classifier()
        intent_result = classifier.classify(prompt_text)

        if not intent_result.needs_pipeline:
            chat_req = ChatCompletionRequest(
                messages=request.messages,
                model=request.model,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
            )
            return await chat_completions(chat_req, current_user)

        from nadirclaw.pipeline import (
            cache_pipeline_result,
            execute_pipeline,
            pipeline_result_to_dict,
        )

        messages_raw = [
            {"role": m.role, "content": m.text_content()} for m in request.messages
        ]

        # Model override: if user specified a concrete model, use it for builder
        model_override = None
        if request.model and request.model not in ("auto", "pipeline", "nadirclaw/pipeline", None):
            model_override = request.model

        pipeline_result = await execute_pipeline(
            intent=intent_result.intent,
            messages=messages_raw,
            pipeline_id=request_id,
            model_override=model_override,
        )

        cache_pipeline_result(pipeline_result)

        if settings.SURREALDB_ENABLED:
            try:
                from nadirclaw.pipeline_db import insert_pipeline_run
                asyncio.create_task(insert_pipeline_run(
                    pipeline_id=pipeline_result.pipeline_id,
                    intent=pipeline_result.intent,
                    status=pipeline_result.status,
                    total_latency_ms=pipeline_result.total_latency_ms,
                    steps=[
                        {
                            "role": s.role,
                            "model": s.model,
                            "status": s.status,
                            "latency_ms": s.latency_ms,
                            "prompt_tokens": s.prompt_tokens,
                            "completion_tokens": s.completion_tokens,
                        }
                        for s in pipeline_result.steps
                    ],
                    user_prompt_preview=prompt_text[:500],
                    final_output_preview=pipeline_result.final_content[:500],
                ))
            except Exception:
                pass

        elapsed_ms = int((time.time() - start_time) * 1000)

        total_prompt = sum(s.prompt_tokens for s in pipeline_result.steps)
        total_completion = sum(s.completion_tokens for s in pipeline_result.steps)

        _log_request({
            "type": "pipeline",
            "request_id": request_id,
            "prompt": prompt_text,
            "selected_model": "pipeline",
            "tier": "pipeline",
            "strategy": f"pipeline:{intent_result.intent}",
            "confidence": intent_result.confidence,
            "total_latency_ms": elapsed_ms,
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
            "total_tokens": total_prompt + total_completion,
            "status": pipeline_result.status,
        })

        return {
            "id": request_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "pipeline",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": pipeline_result.final_content,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": total_prompt,
                "completion_tokens": total_completion,
                "total_tokens": total_prompt + total_completion,
            },
            "nadirclaw_metadata": {
                "request_id": request_id,
                "response_time_ms": elapsed_ms,
                "intent": {
                    "category": intent_result.intent,
                    "confidence": intent_result.confidence,
                    "keyword_boost": intent_result.keyword_boost,
                },
                "pipeline": pipeline_result_to_dict(pipeline_result),
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        elapsed_ms = int((time.time() - start_time) * 1000)
        logger.error("Pipeline error: %s", e, exc_info=True)
        raise HTTPException(500, f"Pipeline error. Request ID: {request_id}")


@router.get("/v1/pipeline/latest")
async def pipeline_latest(
    current_user: UserSession = Depends(validate_local_auth),
):
    """Get the most recent pipeline trace."""
    from nadirclaw.pipeline import get_latest_pipeline, pipeline_result_to_dict

    result = get_latest_pipeline()
    if result is None:
        return {"pipeline": None}
    return {"pipeline": pipeline_result_to_dict(result)}


@router.get("/v1/pipeline/stats")
async def pipeline_stats_endpoint(
    since: Optional[str] = None,
    current_user: UserSession = Depends(validate_local_auth),
):
    """Get pipeline-specific stats (per-intent, per-role)."""
    from nadirclaw.pipeline_db import get_pipeline_stats

    stats = await get_pipeline_stats(since)
    return {"stats": stats}


@router.get("/v1/pipeline/{pipeline_id}/progress")
async def pipeline_progress(
    pipeline_id: str,
    current_user: UserSession = Depends(validate_local_auth),
):
    """Get pipeline execution progress (live tracker -> SurrealDB fallback)."""
    from nadirclaw.pipeline_tracker import get_tracker
    tracker = get_tracker(pipeline_id)
    if tracker:
        return {"progress": tracker.get_progress()}

    if settings.SURREALDB_ENABLED:
        from nadirclaw.pipeline_db import get_pipeline_state
        state = await get_pipeline_state(pipeline_id)
        if state:
            return {"progress": state}

    raise HTTPException(404, f"Pipeline {pipeline_id} not found")


@router.get("/v1/pipeline/{request_id}")
async def pipeline_by_id(
    request_id: str,
    current_user: UserSession = Depends(validate_local_auth),
):
    """Get a pipeline trace by request ID (cache -> SurrealDB)."""
    from nadirclaw.pipeline import get_pipeline_by_id, pipeline_result_to_dict

    result = get_pipeline_by_id(request_id)
    if result:
        return {"pipeline": pipeline_result_to_dict(result)}

    from nadirclaw.pipeline_db import get_pipeline_run

    db_result = await get_pipeline_run(request_id)
    if db_result:
        return {"pipeline": db_result}

    raise HTTPException(404, f"Pipeline run {request_id} not found")
