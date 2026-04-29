"""Observability endpoints — logs, events, dashboard, search, history,
knowledge files, and analytics.

Extracted from ``server.py`` during the A4 refactor (BACKLOG P2). Every
endpoint is read-only telemetry; none touches the LLM dispatch path.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from nadirclaw.auth import UserSession, validate_local_auth
from nadirclaw.events import event_bus
from nadirclaw.settings import settings

router = APIRouter()


# ---------------------------------------------------------------------------
# /v1/logs — view request logs
# ---------------------------------------------------------------------------

@router.get("/v1/logs")
async def view_logs(
    limit: int = 20,
    current_user: UserSession = Depends(validate_local_auth),
) -> Dict[str, Any]:
    """View recent request logs."""
    request_log = settings.LOG_DIR / "requests.jsonl"
    if not request_log.exists():
        return {"logs": [], "total": 0}

    lines = request_log.read_text().strip().split("\n")
    recent = lines[-limit:] if len(lines) > limit else lines
    logs = []
    for line in reversed(recent):
        try:
            logs.append(json.loads(line))
        except json.JSONDecodeError:
            pass

    return {"logs": logs, "total": len(lines), "showing": len(logs)}


# ---------------------------------------------------------------------------
# /v1/events/stream — real-time SSE stream of routing events
# ---------------------------------------------------------------------------

@router.get("/v1/events/stream")
async def event_stream(current_user: UserSession = Depends(validate_local_auth)):
    """Real-time SSE stream of routing events."""
    queue = event_bus.subscribe()

    async def generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    yield {"data": json.dumps(event, default=str)}
                except asyncio.TimeoutError:
                    yield {"data": json.dumps({"event_type": "heartbeat"})}
        except asyncio.CancelledError:
            pass
        finally:
            event_bus.unsubscribe(queue)

    return EventSourceResponse(generator(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# /v1/dashboard — snapshot report with aggregated metrics
# ---------------------------------------------------------------------------

@router.get("/v1/dashboard")
async def dashboard(
    limit: int = 100,
    current_user: UserSession = Depends(validate_local_auth),
):
    """Dashboard snapshot: aggregated metrics + recent events."""
    from nadirclaw.report import generate_report, load_log_entries

    entries = load_log_entries(settings.LOG_DIR / "requests.jsonl")
    report = generate_report(entries[-limit:]) if entries else {}
    recent_events = event_bus.get_history(20)

    return {
        "report": report,
        "recent_events": recent_events,
        "models": {
            "simple": settings.SIMPLE_MODEL,
            "complex": settings.COMPLEX_MODEL,
            "reasoning": settings.REASONING_MODEL,
        },
    }


# ---------------------------------------------------------------------------
# /v1/search — full-text search via SurrealDB
# ---------------------------------------------------------------------------

@router.get("/v1/search")
async def search_history(
    q: str,
    limit: int = 20,
    current_user: UserSession = Depends(validate_local_auth),
):
    """Full-text search across all routing history."""
    from nadirclaw.db import is_connected, search_requests
    if not is_connected():
        raise HTTPException(503, "SurrealDB not connected")
    results = await search_requests(q, limit=limit)
    return {"query": q, "results": results, "count": len(results)}


# ---------------------------------------------------------------------------
# /v1/history — filtered conversation history via SurrealDB
# ---------------------------------------------------------------------------

@router.get("/v1/history")
async def request_history(
    since: Optional[str] = None,
    model: Optional[str] = None,
    tier: Optional[str] = None,
    limit: int = 50,
    current_user: UserSession = Depends(validate_local_auth),
):
    """Query request history from SurrealDB with filters."""
    from nadirclaw.db import get_requests, is_connected
    if not is_connected():
        raise HTTPException(503, "SurrealDB not connected")
    results = await get_requests(since=since, model=model, tier=tier, limit=limit)
    return {"results": results, "count": len(results)}


# ---------------------------------------------------------------------------
# /v1/knowledge — continuous learning endpoints
# ---------------------------------------------------------------------------

@router.get("/v1/knowledge")
async def get_knowledge(current_user: UserSession = Depends(validate_local_auth)):
    """Return current knowledge files content."""
    from nadirclaw.knowledge import get_all_knowledge
    return get_all_knowledge()


@router.post("/v1/knowledge/learn")
async def trigger_learning(current_user: UserSession = Depends(validate_local_auth)):
    """Analyze recent logs and update knowledge files."""
    from nadirclaw.knowledge import learn_from_logs
    result = learn_from_logs(settings.LOG_DIR / "requests.jsonl")
    return result


# ---------------------------------------------------------------------------
# /v1/analytics — comprehensive analytics from SurrealDB
# ---------------------------------------------------------------------------

@router.get("/v1/analytics")
async def analytics_endpoint(
    since: Optional[str] = "30d",
    current_user: UserSession = Depends(validate_local_auth),
):
    """Aggregated analytics from SurrealDB: totals, per-model, latency, strategy, pipeline health."""
    from nadirclaw.db import get_analytics, is_connected

    if not is_connected():
        raise HTTPException(503, "SurrealDB not connected")

    data = await get_analytics(since)
    return {"since": since, "analytics": data}
