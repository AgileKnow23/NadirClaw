"""Unit tests for ``_select_route`` — the routing decision function.

Pins the contract before B2 extracts it out of ``chat_completions`` so the
extraction can land safely. Covers every branch the function must own:
profiles (eco / premium / free / reasoning), alias, direct, session-cache
hit, and the smart-route + routing-modifiers path.

The pipeline pseudo-model (``model="pipeline"``) is *not* tested here —
the B2 plan hoists that short-circuit out of the routing block to the
caller, since it returns an HTTP response rather than a routing decision.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple
from unittest.mock import patch

import pytest


@pytest.fixture
def user():
    from nadirclaw.auth import UserSession
    return UserSession({"id": "test-user", "email": "t@x", "name": "T"})


@pytest.fixture(autouse=True)
def _reset_session_cache():
    """Clear the session cache between tests so cache hits don't leak."""
    from nadirclaw.routing import get_session_cache
    cache = get_session_cache()
    cache._cache.clear()
    cache._access_order.clear() if hasattr(cache, "_access_order") else None
    yield
    cache._cache.clear()


def _ctx(prompt: str = "hello") -> Any:
    """Build a minimal _RequestContext for tests."""
    from nadirclaw.server import _RequestContext
    return _RequestContext(
        request_id="test-request-id",
        start_time=0.0,
        prompt_text=prompt,
        req_meta={},
    )


def _make_request(model=None, messages=None) -> Any:
    from nadirclaw.api_models import ChatCompletionRequest, ChatMessage
    return ChatCompletionRequest(
        messages=messages or [ChatMessage(role="user", content="hello")],
        model=model,
    )


# ---------------------------------------------------------------------------
# Profile branches
# ---------------------------------------------------------------------------

class TestProfileRouting:
    @pytest.mark.asyncio
    async def test_eco_profile(self, user):
        from nadirclaw.server import _select_route
        from nadirclaw.settings import settings

        model, analysis = await _select_route(_make_request("eco"), user, _ctx())

        assert model == settings.SIMPLE_MODEL
        assert analysis["strategy"] == "profile:eco"
        assert analysis["tier"] == "simple"
        assert analysis["confidence"] == 1.0

    @pytest.mark.asyncio
    async def test_premium_profile(self, user):
        from nadirclaw.server import _select_route
        from nadirclaw.settings import settings

        model, analysis = await _select_route(_make_request("premium"), user, _ctx())

        assert model == settings.COMPLEX_MODEL
        assert analysis["strategy"] == "profile:premium"
        assert analysis["tier"] == "complex"

    @pytest.mark.asyncio
    async def test_free_profile(self, user):
        from nadirclaw.server import _select_route
        from nadirclaw.settings import settings

        model, analysis = await _select_route(_make_request("free"), user, _ctx())

        assert model == settings.FREE_MODEL
        assert analysis["strategy"] == "profile:free"
        assert analysis["tier"] == "free"

    @pytest.mark.asyncio
    async def test_reasoning_profile(self, user):
        from nadirclaw.server import _select_route
        from nadirclaw.settings import settings

        model, analysis = await _select_route(_make_request("reasoning"), user, _ctx())

        assert model == settings.REASONING_MODEL
        assert analysis["strategy"] == "profile:reasoning"
        assert analysis["tier"] == "reasoning"


# ---------------------------------------------------------------------------
# Alias / direct
# ---------------------------------------------------------------------------

class TestAliasAndDirect:
    @pytest.mark.asyncio
    async def test_alias_resolves_and_records_alias_from(self, user):
        from nadirclaw.routing import MODEL_ALIASES
        from nadirclaw.server import _select_route

        alias_name, resolved_to = next(iter(MODEL_ALIASES.items()))

        model, analysis = await _select_route(_make_request(alias_name), user, _ctx())

        assert model == resolved_to
        assert analysis["strategy"] == "alias"
        assert analysis["alias_from"] == alias_name
        assert analysis["tier"] == "direct"

    @pytest.mark.asyncio
    async def test_direct_unrecognised_model_passes_through(self, user):
        from nadirclaw.server import _select_route

        explicit = "vendor/explicit-no-such-model-xyz"
        model, analysis = await _select_route(_make_request(explicit), user, _ctx())

        assert model == explicit
        assert analysis["strategy"] == "direct"
        assert analysis["tier"] == "direct"
        assert "alias_from" not in analysis


# ---------------------------------------------------------------------------
# Smart routing
# ---------------------------------------------------------------------------

class TestSmartRoute:
    @pytest.mark.asyncio
    async def test_session_cache_hit_skips_classifier(self, user):
        from nadirclaw.routing import get_session_cache
        from nadirclaw.server import _select_route

        request = _make_request(None)
        get_session_cache().put(request.messages, "cached/model-x", "moderate")

        # If smart_route_full got called we'd hit the real classifier. Patch it
        # to a sentinel that would explode the test if called.
        async def _explode(*args, **kwargs):
            raise AssertionError("smart_route_full must not run on cache hit")

        with patch("nadirclaw.server._smart_route_full", side_effect=_explode):
            model, analysis = await _select_route(request, user, _ctx())

        assert model == "cached/model-x"
        assert analysis["strategy"] == "session-cache"
        assert analysis["tier"] == "moderate"

    @pytest.mark.asyncio
    async def test_smart_route_applies_modifiers_and_caches(self, user):
        from nadirclaw.routing import get_session_cache
        from nadirclaw.server import _select_route

        # Pin the classifier output so the test is deterministic.
        async def fake_smart_route(messages, _user):
            return "raw/model", {
                "strategy": "smart-routing",
                "selected_model": "raw/model",
                "tier": "simple",
                "complexity_score": 0.3,
                "confidence": 0.9,
            }

        # Pin the modifiers so we observe how _select_route folds them in.
        def fake_modifiers(*, base_model, base_tier, **kwargs):
            return ("modified/model", "complex", {"agentic": {"is_agentic": True}})

        request = _make_request(None)

        with patch("nadirclaw.server._smart_route_full", side_effect=fake_smart_route), \
             patch("nadirclaw.server.apply_routing_modifiers", side_effect=fake_modifiers):
            model, analysis = await _select_route(request, user, _ctx())

        assert model == "modified/model"
        assert analysis["selected_model"] == "modified/model"
        assert analysis["tier"] == "complex"
        assert analysis["routing_modifiers"]["agentic"]["is_agentic"] is True

        # The decision must be cached for the next call.
        assert get_session_cache().get(request.messages) == ("modified/model", "complex")

    @pytest.mark.asyncio
    async def test_auto_keyword_triggers_smart_route(self, user):
        """``model="auto"`` is the explicit way to force smart routing."""
        from nadirclaw.server import _select_route

        async def fake_smart_route(messages, _user):
            return "auto/picked", {
                "strategy": "smart-routing",
                "selected_model": "auto/picked",
                "tier": "simple",
                "complexity_score": 0.2,
                "confidence": 0.8,
            }

        def fake_modifiers(*, base_model, base_tier, **kwargs):
            return (base_model, base_tier, {})

        with patch("nadirclaw.server._smart_route_full", side_effect=fake_smart_route), \
             patch("nadirclaw.server.apply_routing_modifiers", side_effect=fake_modifiers):
            model, analysis = await _select_route(_make_request("auto"), user, _ctx())

        assert model == "auto/picked"
        assert analysis["strategy"] == "smart-routing"
