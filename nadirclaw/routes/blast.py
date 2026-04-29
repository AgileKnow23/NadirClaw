"""BLAST prompt-optimization preview endpoint.

Extracted from server.py during the A2 refactor (BACKLOG P2).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from nadirclaw.api_models import BlastPreviewRequest
from nadirclaw.auth import UserSession, validate_local_auth
from nadirclaw.settings import settings

router = APIRouter()


@router.post("/v1/blast")
async def blast_preview(
    request: BlastPreviewRequest,
    current_user: UserSession = Depends(validate_local_auth),
):
    """Preview BLAST prompt optimization without executing the pipeline.

    If intent is not provided, it will be auto-classified.
    """
    from nadirclaw.blast import build_execution_plan, get_blast_optimizer
    from nadirclaw.pipeline import _get_pipeline_configs

    if not settings.BLAST_ENABLED:
        raise HTTPException(400, "BLAST is disabled. Set NADIRCLAW_BLAST_ENABLED=true.")

    intent = request.intent
    if not intent:
        from nadirclaw.intent import get_intent_classifier
        classifier = get_intent_classifier()
        intent_result = classifier.classify(request.prompt)
        intent = intent_result.intent

    optimizer = get_blast_optimizer()
    result = await optimizer.optimize(request.prompt, intent)

    configs = _get_pipeline_configs()
    config = configs.get(intent, configs.get("code_generation", {}))
    plan = build_execution_plan(
        intent=intent,
        sections=result.sections,
        pipeline_config=config,
        blast_model=settings.BLAST_MODEL,
        used_llm=result.used_llm,
    )
    result.execution_plan = plan

    return {
        "original_prompt": result.original_prompt,
        "enhanced_prompt": result.enhanced_prompt,
        "intent": result.intent,
        "sections": result.sections,
        "latency_ms": result.latency_ms,
        "used_llm": result.used_llm,
        "execution_plan": result.execution_plan,
    }
