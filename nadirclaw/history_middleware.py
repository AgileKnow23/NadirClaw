"""Session history middleware for NadirClaw.

Captures every /v1/chat/completions request to SurrealDB with vector embeddings.
This logs individual LLM interactions (every action), not just session summaries.

Non-blocking: all writes are fire-and-forget via asyncio.create_task().
"""

import asyncio
import json
import math
import time
import urllib.request
import urllib.error
import base64
from datetime import datetime, timezone
from typing import Any

# SurrealDB config (same as session-history system)
SURREAL_URL = "http://127.0.0.1:8000/sql"
SURREAL_NS = "claude"
SURREAL_DB = "history"
SURREAL_AUTH = base64.b64encode(b"root:root").decode()

# Ollama config
OLLAMA_URL = "http://127.0.0.1:11434/api/embed"
EMBED_MODEL = "nomic-embed-text"


def _surreal_execute(sql: str) -> bool:
    """Execute SurrealQL. Returns True on success."""
    full_sql = f"USE NS {SURREAL_NS} DB {SURREAL_DB}; {sql}"
    headers = {
        "Accept": "application/json",
        "Content-Type": "text/plain",
        "Authorization": f"Basic {SURREAL_AUTH}",
    }
    req = urllib.request.Request(SURREAL_URL, data=full_sql.encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode())
            for r in body:
                if isinstance(r, dict) and r.get("status") == "ERR":
                    return False
            return True
    except Exception:
        return False


def _get_embedding(text: str) -> list[float] | None:
    """Get embedding from Ollama. Returns None on failure."""
    payload = json.dumps({"model": EMBED_MODEL, "input": text}).encode()
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(OLLAMA_URL, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode())
            embeddings = body.get("embeddings", [])
            if embeddings and len(embeddings[0]) > 0:
                return embeddings[0]
    except Exception:
        pass
    return None


def _serialize(value: Any) -> str:
    """Serialize Python value to SurrealQL literal."""
    if value is None:
        return "NONE"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        if math.isnan(value) or math.isinf(value):
            return "0"
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace("'", "\\'")
        return f"'{escaped}'"
    if isinstance(value, list):
        items = ", ".join(_serialize(v) for v in value)
        return f"[{items}]"
    return f"'{str(value)}'"


def _extract_prompt_summary(messages: list[dict], max_len: int = 200) -> str:
    """Extract a readable summary from the message list."""
    # Get the last user message as the primary prompt
    user_msgs = [m for m in messages if m.get("role") == "user"]
    if not user_msgs:
        return "No user message"
    last = user_msgs[-1]
    content = last.get("content", "")
    if isinstance(content, list):
        # Handle multi-part messages
        text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
        content = " ".join(text_parts)
    if len(content) > max_len:
        return content[:max_len] + "..."
    return content


def log_completion(
    request_id: str,
    messages: list[dict],
    model: str,
    provider: str,
    tier: str,
    response_text: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: int,
    stream: bool,
    status: str = "ok",
):
    """Log a completion to SurrealDB with embedding. Fire-and-forget."""
    try:
        prompt_summary = _extract_prompt_summary(messages)
        response_preview = response_text[:300] if response_text else ""
        now = datetime.now(timezone.utc).isoformat()

        # Build the action record
        fields = {
            "request_id": request_id,
            "tool": "nadirclaw",
            "model": model,
            "provider": provider,
            "tier": tier,
            "prompt_summary": prompt_summary,
            "response_preview": response_preview,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "latency_ms": latency_ms,
            "stream": stream,
            "status": status,
            "message_count": len(messages),
            "timestamp": now,
        }

        field_sql = ", ".join(f"{k} = {_serialize(v)}" for k, v in fields.items())
        _surreal_execute(f"CREATE action SET {field_sql};")

        # Embed the prompt for semantic search (async-friendly)
        embed_text = f"Model: {model}\nTier: {tier}\nPrompt: {prompt_summary}"
        if response_preview:
            embed_text += f"\nResponse: {response_preview[:100]}"

        embedding = _get_embedding(embed_text)
        if embedding:
            emb_fields = {
                "request_id": request_id,
                "text": embed_text,
                "embedding": embedding,
                "model": model,
                "timestamp": now,
            }
            emb_sql = ", ".join(f"{k} = {_serialize(v)}" for k, v in emb_fields.items())
            _surreal_execute(f"CREATE action_embedding SET {emb_sql};")

    except Exception:
        pass  # Never break the main request flow


async def log_completion_async(
    request_id: str,
    messages: list[dict],
    model: str,
    provider: str,
    tier: str,
    response_text: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: int,
    stream: bool,
    status: str = "ok",
):
    """Async wrapper — runs log_completion in a thread pool."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: log_completion(
        request_id, messages, model, provider, tier,
        response_text, prompt_tokens, completion_tokens,
        latency_ms, stream, status,
    ))


def ensure_schema():
    """Create action and action_embedding tables if they don't exist."""
    schema = """
    DEFINE TABLE IF NOT EXISTS action SCHEMALESS;
    DEFINE INDEX IF NOT EXISTS idx_action_request ON action FIELDS request_id;
    DEFINE INDEX IF NOT EXISTS idx_action_model ON action FIELDS model;
    DEFINE INDEX IF NOT EXISTS idx_action_time ON action FIELDS timestamp;
    DEFINE TABLE IF NOT EXISTS action_embedding SCHEMALESS;
    DEFINE INDEX IF NOT EXISTS idx_aemb_request ON action_embedding FIELDS request_id;
    """
    _surreal_execute(schema)
