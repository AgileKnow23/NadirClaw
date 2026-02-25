"""Tests for pipeline state tracker."""

import pytest
from unittest.mock import AsyncMock, patch

from nadirclaw.pipeline_tracker import (
    PipelineTracker,
    StepState,
    register_tracker,
    get_tracker,
    unregister_tracker,
    _active_trackers,
)


class TestStepState:
    def test_defaults(self):
        step = StepState(role="builder", model="ollama/qwen3:8b")
        assert step.role == "builder"
        assert step.model == "ollama/qwen3:8b"
        assert step.status == "pending"
        assert step.started_at is None
        assert step.finished_at is None
        assert step.latency_ms == 0
        assert step.error is None

    def test_to_dict(self):
        step = StepState(role="judge", model="ollama/deepseek-r1:8b", status="running")
        d = step.to_dict()
        assert d["role"] == "judge"
        assert d["model"] == "ollama/deepseek-r1:8b"
        assert d["status"] == "running"
        assert "started_at" in d
        assert "latency_ms" in d

    def test_to_dict_with_error(self):
        step = StepState(role="builder", model="test", status="error", error="timeout")
        d = step.to_dict()
        assert d["error"] == "timeout"
        assert d["status"] == "error"


class TestPipelineTracker:
    def _make_plan(self, concurrent=False):
        """Build a minimal execution plan for testing."""
        steps = [
            {"agent": "blast_optimizer", "model": "ollama/qwen2.5:3b", "action": "optimize"},
            {"agent": "builder", "model": "ollama/qwen3:8b", "action": "generate code"},
            {"agent": "judge", "model": "ollama/deepseek-r1:8b", "action": "review"},
        ]
        return {
            "summary": "Test plan",
            "intent": "debugging",
            "concurrent": concurrent,
            "total_agents": 3,
            "total_phases": 1,
            "steps": steps,
            "phases": [],
        }

    def _make_concurrent_plan(self):
        steps = [
            {"agent": "blast_optimizer", "model": "ollama/qwen2.5:3b", "action": "optimize"},
            {"agent": "builder:impl", "model": "ollama/qwen3:8b", "action": "implement"},
            {"agent": "builder:tests", "model": "ollama/qwen3:8b", "action": "write tests"},
            {"agent": "synthesizer", "model": "ollama/qwen3:8b", "action": "merge"},
            {"agent": "judge", "model": "ollama/deepseek-r1:8b", "action": "review"},
        ]
        return {
            "summary": "Concurrent plan",
            "intent": "code_generation",
            "concurrent": True,
            "total_agents": 5,
            "total_phases": 4,
            "steps": steps,
            "phases": [],
        }

    def test_blast_optimizer_excluded_from_steps(self):
        plan = self._make_plan()
        tracker = PipelineTracker("test-1", "debugging", plan, "fix the bug")
        roles = [s.role for s in tracker.steps]
        assert "blast_optimizer" not in roles
        assert "builder" in roles
        assert "judge" in roles
        assert tracker.total_steps == 2

    def test_concurrent_plan_steps(self):
        plan = self._make_concurrent_plan()
        tracker = PipelineTracker("test-2", "code_generation", plan, "write code")
        roles = [s.role for s in tracker.steps]
        assert "blast_optimizer" not in roles
        assert "builder:impl" in roles
        assert "builder:tests" in roles
        assert "synthesizer" in roles
        assert "judge" in roles
        assert tracker.total_steps == 4
        assert tracker.concurrent is True

    @pytest.mark.asyncio
    @patch("nadirclaw.pipeline_tracker.settings")
    async def test_start_sets_running(self, mock_settings):
        mock_settings.SURREALDB_ENABLED = False
        plan = self._make_plan()
        tracker = PipelineTracker("test-3", "debugging", plan, "fix")
        await tracker.start()
        assert tracker.status == "running"
        assert tracker.started_at is not None

    def test_step_started_updates_state(self):
        plan = self._make_plan()
        tracker = PipelineTracker("test-4", "debugging", plan, "fix")
        tracker.step_started("builder", "ollama/qwen3:8b")
        step = tracker._find_step("builder")
        assert step.status == "running"
        assert step.started_at is not None

    def test_step_completed_increments_count(self):
        plan = self._make_plan()
        tracker = PipelineTracker("test-5", "debugging", plan, "fix")
        tracker.step_started("builder", "ollama/qwen3:8b")
        tracker.step_completed("builder", status="ok", latency_ms=1200)
        assert tracker.completed_steps == 1
        step = tracker._find_step("builder")
        assert step.status == "completed"
        assert step.latency_ms == 1200
        assert step.finished_at is not None

    def test_step_completed_error_still_increments(self):
        plan = self._make_plan()
        tracker = PipelineTracker("test-6", "debugging", plan, "fix")
        tracker.step_started("builder", "ollama/qwen3:8b")
        tracker.step_completed("builder", status="error", latency_ms=500, error="timeout")
        assert tracker.completed_steps == 1
        step = tracker._find_step("builder")
        assert step.status == "error"
        assert step.error == "timeout"

    def test_get_progress_snapshot(self):
        plan = self._make_plan()
        tracker = PipelineTracker("test-7", "debugging", plan, "fix")
        tracker.step_started("builder", "ollama/qwen3:8b")

        progress = tracker.get_progress()
        assert progress["pipeline_id"] == "test-7"
        assert progress["intent"] == "debugging"
        assert progress["status"] == "pending"
        assert progress["total_steps"] == 2
        assert progress["completed_steps"] == 0
        assert progress["current_step"] == "builder"
        assert progress["percent"] == 0
        assert len(progress["steps"]) == 2

    def test_get_progress_after_completion(self):
        plan = self._make_plan()
        tracker = PipelineTracker("test-8", "debugging", plan, "fix")
        tracker.step_started("builder", "ollama/qwen3:8b")
        tracker.step_completed("builder", status="ok", latency_ms=1000)
        tracker.step_started("judge", "ollama/deepseek-r1:8b")
        tracker.step_completed("judge", status="ok", latency_ms=800)

        progress = tracker.get_progress()
        assert progress["completed_steps"] == 2
        assert progress["percent"] == 100
        assert progress["current_step"] == ""

    @pytest.mark.asyncio
    @patch("nadirclaw.pipeline_tracker.settings")
    async def test_finish_sets_final_state(self, mock_settings):
        mock_settings.SURREALDB_ENABLED = False
        plan = self._make_plan()
        tracker = PipelineTracker("test-9", "debugging", plan, "fix")
        await tracker.start()
        await tracker.finish("ok")
        assert tracker.status == "ok"
        assert tracker.finished_at is not None

    @pytest.mark.asyncio
    @patch("nadirclaw.pipeline_tracker.settings")
    async def test_finish_with_error(self, mock_settings):
        mock_settings.SURREALDB_ENABLED = False
        plan = self._make_plan()
        tracker = PipelineTracker("test-10", "debugging", plan, "fix")
        await tracker.start()
        await tracker.finish("error", error="Builder crashed")
        assert tracker.status == "error"
        assert tracker.error == "Builder crashed"

    def test_find_step_returns_none_for_unknown_role(self):
        plan = self._make_plan()
        tracker = PipelineTracker("test-11", "debugging", plan, "fix")
        assert tracker._find_step("nonexistent") is None


class TestTrackerRegistry:
    def setup_method(self):
        """Clear the registry before each test."""
        _active_trackers.clear()

    def test_register_and_get(self):
        plan = {"steps": [{"agent": "builder", "model": "test"}], "concurrent": False}
        tracker = PipelineTracker("reg-1", "debugging", plan, "test")
        register_tracker(tracker)
        assert get_tracker("reg-1") is tracker

    def test_get_nonexistent_returns_none(self):
        assert get_tracker("nonexistent") is None

    def test_unregister(self):
        plan = {"steps": [{"agent": "builder", "model": "test"}], "concurrent": False}
        tracker = PipelineTracker("reg-2", "debugging", plan, "test")
        register_tracker(tracker)
        unregister_tracker("reg-2")
        assert get_tracker("reg-2") is None

    def test_unregister_nonexistent_is_safe(self):
        unregister_tracker("does-not-exist")  # should not raise
