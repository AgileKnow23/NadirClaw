"""Golden-fixture contract tests for /v1/chat/completions.

These tests pin today's behavior of ``chat_completions`` so the upcoming
Phase B extraction (preprocess / select_route / dispatch_with_policy /
build_response) can refactor with confidence.

We mock at the LLM-dispatch boundary only:
  - ``nadirclaw.server.call_with_tier_fallback`` — every routing path lands
    here for normal completions, including the force-model branch.
  - ``nadirclaw.server.parallel_dispatch`` and
    ``nadirclaw.server.should_parallel_dispatch`` — for the parallel branch.
  - ``nadirclaw.server._pipeline_orchestrator.should_pipeline`` — kept
    False by default; one fixture flips it on and mocks ``run`` too.

Everything between the HTTP layer and that boundary runs for real:
auth, validation, rate limiter, classifier (binary + classifier_v2),
profile/alias resolution, routing modifiers, session cache, telemetry.

Snapshots assert a *stable subset* of the response shape so harmless
edits (UUIDs, timestamps) never break a fixture.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _ok_response(content: str = "ok") -> Dict[str, Any]:
    """Canonical success payload from ``call_with_tier_fallback``."""
    return {
        "content": content,
        "finish_reason": "stop",
        "prompt_tokens": 11,
        "completion_tokens": 7,
    }


def _completion_keys(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce a chat.completion response to the snapshot-stable subset."""
    msg = payload["choices"][0]["message"]
    routing = (payload.get("nadirclaw_metadata") or {}).get("routing", {})
    return {
        "object": payload["object"],
        "model": payload["model"],
        "role": msg["role"],
        "content": msg["content"],
        "finish_reason": payload["choices"][0]["finish_reason"],
        "usage": payload["usage"],
        "routing.strategy": routing.get("strategy"),
        "routing.tier": routing.get("tier"),
        "routing.selected_model": routing.get("selected_model"),
        "routing.fallback_from": routing.get("fallback_from"),
    }


def _parse_sse_chunks(text: str) -> List[Any]:
    """Pull the data: payloads out of an SSE stream, returning parsed JSON
    (or the literal string for ``[DONE]``)."""
    chunks: List[Any] = []
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            chunks.append("[DONE]")
            continue
        chunks.append(json.loads(payload))
    return chunks


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    """FastAPI TestClient — fresh per test so the rate limiter is reset."""
    from nadirclaw.server import app, _rate_limiter
    _rate_limiter._hits.clear()
    return TestClient(app)


@pytest.fixture(autouse=True)
def disable_pipeline_v2():
    """Pipeline V2 is opt-in via env. Make absolutely sure tests don't trip
    its gate even if a stray env var is set."""
    with patch("nadirclaw.server._pipeline_orchestrator.should_pipeline", return_value=False):
        yield


@pytest.fixture(autouse=True)
def disable_parallel_dispatch():
    """Parallel dispatch fires only for moderate/complex tiers. Force it off
    by default; one fixture re-enables it explicitly."""
    with patch("nadirclaw.server.should_parallel_dispatch", return_value=False):
        yield


# ---------------------------------------------------------------------------
# Buffered (non-streaming) success
# ---------------------------------------------------------------------------

class TestBufferedSuccess:
    def test_auto_routing_returns_completion(self, client):
        """Auto routing → smart_route_full → call_with_tier_fallback → JSON."""
        async def fake_dispatch(model, messages, provider, analysis_info, **kwargs):
            return _ok_response("buffered ok"), model, analysis_info

        with patch("nadirclaw.server.call_with_tier_fallback", side_effect=fake_dispatch):
            resp = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "what is 2+2?"}]},
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        snapshot = _completion_keys(body)

        # Stable subset
        assert snapshot["object"] == "chat.completion"
        assert snapshot["role"] == "assistant"
        assert snapshot["content"] == "buffered ok"
        assert snapshot["finish_reason"] == "stop"
        assert snapshot["usage"] == {
            "prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18,
        }
        assert snapshot["routing.strategy"] in (
            "smart-routing", "session-cache",
        )
        assert snapshot["routing.tier"] in (
            "trivial", "simple", "moderate", "complex", "expert",
        )
        assert snapshot["routing.fallback_from"] is None
        # The model in the response = the routed model = the dispatch model
        assert body["model"] == snapshot["routing.selected_model"]


# ---------------------------------------------------------------------------
# Streaming success
# ---------------------------------------------------------------------------

class TestStreamingSuccess:
    def test_stream_yields_content_then_finish_then_done(self, client):
        async def fake_dispatch(model, messages, provider, analysis_info, **kwargs):
            return _ok_response("stream ok"), model, analysis_info

        with patch("nadirclaw.server.call_with_tier_fallback", side_effect=fake_dispatch):
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                },
            )

        assert resp.status_code == 200, resp.text
        chunks = _parse_sse_chunks(resp.text)

        # Three events: content chunk, finish chunk, [DONE]
        assert len(chunks) == 3
        assert chunks[-1] == "[DONE]"

        content_chunk, finish_chunk, _ = chunks
        assert content_chunk["object"] == "chat.completion.chunk"
        assert content_chunk["choices"][0]["delta"] == {
            "role": "assistant", "content": "stream ok",
        }
        assert content_chunk["choices"][0]["finish_reason"] is None

        assert finish_chunk["choices"][0]["delta"] == {}
        assert finish_chunk["choices"][0]["finish_reason"] == "stop"
        assert finish_chunk["usage"] == {
            "prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18,
        }


# ---------------------------------------------------------------------------
# Routing profile: eco (skips classifier, forces SIMPLE_MODEL)
# ---------------------------------------------------------------------------

class TestEcoProfile:
    def test_eco_profile_skips_classifier(self, client):
        from nadirclaw.settings import settings

        captured: Dict[str, Any] = {}

        async def fake_dispatch(model, messages, provider, analysis_info, **kwargs):
            captured["model"] = model
            captured["analysis_info"] = analysis_info
            return _ok_response("eco"), model, analysis_info

        with patch("nadirclaw.server.call_with_tier_fallback", side_effect=fake_dispatch):
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": "eco",
                    "messages": [{"role": "user", "content": "trivial"}],
                },
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        snapshot = _completion_keys(body)

        assert captured["model"] == settings.SIMPLE_MODEL
        assert captured["analysis_info"]["strategy"] == "profile:eco"
        assert captured["analysis_info"]["tier"] == "simple"
        assert snapshot["routing.strategy"] == "profile:eco"
        assert snapshot["routing.tier"] == "simple"
        assert snapshot["routing.selected_model"] == settings.SIMPLE_MODEL


# ---------------------------------------------------------------------------
# Direct model request (skips classifier, no alias)
# ---------------------------------------------------------------------------

class TestDirectModel:
    def test_direct_model_routes_through_unchanged(self, client):
        async def fake_dispatch(model, messages, provider, analysis_info, **kwargs):
            return _ok_response("direct"), model, analysis_info

        with patch("nadirclaw.server.call_with_tier_fallback", side_effect=fake_dispatch):
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": "some-vendor/some-explicit-model-name",
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )

        assert resp.status_code == 200, resp.text
        snapshot = _completion_keys(resp.json())
        assert snapshot["routing.strategy"] == "direct"
        assert snapshot["routing.tier"] == "direct"
        assert snapshot["routing.selected_model"] == "some-vendor/some-explicit-model-name"


# ---------------------------------------------------------------------------
# Alias resolution
# ---------------------------------------------------------------------------

class TestAliasResolution:
    def test_alias_is_resolved_and_recorded(self, client):
        from nadirclaw.routing import MODEL_ALIASES
        # Pick any alias from the registry deterministically.
        alias_name, resolved_to = next(iter(MODEL_ALIASES.items()))

        async def fake_dispatch(model, messages, provider, analysis_info, **kwargs):
            return _ok_response("aliased"), model, analysis_info

        with patch("nadirclaw.server.call_with_tier_fallback", side_effect=fake_dispatch):
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": alias_name,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        routing = body["nadirclaw_metadata"]["routing"]
        assert routing["strategy"] == "alias"
        assert routing["alias_from"] == alias_name
        assert routing["selected_model"] == resolved_to


# ---------------------------------------------------------------------------
# Rate-limit fallback (analysis_info.fallback_from set)
# ---------------------------------------------------------------------------

class TestRateLimitFallback:
    def test_fallback_metadata_propagates(self, client):
        """``call_with_tier_fallback`` is the boundary that decides + records
        the fallback. We assert the server faithfully surfaces what it returns.
        """
        async def fake_dispatch(model, messages, provider, analysis_info, **kwargs):
            updated = {
                **analysis_info,
                "fallback_from": model,
                "selected_model": "fallback/model",
                "strategy": (analysis_info.get("strategy") or "smart-routing") + "+fallback",
            }
            return _ok_response("fellback"), "fallback/model", updated

        with patch("nadirclaw.server.call_with_tier_fallback", side_effect=fake_dispatch):
            resp = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hello"}]},
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        routing = body["nadirclaw_metadata"]["routing"]
        assert routing["fallback_from"] is not None
        assert routing["selected_model"] == "fallback/model"
        assert routing["strategy"].endswith("+fallback")
        assert body["model"] == "fallback/model"


# ---------------------------------------------------------------------------
# Force-model branch (Pipeline V2 self-call bypass)
# ---------------------------------------------------------------------------

class TestForceModel:
    def test_force_model_skips_routing(self, client):
        captured: Dict[str, Any] = {}

        async def fake_dispatch(model, messages, provider, analysis_info, **kwargs):
            captured["model"] = model
            captured["analysis_info"] = analysis_info
            return _ok_response("forced"), model, analysis_info

        with patch("nadirclaw.server.call_with_tier_fallback", side_effect=fake_dispatch):
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": "anthropic/claude-3-5-sonnet-20241022",
                    "messages": [{"role": "user", "content": "hi"}],
                    "x_nadirclaw_force_model": True,
                },
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()

        # Force-model branch dispatches with strategy=force_model, tier=direct
        assert captured["analysis_info"]["strategy"] == "force_model"
        assert captured["analysis_info"]["tier"] == "direct"
        assert captured["model"] == "anthropic/claude-3-5-sonnet-20241022"
        # And it short-circuits *before* the metadata-rich main return path,
        # so nadirclaw_metadata is intentionally absent.
        assert body["model"] == "anthropic/claude-3-5-sonnet-20241022"
        assert body["choices"][0]["message"]["content"] == "forced"
        assert "nadirclaw_metadata" not in body


# ---------------------------------------------------------------------------
# Validation error (HTTP 422 — body shape)
# ---------------------------------------------------------------------------

class TestValidationError:
    def test_missing_messages_returns_422(self, client):
        resp = client.post("/v1/chat/completions", json={})
        assert resp.status_code == 422
        assert "detail" in resp.json()


# ---------------------------------------------------------------------------
# Oversized content (HTTP 413 — content-length guard)
# ---------------------------------------------------------------------------

class TestOversizedContent:
    def test_total_content_over_1mb_returns_413(self, client):
        # _MAX_CONTENT_LENGTH = 1_000_000 — push just past it
        big = "x" * 1_000_001
        resp = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": big}]},
        )
        assert resp.status_code == 413
        assert "too large" in resp.json()["detail"].lower()
