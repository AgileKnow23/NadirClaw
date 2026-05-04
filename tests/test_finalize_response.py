"""Unit tests for ``_finalize_response`` — the post-dispatch wrap-up.

Pins the contract before B4 extracts it out of ``chat_completions``.
The helper consumes a completed dispatch and is responsible for:
  - enriching and writing the log entry (log_request)
  - firing-and-forgetting the session-history write
  - firing-and-forgetting the event_bus.publish for the live dashboard
  - returning either an OpenAI-compatible JSON response or an SSE stream
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def ctx():
    from nadirclaw.server import _RequestContext
    return _RequestContext(
        request_id="rid-test",
        start_time=0.0,
        prompt_text="hello there",
        req_meta={"stream": False, "message_count": 1, "has_tools": False},
    )


def _make_request(stream: bool = False):
    from nadirclaw.api_models import ChatCompletionRequest, ChatMessage
    return ChatCompletionRequest(
        messages=[
            ChatMessage(role="system", content="you are nice"),
            ChatMessage(role="user", content="hello there"),
        ],
        stream=stream,
    )


def _ok_response_data() -> Dict[str, Any]:
    return {
        "content": "the answer",
        "finish_reason": "stop",
        "prompt_tokens": 13,
        "completion_tokens": 5,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFinalizeResponse:
    @pytest.mark.asyncio
    async def test_non_streaming_returns_openai_compatible_json(self, ctx):
        from nadirclaw.server import _finalize_response

        result = await _finalize_response(
            request=_make_request(stream=False),
            ctx=ctx,
            response_data=_ok_response_data(),
            selected_model="vendor/model-x",
            analysis_info={
                "strategy": "smart-routing",
                "tier": "simple",
                "selected_model": "vendor/model-x",
                "complexity_score": 0.2,
                "confidence": 0.9,
            },
            provider="vendor",
            elapsed_ms=42,
        )

        assert result["object"] == "chat.completion"
        assert result["model"] == "vendor/model-x"
        assert result["choices"][0]["message"] == {
            "role": "assistant", "content": "the answer",
        }
        assert result["choices"][0]["finish_reason"] == "stop"
        assert result["usage"] == {
            "prompt_tokens": 13, "completion_tokens": 5, "total_tokens": 18,
        }
        meta = result["nadirclaw_metadata"]
        assert meta["request_id"] == ctx.request_id
        assert meta["response_time_ms"] == 42
        assert meta["routing"]["strategy"] == "smart-routing"

    @pytest.mark.asyncio
    async def test_streaming_returns_event_source(self, ctx):
        from sse_starlette.sse import EventSourceResponse
        from nadirclaw.server import _finalize_response

        result = await _finalize_response(
            request=_make_request(stream=True),
            ctx=ctx,
            response_data=_ok_response_data(),
            selected_model="vendor/model-x",
            analysis_info={"strategy": "smart-routing", "tier": "simple"},
            provider="vendor",
            elapsed_ms=42,
        )
        assert isinstance(result, EventSourceResponse)

    @pytest.mark.asyncio
    async def test_log_request_called_with_enriched_entry(self, ctx):
        from nadirclaw.server import _finalize_response

        with patch("nadirclaw.server.log_request") as log_mock:
            await _finalize_response(
                request=_make_request(),
                ctx=ctx,
                response_data=_ok_response_data(),
                selected_model="vendor/model-x",
                analysis_info={
                    "strategy": "smart-routing",
                    "tier": "simple",
                    "fallback_from": None,
                    "complexity_score": 0.2,
                    "confidence": 0.9,
                },
                provider="vendor",
                elapsed_ms=42,
            )

        assert log_mock.called
        entry = log_mock.call_args.args[0]
        assert entry["type"] == "completion"
        assert entry["status"] == "ok"
        assert entry["request_id"] == ctx.request_id
        assert entry["selected_model"] == "vendor/model-x"
        assert entry["provider"] == "vendor"
        assert entry["tier"] == "simple"
        assert entry["total_latency_ms"] == 42
        assert entry["total_tokens"] == 18
        assert entry["response_preview"] == "the answer"
        assert entry["fallback_used"] is None
        # The enrichment includes ctx.req_meta + cost + full conversation
        assert entry["message_count"] == 1
        assert "estimated_cost_usd" in entry
        assert entry["messages"][0]["role"] == "system"
        assert entry["response_text"] == "the answer"
        assert entry["system_prompt"] == "you are nice"

    @pytest.mark.asyncio
    async def test_event_bus_publish_is_scheduled(self, ctx):
        from nadirclaw.server import _finalize_response

        with patch("nadirclaw.server.event_bus") as bus:
            bus.publish = MagicMock()  # publish() returns a coroutine in real code
            # asyncio.create_task wraps it; for the mock, we just need it called.
            await _finalize_response(
                request=_make_request(),
                ctx=ctx,
                response_data=_ok_response_data(),
                selected_model="vendor/model-x",
                analysis_info={"strategy": "smart-routing", "tier": "simple"},
                provider="vendor",
                elapsed_ms=42,
            )
            assert bus.publish.called
            event = bus.publish.call_args.args[0]
            assert event["event_type"] == "routing_decision"
            assert event["request_id"] == ctx.request_id
            assert event["selected_model"] == "vendor/model-x"
            assert event["status"] == "ok"

    @pytest.mark.asyncio
    async def test_history_failure_does_not_break_flow(self, ctx):
        """history_middleware import failure must be swallowed."""
        from nadirclaw.server import _finalize_response

        # Force the lazy import to blow up
        import sys
        sentinel = MagicMock()
        sentinel.log_completion_async = MagicMock(side_effect=RuntimeError("history down"))
        with patch.dict(sys.modules, {"nadirclaw.history_middleware": sentinel}):
            result = await _finalize_response(
                request=_make_request(),
                ctx=ctx,
                response_data=_ok_response_data(),
                selected_model="vendor/model-x",
                analysis_info={"strategy": "smart-routing", "tier": "simple"},
                provider="vendor",
                elapsed_ms=42,
            )
        # Flow must complete and return the JSON envelope.
        assert result["object"] == "chat.completion"
