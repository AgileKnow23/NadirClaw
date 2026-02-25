"""Pipeline state tracker for crash recovery and real-time progress.

Instantiated per pipeline run, threaded through execution flow.
Coordinates two concerns:
- DB persistence: upserts pipeline_state records at each state transition
- Progress events: publishes pipeline_progress events to event_bus for SSE
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from nadirclaw.events import event_bus
from nadirclaw.settings import settings

logger = logging.getLogger("nadirclaw.pipeline_tracker")


# ---------------------------------------------------------------------------
# StepState — tracks individual step progress
# ---------------------------------------------------------------------------

@dataclass
class StepState:
    """State of a single pipeline step."""
    role: str               # "builder", "builder:impl", "judge", etc.
    model: str
    status: str = "pending"  # pending | running | completed | error
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    latency_ms: int = 0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# PipelineTracker — coordinates DB persistence + progress events
# ---------------------------------------------------------------------------

class PipelineTracker:
    """Tracks pipeline execution state for crash recovery and live progress."""

    def __init__(
        self,
        pipeline_id: str,
        intent: str,
        execution_plan: Dict[str, Any],
        user_prompt: str,
    ):
        self.pipeline_id = pipeline_id
        self.intent = intent
        self.execution_plan = execution_plan
        self.user_prompt = user_prompt
        self.status = "pending"
        self.error: Optional[str] = None
        self.started_at: Optional[str] = None
        self.finished_at: Optional[str] = None
        self.concurrent = execution_plan.get("concurrent", False)

        # Build step list from execution plan, excluding blast_optimizer
        self.steps: List[StepState] = []
        for step in execution_plan.get("steps", []):
            agent = step.get("agent", "")
            if agent == "blast_optimizer":
                continue
            model = step.get("model", "")
            self.steps.append(StepState(role=agent, model=model))

        self.total_steps = len(self.steps)
        self.completed_steps = 0

    async def start(self) -> None:
        """Mark pipeline as running, persist initial state, publish event."""
        self.status = "running"
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._persist()
        await self._publish_progress()

    def step_started(self, role: str, model: str) -> None:
        """Mark a step as running and persist."""
        step = self._find_step(role)
        if step:
            step.status = "running"
            step.model = model
            step.started_at = datetime.now(timezone.utc).isoformat()
        self._persist()

    def step_completed(
        self,
        role: str,
        status: str = "completed",
        latency_ms: int = 0,
        error: Optional[str] = None,
    ) -> None:
        """Mark a step as done, update counters, persist, publish progress."""
        step = self._find_step(role)
        if step:
            step.status = "error" if status == "error" else "completed"
            step.finished_at = datetime.now(timezone.utc).isoformat()
            step.latency_ms = latency_ms
            step.error = error

        self.completed_steps = sum(
            1 for s in self.steps if s.status in ("completed", "error")
        )

        self._persist()
        # Fire-and-forget progress publish
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._publish_progress())
        except RuntimeError:
            pass

    async def finish(self, status: str, error: Optional[str] = None) -> None:
        """Mark pipeline as finished, persist final state, publish event."""
        self.status = status
        self.error = error
        self.finished_at = datetime.now(timezone.utc).isoformat()
        self.completed_steps = sum(
            1 for s in self.steps if s.status in ("completed", "error")
        )
        self._persist()
        await self._publish_progress()

    def get_progress(self) -> Dict[str, Any]:
        """Return a snapshot suitable for the polling endpoint."""
        current_step = ""
        for s in self.steps:
            if s.status == "running":
                current_step = s.role
                break

        percent = 0
        if self.total_steps > 0:
            percent = int((self.completed_steps / self.total_steps) * 100)

        return {
            "pipeline_id": self.pipeline_id,
            "intent": self.intent,
            "status": self.status,
            "concurrent": self.concurrent,
            "completed_steps": self.completed_steps,
            "total_steps": self.total_steps,
            "current_step": current_step,
            "percent": percent,
            "steps": [s.to_dict() for s in self.steps],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }

    # -- internal helpers --------------------------------------------------

    def _find_step(self, role: str) -> Optional[StepState]:
        """Find a step by role name."""
        for s in self.steps:
            if s.role == role:
                return s
        return None

    def _persist(self) -> None:
        """Fire-and-forget upsert to SurrealDB."""
        if not settings.SURREALDB_ENABLED:
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._do_persist())
        except RuntimeError:
            pass

    async def _do_persist(self) -> None:
        """Actual DB write."""
        try:
            from nadirclaw.pipeline_db import upsert_pipeline_state
            await upsert_pipeline_state(
                pipeline_id=self.pipeline_id,
                status=self.status,
                intent=self.intent,
                concurrent=self.concurrent,
                total_steps=self.total_steps,
                completed_steps=self.completed_steps,
                current_step=self.get_progress()["current_step"],
                steps=[s.to_dict() for s in self.steps],
                user_prompt_preview=self.user_prompt[:200],
                started_at=self.started_at,
                finished_at=self.finished_at,
                error=self.error,
            )
        except Exception as e:
            logger.debug("Failed to persist pipeline state: %s", e)

    async def _publish_progress(self) -> None:
        """Publish a progress event to the event bus."""
        try:
            await event_bus.publish({
                "event_type": "pipeline_progress",
                **self.get_progress(),
            })
        except Exception as e:
            logger.debug("Failed to publish pipeline progress: %s", e)


# ---------------------------------------------------------------------------
# Module-level tracker registry (for polling endpoint)
# ---------------------------------------------------------------------------

_active_trackers: Dict[str, PipelineTracker] = {}


def register_tracker(tracker: PipelineTracker) -> None:
    """Register a tracker for the polling endpoint."""
    _active_trackers[tracker.pipeline_id] = tracker


def get_tracker(pipeline_id: str) -> Optional[PipelineTracker]:
    """Get an active tracker by pipeline ID."""
    return _active_trackers.get(pipeline_id)


def unregister_tracker(pipeline_id: str) -> None:
    """Remove a tracker from the registry."""
    _active_trackers.pop(pipeline_id, None)
