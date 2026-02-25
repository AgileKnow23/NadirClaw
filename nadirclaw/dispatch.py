"""Model dispatch — call the right backend for a model.

Extracted from server.py to be reusable by both the single-model router
and the multi-model pipeline engine without circular imports.
"""

import asyncio
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Any, Dict, List, Optional

from nadirclaw.settings import settings

logger = logging.getLogger("nadirclaw.dispatch")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class RateLimitExhausted(Exception):
    """Raised when a model's rate limit is exhausted after retries."""

    def __init__(self, model: str, retry_after: int = 60):
        self.model = model
        self.retry_after = retry_after
        super().__init__(f"Rate limit exhausted for {model} (retry in {retry_after}s)")


# ---------------------------------------------------------------------------
# Gemini native SDK helpers
# ---------------------------------------------------------------------------

def _strip_gemini_prefix(model: str) -> str:
    """Remove 'gemini/' prefix if present (LiteLLM style -> native name)."""
    return model.removeprefix("gemini/")


_gemini_clients: Dict[str, Any] = {}
_gemini_client_lock = Lock()
_gemini_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="gemini")


def _get_gemini_client(api_key: str):
    """Get or create a thread-safe, per-key google-genai Client."""
    with _gemini_client_lock:
        if api_key not in _gemini_clients:
            from google import genai
            _gemini_clients[api_key] = genai.Client(api_key=api_key)
        return _gemini_clients[api_key]


# ---------------------------------------------------------------------------
# Raw message dispatch (for pipeline — takes plain dicts, not Pydantic)
# ---------------------------------------------------------------------------

async def dispatch_raw(
    model: str,
    messages: List[Dict[str, str]],
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """Dispatch a model call using raw message dicts (not ChatCompletionRequest).

    This is the primary interface for the pipeline engine.
    Returns {"content": str, "finish_reason": str, "prompt_tokens": int, "completion_tokens": int}.
    """
    from nadirclaw.credentials import detect_provider, get_credential

    provider = detect_provider(model)

    if provider == "google":
        return await _call_gemini_raw(model, messages, provider, temperature, max_tokens)
    return await _call_litellm_raw(model, messages, provider, temperature, max_tokens)


async def _call_gemini_raw(
    model: str,
    messages: List[Dict[str, str]],
    provider: str,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    _retry_count: int = 0,
) -> Dict[str, Any]:
    """Call Gemini with raw message dicts."""
    from google.genai import types
    from google.genai.errors import ClientError
    from nadirclaw.credentials import get_credential

    MAX_RETRIES = 1

    api_key = get_credential(provider)
    if not api_key:
        raise RuntimeError("No Google/Gemini API key configured.")

    client = _get_gemini_client(api_key)
    native_model = _strip_gemini_prefix(model)

    system_parts = []
    contents = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role in ("system", "developer"):
            system_parts.append(content)
        else:
            contents.append(
                types.Content(
                    role="user" if role == "user" else "model",
                    parts=[types.Part.from_text(text=content)],
                )
            )

    gen_config_kwargs: Dict[str, Any] = {}
    if temperature is not None:
        gen_config_kwargs["temperature"] = temperature
    if max_tokens is not None:
        gen_config_kwargs["max_output_tokens"] = max_tokens

    generate_kwargs: Dict[str, Any] = {"model": native_model, "contents": contents}
    if gen_config_kwargs or system_parts:
        config_kwargs = {**gen_config_kwargs}
        if system_parts:
            config_kwargs["system_instruction"] = "\n".join(system_parts)
        generate_kwargs["config"] = types.GenerateContentConfig(**config_kwargs)

    loop = asyncio.get_running_loop()
    try:
        response = await asyncio.wait_for(
            loop.run_in_executor(
                _gemini_executor,
                lambda: client.models.generate_content(**generate_kwargs),
            ),
            timeout=120,
        )
    except asyncio.TimeoutError:
        return {"content": "Model timed out.", "finish_reason": "stop", "prompt_tokens": 0, "completion_tokens": 0}
    except ClientError as e:
        if e.code == 429 or "RESOURCE_EXHAUSTED" in str(e):
            retry_delay = 60
            delay_match = re.search(r"retry in (\d+(?:\.\d+)?)s", str(e), re.IGNORECASE)
            if delay_match:
                retry_delay = min(int(float(delay_match.group(1))) + 2, 120)
            if _retry_count < MAX_RETRIES:
                await asyncio.sleep(retry_delay)
                return await _call_gemini_raw(model, messages, provider, temperature, max_tokens, _retry_count + 1)
            raise RateLimitExhausted(model=model, retry_after=retry_delay)
        raise

    usage = getattr(response, "usage_metadata", None)
    prompt_tokens = getattr(usage, "prompt_token_count", 0) or 0
    completion_tokens = getattr(usage, "candidates_token_count", 0) or 0

    content = ""
    if response.candidates:
        candidate = response.candidates[0]
        if hasattr(candidate, "content") and candidate.content and candidate.content.parts:
            text_parts = [p.text for p in candidate.content.parts if hasattr(p, "text") and p.text]
            content = "".join(text_parts)

    if not content:
        try:
            content = response.text or ""
        except (ValueError, AttributeError):
            content = ""

    return {"content": content, "finish_reason": "stop", "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}


async def _call_litellm_raw(
    model: str,
    messages: List[Dict[str, str]],
    provider: Optional[str],
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """Call LiteLLM with raw message dicts."""
    import litellm
    from nadirclaw.credentials import get_credential

    if provider == "openai-codex":
        litellm_model = model.removeprefix("openai-codex/")
        cred_provider = "openai-codex"
    else:
        litellm_model = model
        cred_provider = provider

    call_kwargs: Dict[str, Any] = {"model": litellm_model, "messages": messages}
    if temperature is not None:
        call_kwargs["temperature"] = temperature
    if max_tokens is not None:
        call_kwargs["max_tokens"] = max_tokens

    if cred_provider and cred_provider != "ollama":
        api_key = get_credential(cred_provider)
        if api_key:
            call_kwargs["api_key"] = api_key

    try:
        response = await litellm.acompletion(**call_kwargs)
    except Exception as e:
        err_str = str(e).lower()
        if "429" in err_str or "rate" in err_str or "quota" in err_str or "resource_exhausted" in err_str:
            raise RateLimitExhausted(model=model, retry_after=60)
        raise

    return {
        "content": response.choices[0].message.content,
        "finish_reason": response.choices[0].finish_reason or "stop",
        "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
        "completion_tokens": response.usage.completion_tokens if response.usage else 0,
    }


# ---------------------------------------------------------------------------
# Fallback logic (reusable)
# ---------------------------------------------------------------------------

def _rate_limit_error_response(model: str) -> Dict[str, Any]:
    """Build a graceful response when all models are rate-limited."""
    return {
        "content": (
            "All configured models are currently rate-limited. "
            "Please wait a minute and try again."
        ),
        "finish_reason": "stop",
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }


async def dispatch_with_fallback(
    model: str,
    messages: List[Dict[str, str]],
    fallback_model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> tuple:
    """Try model, fall back to fallback_model on rate limit.

    Returns (response_data, actual_model_used).
    """
    try:
        response = await dispatch_raw(model, messages, temperature, max_tokens)
        return response, model
    except RateLimitExhausted:
        if fallback_model and fallback_model != model:
            logger.warning("Rate limit on %s — falling back to %s", model, fallback_model)
            try:
                response = await dispatch_raw(fallback_model, messages, temperature, max_tokens)
                return response, fallback_model
            except RateLimitExhausted:
                pass
        return _rate_limit_error_response(model), model
