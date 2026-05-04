"""Unit tests for ``_try_parallel_dispatch`` and ``_try_pipeline_v2``.

Pins the contract before B3 extracts both short-circuit branches out of
``chat_completions``. Each helper runs its gate, optionally executes
its dispatch, and returns either a full HTTP response dict (when the
gate triggers) or ``None`` (when the caller should fall through to
single-model dispatch).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def ctx():
    from nadirclaw.server import _RequestContext
    return _RequestContext(
        request_id="rid-test",
        start_time=0.0,
        prompt_text="hello there",
        req_meta={"stream": False, "message_count": 1},
    )


def _make_request(stream: bool = False):
    from nadirclaw.api_models import ChatCompletionRequest, ChatMessage
    return ChatCompletionRequest(
        messages=[ChatMessage(role="user", content="hello there")],
        stream=stream,
    )


# ---------------------------------------------------------------------------
# Parallel dispatch
# ---------------------------------------------------------------------------

class TestTryParallelDispatch:
    @pytest.mark.asyncio
    async def test_low_complexity_falls_through(self, ctx):
        """complexity_score < 0.40 → classifier_v2 not invoked, no parallel."""
        from nadirclaw.server import _try_parallel_dispatch
        result = await _try_parallel_dispatch(
            _make_request(), ctx, {"complexity_score": 0.10, "tier": "simple"},
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_gate_closed_falls_through(self, ctx):
        from nadirclaw.server import _try_parallel_dispatch

        with patch("nadirclaw.server.should_parallel_dispatch", return_value=False):
            result = await _try_parallel_dispatch(
                _make_request(), ctx, {"complexity_score": 0.85, "tier": "complex"},
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_gate_open_returns_combined_response(self, ctx):
        from nadirclaw.server import _try_parallel_dispatch

        pd_result = SimpleNamespace(
            preferred="A",
            preferred_model="model-a",
            latency_a_ms=120,
            latency_b_ms=180,
        )

        with patch("nadirclaw.server.should_parallel_dispatch", return_value=True), \
             patch("nadirclaw.server.get_model_pair", return_value=("model-a", "model-b")), \
             patch("nadirclaw.server.parallel_dispatch", AsyncMock(return_value=pd_result)), \
             patch("nadirclaw.server.format_parallel_response", return_value="combined!"):
            response = await _try_parallel_dispatch(
                _make_request(), ctx, {"complexity_score": 0.85, "tier": "complex"},
            )

        assert response is not None
        assert response["model"] == "nadirclaw/parallel"
        assert response["choices"][0]["message"]["content"] == "combined!"
        meta = response["nadirclaw_metadata"]
        assert meta["parallel_dispatch"] is True
        assert meta["model_a"] == "model-a"
        assert meta["model_b"] == "model-b"

    @pytest.mark.asyncio
    async def test_gate_open_streaming_returns_event_source(self, ctx):
        from sse_starlette.sse import EventSourceResponse
        from nadirclaw.server import _try_parallel_dispatch

        pd_result = SimpleNamespace(
            preferred="B", preferred_model="model-b", latency_a_ms=200, latency_b_ms=110,
        )
        with patch("nadirclaw.server.should_parallel_dispatch", return_value=True), \
             patch("nadirclaw.server.get_model_pair", return_value=("model-a", "model-b")), \
             patch("nadirclaw.server.parallel_dispatch", AsyncMock(return_value=pd_result)), \
             patch("nadirclaw.server.format_parallel_response", return_value="streamed!"):
            response = await _try_parallel_dispatch(
                _make_request(stream=True), ctx,
                {"complexity_score": 0.85, "tier": "complex"},
            )
        assert isinstance(response, EventSourceResponse)

    @pytest.mark.asyncio
    async def test_dispatch_exception_falls_through(self, ctx):
        """Any failure inside parallel_dispatch must yield None so the caller
        falls back to single-model dispatch (graceful degradation)."""
        from nadirclaw.server import _try_parallel_dispatch

        with patch("nadirclaw.server.should_parallel_dispatch", return_value=True), \
             patch("nadirclaw.server.get_model_pair", return_value=("model-a", "model-b")), \
             patch("nadirclaw.server.parallel_dispatch",
                   AsyncMock(side_effect=RuntimeError("boom"))):
            result = await _try_parallel_dispatch(
                _make_request(), ctx, {"complexity_score": 0.85, "tier": "complex"},
            )
        assert result is None


# ---------------------------------------------------------------------------
# Pipeline V2
# ---------------------------------------------------------------------------

class TestTryPipelineV2:
    @pytest.mark.asyncio
    async def test_v2_disabled_falls_through(self, ctx, monkeypatch):
        from nadirclaw.server import _try_pipeline_v2

        monkeypatch.setenv("NADIRCLAW_PIPELINE_V2_ENABLED", "false")
        result = await _try_pipeline_v2(_make_request(), ctx, {"complexity_score": 0.9})
        assert result is None

    @pytest.mark.asyncio
    async def test_should_pipeline_false_falls_through(self, ctx, monkeypatch):
        from nadirclaw.server import _try_pipeline_v2

        monkeypatch.setenv("NADIRCLAW_PIPELINE_V2_ENABLED", "true")
        with patch("nadirclaw.server._pipeline_orchestrator.should_pipeline", return_value=False):
            result = await _try_pipeline_v2(_make_request(), ctx, {"complexity_score": 0.9})
        assert result is None

    @pytest.mark.asyncio
    async def test_run_succeeds_returns_response(self, ctx, monkeypatch):
        from nadirclaw.server import _try_pipeline_v2

        monkeypatch.setenv("NADIRCLAW_PIPELINE_V2_ENABLED", "true")

        pipeline_result = SimpleNamespace(
            final_response="orchestrated answer",
            models_used=["m1", "m2"],
            subtask_results=[1, 2, 3],
        )

        with patch("nadirclaw.server._pipeline_orchestrator.should_pipeline", return_value=True), \
             patch("nadirclaw.server._pipeline_orchestrator.run",
                   AsyncMock(return_value=pipeline_result)):
            response = await _try_pipeline_v2(
                _make_request(), ctx, {"complexity_score": 0.9},
            )

        assert response is not None
        assert response["model"] == "nadirclaw/pipeline-v2"
        assert response["choices"][0]["message"]["content"] == "orchestrated answer"
        meta = response["nadirclaw_metadata"]
        assert meta["pipeline_v2"] is True
        assert meta["models_used"] == ["m1", "m2"]
        assert meta["subtask_count"] == 3

    @pytest.mark.asyncio
    async def test_run_raises_falls_through(self, ctx, monkeypatch):
        from nadirclaw.server import _try_pipeline_v2

        monkeypatch.setenv("NADIRCLAW_PIPELINE_V2_ENABLED", "true")

        with patch("nadirclaw.server._pipeline_orchestrator.should_pipeline", return_value=True), \
             patch("nadirclaw.server._pipeline_orchestrator.run",
                   AsyncMock(side_effect=RuntimeError("boom"))):
            result = await _try_pipeline_v2(
                _make_request(), ctx, {"complexity_score": 0.9},
            )
        assert result is None
