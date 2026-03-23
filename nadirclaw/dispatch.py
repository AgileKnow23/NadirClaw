"""Model dispatch — call the right backend for a model.

Extracted from server.py to be reusable by both the single-model router
and the multi-model pipeline engine without circular imports.
"""

import asyncio
import json as _json
import logging
import os
import platform
import re
import shutil
import subprocess
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Any, Dict, List, Optional

from nadirclaw.settings import settings

_IS_WINDOWS = platform.system() == "Windows"

logger = logging.getLogger("nadirclaw.dispatch")

# ---------------------------------------------------------------------------
# Google Code Assist endpoint (accepts Gemini CLI OAuth tokens)
# ---------------------------------------------------------------------------
_CLOUDCODE_ENDPOINT = "https://cloudcode-pa.googleapis.com/v1internal"


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
# Gemini helpers
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


def _is_oauth_token(token: str) -> bool:
    """Check if a token is an OAuth access token (not an API key)."""
    return token.startswith("ya29.") or token.startswith("ya29a.")


# ---------------------------------------------------------------------------
# Claude CLI backend (uses Max/Pro subscription, not API key)
# ---------------------------------------------------------------------------

_claude_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="claude-cli")


async def _call_claude_cli(
    model: str,
    messages: List[Dict[str, str]],
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """Call Claude via the Claude CLI (`claude -p`).

    Uses the user's Claude Max/Pro subscription — no API key needed.
    The CLI must be installed and authenticated (`claude` on PATH).
    """
    # Build prompt from messages
    system_parts = []
    user_parts = []
    for m in messages:
        role = m.get("role", "user")
        text = m.get("content", "")
        if role in ("system", "developer"):
            system_parts.append(text)
        elif role == "assistant":
            user_parts.append(f"[Assistant previously said]: {text}")
        else:
            user_parts.append(text)

    prompt = "\n\n".join(user_parts) if user_parts else "Hello"

    # Strip provider prefix if present
    native_model = model.removeprefix("anthropic/")

    # Resolve binary — Claude CLI is a native exe, but resolve for robustness
    claude_bin = shutil.which("claude")
    if not claude_bin:
        raise RuntimeError(
            "Claude CLI not found. Install it: npm install -g @anthropic-ai/claude-code"
        )

    # Build command — Claude CLI doesn't expose max_tokens, only max budget
    cmd = [claude_bin, "-p", "--output-format", "json", "--model", native_model]
    if system_parts:
        cmd.extend(["--system-prompt", "\n".join(system_parts)])

    # Must unset CLAUDECODE to avoid "Cannot be launched inside another
    # Claude Code session" nesting error.
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    loop = asyncio.get_running_loop()

    def _run_cli():
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            env=env,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()[:500]
            raise RuntimeError(f"Claude CLI error (exit {result.returncode}): {stderr}")
        return result.stdout

    try:
        output = await asyncio.wait_for(
            loop.run_in_executor(_claude_executor, _run_cli),
            timeout=185,
        )
    except asyncio.TimeoutError:
        return {
            "content": "Model timed out.",
            "finish_reason": "stop",
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }

    # Parse JSON response
    if not output:
        raise RuntimeError("Claude CLI returned empty output")
    try:
        data = _json.loads(output)
    except (_json.JSONDecodeError, TypeError):
        # CLI sometimes outputs non-JSON (e.g. plain text in older versions)
        return {
            "content": str(output).strip(),
            "finish_reason": "stop",
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }

    if data.get("is_error"):
        error_msg = data.get("result", "Unknown Claude CLI error")
        raise RuntimeError(f"Claude CLI returned error: {error_msg}")

    content = data.get("result", "")
    usage = data.get("usage", {})
    return {
        "content": content,
        "finish_reason": "stop",
        "prompt_tokens": usage.get("input_tokens", 0),
        "completion_tokens": usage.get("output_tokens", 0),
    }


# ---------------------------------------------------------------------------
# Codex CLI backend (uses ChatGPT Plus/Pro subscription, not API key)
# ---------------------------------------------------------------------------

_codex_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="codex-cli")


async def _call_codex_cli(
    model: str,
    messages: List[Dict[str, str]],
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """Call OpenAI via the Codex CLI (`codex exec`).

    Uses the user's ChatGPT Plus/Pro subscription — no API key needed.
    The CLI must be installed and authenticated (`codex` on PATH).
    Overrides model_provider to "openai" to avoid circular routing through NadirClaw.
    """
    # Build prompt from messages
    system_parts = []
    user_parts = []
    for m in messages:
        role = m.get("role", "user")
        text = m.get("content", "")
        if role in ("system", "developer"):
            system_parts.append(text)
        elif role == "assistant":
            user_parts.append(f"[Assistant previously said]: {text}")
        else:
            user_parts.append(text)

    prompt = "\n\n".join(user_parts) if user_parts else "Hello"
    if system_parts:
        prompt = "\n".join(system_parts) + "\n\n" + prompt

    # Build command — force openai provider to avoid circular NadirClaw routing
    codex_bin = shutil.which("codex")
    if not codex_bin:
        raise RuntimeError(
            "Codex CLI not found. Install it: npm install -g @openai/codex"
        )

    cmd = [
        codex_bin, "exec",
        "--json",
        "--skip-git-repo-check",
        "--ephemeral",
        "-c", 'model_provider="openai"',
        prompt,
    ]

    loop = asyncio.get_running_loop()

    def _run_cli():
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            shell=_IS_WINDOWS,  # needed for npm .cmd shims on Windows
        )
        return result.stdout, result.stderr, result.returncode

    try:
        stdout, stderr, returncode = await asyncio.wait_for(
            loop.run_in_executor(_codex_executor, _run_cli),
            timeout=185,
        )
    except asyncio.TimeoutError:
        return {
            "content": "Model timed out.",
            "finish_reason": "stop",
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }

    # Parse JSONL output — look for agent_message items and usage
    content_parts = []
    prompt_tokens = 0
    completion_tokens = 0

    for line in stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = _json.loads(line)
        except _json.JSONDecodeError:
            continue

        etype = event.get("type", "")

        # Collect message text
        if etype == "item.completed":
            item = event.get("item", {})
            if item.get("type") == "agent_message":
                text = item.get("text", "")
                if text:
                    content_parts.append(text)

        # Collect usage
        elif etype == "turn.completed":
            usage = event.get("usage", {})
            prompt_tokens += usage.get("input_tokens", 0) + usage.get("cached_input_tokens", 0)
            completion_tokens += usage.get("output_tokens", 0)

        # Check for fatal errors
        elif etype == "turn.failed":
            error = event.get("error", {})
            raise RuntimeError(f"Codex CLI error: {error.get('message', 'unknown')}")

    content = "\n".join(content_parts)
    if not content and returncode != 0:
        raise RuntimeError(f"Codex CLI failed (exit {returncode}): {stderr[:500]}")

    return {
        "content": content,
        "finish_reason": "stop",
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }


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
    if provider == "anthropic":
        return await _call_claude_cli(model, messages, temperature, max_tokens)
    if provider == "openai-codex":
        return await _call_codex_cli(model, messages, temperature, max_tokens)
    return await _call_litellm_raw(model, messages, provider, temperature, max_tokens)


def _build_cloudcode_request(
    model: str,
    messages: List[Dict[str, str]],
    project_id: str,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> dict:
    """Build a request body for the cloudcode-pa Code Assist endpoint."""
    system_parts = []
    contents = []
    for m in messages:
        role = m.get("role", "user")
        text = m.get("content", "")
        if role in ("system", "developer"):
            system_parts.append(text)
        else:
            contents.append({
                "role": "user" if role == "user" else "model",
                "parts": [{"text": text}],
            })

    gen_config: Dict[str, Any] = {}
    if temperature is not None:
        gen_config["temperature"] = temperature
    if max_tokens is not None:
        gen_config["maxOutputTokens"] = max_tokens

    inner: Dict[str, Any] = {"contents": contents}
    if gen_config:
        inner["generationConfig"] = gen_config
    if system_parts:
        inner["systemInstruction"] = {
            "role": "user",
            "parts": [{"text": "\n".join(system_parts)}],
        }

    return {
        "model": _strip_gemini_prefix(model),
        "project": project_id,
        "request": inner,
    }


def _parse_cloudcode_response(data: dict) -> Dict[str, Any]:
    """Parse a cloudcode-pa generateContent response into our standard format."""
    resp = data.get("response", {})
    candidates = resp.get("candidates", [])
    content = ""
    if candidates:
        parts = candidates[0].get("content", {}).get("parts", [])
        content = "".join(p.get("text", "") for p in parts)

    usage = resp.get("usageMetadata", {})
    return {
        "content": content,
        "finish_reason": "stop",
        "prompt_tokens": usage.get("promptTokenCount", 0),
        "completion_tokens": usage.get("candidatesTokenCount", 0),
    }


async def _call_gemini_oauth(
    model: str,
    messages: List[Dict[str, str]],
    token: str,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    _retry_count: int = 0,
) -> Dict[str, Any]:
    """Call Gemini via cloudcode-pa Code Assist endpoint using OAuth token."""
    from nadirclaw.credentials import get_credential_metadata

    MAX_RETRIES = 1
    meta = get_credential_metadata("gemini")
    project_id = meta.get("project_id", "")

    body = _build_cloudcode_request(model, messages, project_id, temperature, max_tokens)
    data = _json.dumps(body).encode()
    url = f"{_CLOUDCODE_ENDPOINT}:generateContent"

    req = urllib.request.Request(url, data=data, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })

    loop = asyncio.get_running_loop()
    try:
        def _do_request():
            resp = urllib.request.urlopen(req, timeout=120)
            return _json.loads(resp.read())

        result = await asyncio.wait_for(
            loop.run_in_executor(_gemini_executor, _do_request),
            timeout=125,
        )
        return _parse_cloudcode_response(result)

    except asyncio.TimeoutError:
        return {"content": "Model timed out.", "finish_reason": "stop", "prompt_tokens": 0, "completion_tokens": 0}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()[:500]
        if e.code == 429:
            retry_delay = 5
            delay_match = re.search(r"retry.*?(\d+(?:\.\d+)?)s", err_body, re.IGNORECASE)
            if delay_match:
                retry_delay = min(int(float(delay_match.group(1))) + 2, 120)
            if _retry_count < MAX_RETRIES:
                logger.warning("Gemini rate limit — retrying in %ds", retry_delay)
                await asyncio.sleep(retry_delay)
                return await _call_gemini_oauth(model, messages, token, temperature, max_tokens, _retry_count + 1)
            raise RateLimitExhausted(model=model, retry_after=retry_delay)
        raise RuntimeError(f"Gemini API error {e.code}: {err_body}")


async def _call_gemini_raw(
    model: str,
    messages: List[Dict[str, str]],
    provider: str,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    _retry_count: int = 0,
) -> Dict[str, Any]:
    """Call Gemini — uses OAuth via cloudcode-pa if available, else SDK with API key."""
    from nadirclaw.credentials import get_credential

    MAX_RETRIES = 1

    token = get_credential(provider)
    if not token:
        raise RuntimeError(
            "No Google/Gemini credential configured. "
            "Run: nadirclaw auth gemini login"
        )

    # OAuth token → use cloudcode-pa endpoint (Gemini CLI compatible)
    if _is_oauth_token(token):
        return await _call_gemini_oauth(model, messages, token, temperature, max_tokens, _retry_count)

    # API key → use google-genai SDK directly
    from google.genai import types
    from google.genai.errors import ClientError

    client = _get_gemini_client(token)
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
