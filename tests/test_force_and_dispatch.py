"""Unit tests for ``_handle_force_model`` and ``_dispatch_single_model``.

Pins both helpers' contracts before B5 extracts them out of
``chat_completions``. Together with prior helpers they reduce
``chat_completions`` to a thin orchestrator under the 60-line ceiling.
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def ctx():
    from nadirclaw.server import _RequestContext
    return _RequestContext(
        request_id="rid-test",
        start_time=0.0,
        prompt_text="hi",
        req_meta={},
    )


def _make_request(model=None, force=False, stream=False):
    from nadirclaw.api_models import ChatCompletionRequest, ChatMessage
    return ChatCompletionRequest(
        messages=[ChatMessage(role="user", content="hi")],
        model=model,
        stream=stream,
        x_nadirclaw_force_model=force,
    )


def _ok_response_data() -> Dict[str, Any]:
    return {
        "content": "forced reply",
        "finish_reason": "stop",
        "prompt_tokens": 4,
        "completion_tokens": 3,
    }


# ---------------------------------------------------------------------------
# Force-model branch
# ---------------------------------------------------------------------------

class TestHandleForceModel:
    @pytest.mark.asyncio
    async def test_not_requested_returns_none(self, ctx):
        from nadirclaw.server import _handle_force_model
        result = await _handle_force_model(_make_request(model="x", force=False), ctx)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_model_returns_none(self, ctx):
        """force_model=True without a model is ignored — fall through."""
        from nadirclaw.server import _handle_force_model
        result = await _handle_force_model(_make_request(model=None, force=True), ctx)
        assert result is None

    @pytest.mark.asyncio
    async def test_success_returns_dict(self, ctx):
        from nadirclaw.server import _handle_force_model

        async def fake_dispatch(model, messages, provider, analysis_info, **kwargs):
            return _ok_response_data(), model, analysis_info

        with patch("nadirclaw.server.call_with_tier_fallback", side_effect=fake_dispatch):
            response = await _handle_force_model(
                _make_request(model="anthropic/claude-3-5", force=True), ctx,
            )

        assert response is not None
        assert response["model"] == "anthropic/claude-3-5"
        assert response["choices"][0]["message"] == {
            "role": "assistant", "content": "forced reply",
        }
        assert response["usage"]["total_tokens"] == 7
        # Force-model intentionally has no nadirclaw_metadata.
        assert "nadirclaw_metadata" not in response

    @pytest.mark.asyncio
    async def test_stream_returns_event_source(self, ctx):
        from sse_starlette.sse import EventSourceResponse
        from nadirclaw.server import _handle_force_model

        async def fake_dispatch(model, messages, provider, analysis_info, **kwargs):
            return _ok_response_data(), model, analysis_info

        with patch("nadirclaw.server.call_with_tier_fallback", side_effect=fake_dispatch):
            response = await _handle_force_model(
                _make_request(model="anthropic/claude-3-5", force=True, stream=True),
                ctx,
            )
        assert isinstance(response, EventSourceResponse)

    @pytest.mark.asyncio
    async def test_dispatch_failure_raises_500(self, ctx):
        from fastapi import HTTPException
        from nadirclaw.server import _handle_force_model

        with patch("nadirclaw.server.call_with_tier_fallback",
                   AsyncMock(side_effect=RuntimeError("model down"))):
            with pytest.raises(HTTPException) as exc_info:
                await _handle_force_model(
                    _make_request(model="anthropic/claude-3-5", force=True), ctx,
                )
        assert exc_info.value.status_code == 500
        assert "Force-model dispatch failed" in exc_info.value.detail


# ---------------------------------------------------------------------------
# Single-model dispatch
# ---------------------------------------------------------------------------

class TestDispatchSingleModel:
    @pytest.mark.asyncio
    async def test_returns_response_tuple(self, ctx):
        from nadirclaw.server import _dispatch_single_model

        async def fake_dispatch(model, messages, provider, analysis_info, **kwargs):
            return _ok_response_data(), model, {**analysis_info, "dispatched": True}

        with patch("nadirclaw.server.call_with_tier_fallback", side_effect=fake_dispatch):
            response_data, model, analysis_info, provider, elapsed_ms = (
                await _dispatch_single_model(
                    _make_request(),
                    ctx,
                    selected_model="vendor/model-y",
                    analysis_info={"strategy": "smart-routing", "tier": "simple"},
                )
            )

        assert response_data["content"] == "forced reply"
        assert model == "vendor/model-y"
        assert analysis_info["dispatched"] is True
        assert isinstance(elapsed_ms, int)
        assert elapsed_ms >= 0
        # Provider is detected from the model — for an unknown vendor it's
        # None (or whatever detect_provider decides).
        assert provider is None or isinstance(provider, str)

    @pytest.mark.asyncio
    async def test_passes_request_kwargs_through(self, ctx):
        """temperature / max_tokens / top_p must reach call_with_tier_fallback."""
        from nadirclaw.api_models import ChatCompletionRequest, ChatMessage
        from nadirclaw.server import _dispatch_single_model

        request = ChatCompletionRequest(
            messages=[ChatMessage(role="user", content="hi")],
            temperature=0.7,
            max_tokens=128,
            top_p=0.9,
        )

        captured: Dict[str, Any] = {}

        async def fake_dispatch(model, messages, provider, analysis_info, **kwargs):
            captured.update(kwargs)
            return _ok_response_data(), model, analysis_info

        with patch("nadirclaw.server.call_with_tier_fallback", side_effect=fake_dispatch):
            await _dispatch_single_model(
                request, ctx,
                selected_model="vendor/model-y",
                analysis_info={"strategy": "direct", "tier": "direct"},
            )

        assert captured == {"temperature": 0.7, "max_tokens": 128, "top_p": 0.9}
