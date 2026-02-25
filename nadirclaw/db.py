"""SurrealDB integration for NadirClaw — persistent history & searchable context."""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("nadirclaw.db")

# Module-level connection state
_db = None
_connected = False

# ---------------------------------------------------------------------------
# Schema DDL — executed on first connect
# ---------------------------------------------------------------------------

_SCHEMA_DDL = """
-- English full-text analyzer for searching conversations
DEFINE ANALYZER IF NOT EXISTS nadirclaw_analyzer
    TOKENIZERS class FILTERS lowercase, snowball(english);

-- Main request log table (schemaless to accept any extra fields)
DEFINE TABLE IF NOT EXISTS request SCHEMALESS;

DEFINE FIELD IF NOT EXISTS timestamp       ON request TYPE datetime;
DEFINE FIELD IF NOT EXISTS request_id      ON request TYPE string;
DEFINE FIELD IF NOT EXISTS type            ON request TYPE string;
DEFINE FIELD IF NOT EXISTS status          ON request TYPE string;
DEFINE FIELD IF NOT EXISTS selected_model  ON request TYPE string;
DEFINE FIELD IF NOT EXISTS provider        ON request TYPE string;
DEFINE FIELD IF NOT EXISTS tier            ON request TYPE string;
DEFINE FIELD IF NOT EXISTS strategy        ON request TYPE string;
DEFINE FIELD IF NOT EXISTS confidence      ON request TYPE option<float>;
DEFINE FIELD IF NOT EXISTS complexity_score ON request TYPE option<float>;
DEFINE FIELD IF NOT EXISTS classifier_latency_ms ON request TYPE option<int>;
DEFINE FIELD IF NOT EXISTS total_latency_ms ON request TYPE option<int>;
DEFINE FIELD IF NOT EXISTS prompt_tokens   ON request TYPE int DEFAULT 0;
DEFINE FIELD IF NOT EXISTS completion_tokens ON request TYPE int DEFAULT 0;
DEFINE FIELD IF NOT EXISTS total_tokens    ON request TYPE int DEFAULT 0;
DEFINE FIELD IF NOT EXISTS estimated_cost_usd ON request TYPE option<float>;
DEFINE FIELD IF NOT EXISTS prompt_text     ON request TYPE string DEFAULT '';
DEFINE FIELD IF NOT EXISTS response_text   ON request TYPE string DEFAULT '';
DEFINE FIELD IF NOT EXISTS system_prompt   ON request TYPE string DEFAULT '';
DEFINE FIELD IF NOT EXISTS messages        ON request TYPE array DEFAULT [];
DEFINE FIELD IF NOT EXISTS fallback_used   ON request TYPE option<string>;
DEFINE FIELD IF NOT EXISTS stream          ON request TYPE bool DEFAULT false;
DEFINE FIELD IF NOT EXISTS has_tools       ON request TYPE bool DEFAULT false;
DEFINE FIELD IF NOT EXISTS tool_count      ON request TYPE int DEFAULT 0;
DEFINE FIELD IF NOT EXISTS agentic         ON request TYPE bool DEFAULT false;
DEFINE FIELD IF NOT EXISTS reasoning       ON request TYPE bool DEFAULT false;
DEFINE FIELD IF NOT EXISTS requested_model ON request TYPE string DEFAULT '';

-- Standard indexes for filtering/grouping
DEFINE INDEX IF NOT EXISTS idx_request_id ON request FIELDS request_id UNIQUE;
DEFINE INDEX IF NOT EXISTS idx_timestamp  ON request FIELDS timestamp;
DEFINE INDEX IF NOT EXISTS idx_model      ON request FIELDS selected_model;
DEFINE INDEX IF NOT EXISTS idx_tier       ON request FIELDS tier;
DEFINE INDEX IF NOT EXISTS idx_status     ON request FIELDS status;

-- Full-text search indexes (the key feature)
DEFINE INDEX IF NOT EXISTS idx_prompt_ft   ON request FIELDS prompt_text
    FULLTEXT ANALYZER nadirclaw_analyzer BM25;
DEFINE INDEX IF NOT EXISTS idx_response_ft ON request FIELDS response_text
    FULLTEXT ANALYZER nadirclaw_analyzer BM25;
DEFINE INDEX IF NOT EXISTS idx_system_ft   ON request FIELDS system_prompt
    FULLTEXT ANALYZER nadirclaw_analyzer BM25;
"""


# ---------------------------------------------------------------------------
# Connection lifecycle
# ---------------------------------------------------------------------------

def is_connected() -> bool:
    """Check if the SurrealDB connection is active."""
    return _connected and _db is not None


async def init_db() -> None:
    """Connect to SurrealDB, authenticate, select namespace/database, and run schema DDL."""
    global _db, _connected

    from nadirclaw.settings import settings

    if not settings.SURREALDB_ENABLED:
        logger.info("SurrealDB disabled via NADIRCLAW_SURREALDB_ENABLED")
        return

    try:
        from surrealdb import AsyncSurreal

        _db = AsyncSurreal(settings.SURREALDB_URL)
        await _db.connect()
        await _db.signin({"username": settings.SURREALDB_USER, "password": settings.SURREALDB_PASS})
        await _db.use(settings.SURREALDB_NS, settings.SURREALDB_DB)

        # Run schema DDL statements one by one
        for statement in _SCHEMA_DDL.strip().split(";"):
            # Strip comment lines before checking if the statement is empty
            lines = [l for l in statement.split("\n") if not l.strip().startswith("--")]
            statement = "\n".join(lines).strip()
            if statement:
                await _db.query(statement + ";")

        # Run pipeline schema DDL
        from nadirclaw.pipeline_db import PIPELINE_SCHEMA_DDL
        for statement in PIPELINE_SCHEMA_DDL.strip().split(";"):
            lines = [l for l in statement.split("\n") if not l.strip().startswith("--")]
            statement = "\n".join(lines).strip()
            if statement:
                await _db.query(statement + ";")

        _connected = True
        logger.info("SurrealDB connected: %s (ns=%s, db=%s)",
                     settings.SURREALDB_URL, settings.SURREALDB_NS, settings.SURREALDB_DB)

    except Exception as e:
        _connected = False
        _db = None
        logger.warning("SurrealDB unavailable — continuing without persistent DB: %s", e)


async def close_db() -> None:
    """Disconnect from SurrealDB."""
    global _db, _connected

    if _db is not None:
        try:
            await _db.close()
        except Exception as e:
            logger.warning("Error closing SurrealDB connection: %s", e)
        finally:
            _db = None
            _connected = False
            logger.info("SurrealDB disconnected")


# ---------------------------------------------------------------------------
# CRUD operations
# ---------------------------------------------------------------------------

async def insert_request(entry: dict) -> None:
    """Insert a request record into SurrealDB.

    Transforms the log entry dict into the DB schema format.
    Silently returns on any failure (DB should never block requests).
    """
    if not is_connected():
        return

    try:
        # Build DB record from log entry
        record = {
            "timestamp": entry.get("timestamp", datetime.now(timezone.utc).isoformat()),
            "request_id": entry.get("request_id", ""),
            "type": entry.get("type", ""),
            "status": entry.get("status", ""),
            "selected_model": entry.get("selected_model", ""),
            "provider": entry.get("provider", ""),
            "tier": entry.get("tier", ""),
            "strategy": entry.get("strategy", ""),
            "confidence": entry.get("confidence"),
            "complexity_score": entry.get("complexity_score"),
            "classifier_latency_ms": _safe_int(entry.get("classifier_latency_ms")),
            "total_latency_ms": _safe_int(entry.get("total_latency_ms")),
            "prompt_tokens": _safe_int(entry.get("prompt_tokens", 0)),
            "completion_tokens": _safe_int(entry.get("completion_tokens", 0)),
            "total_tokens": _safe_int(entry.get("total_tokens", 0)),
            "estimated_cost_usd": entry.get("estimated_cost_usd"),
            "prompt_text": entry.get("prompt", "") or entry.get("prompt_text", ""),
            "response_text": entry.get("response_text", ""),
            "system_prompt": entry.get("system_prompt", ""),
            "messages": entry.get("messages", []),
            "fallback_used": entry.get("fallback_used"),
            "stream": bool(entry.get("stream", False)),
            "has_tools": bool(entry.get("has_tools", False)),
            "tool_count": _safe_int(entry.get("tool_count", 0)),
            "agentic": bool(entry.get("agentic", False)),
            "reasoning": bool(entry.get("reasoning", False)),
            "requested_model": entry.get("requested_model", ""),
        }

        await _db.query(
            "CREATE request SET "
            "timestamp = type::datetime($timestamp), "
            "request_id = $request_id, "
            "type = $type, "
            "status = $status, "
            "selected_model = $selected_model, "
            "provider = $provider, "
            "tier = $tier, "
            "strategy = $strategy, "
            "confidence = $confidence, "
            "complexity_score = $complexity_score, "
            "classifier_latency_ms = $classifier_latency_ms, "
            "total_latency_ms = $total_latency_ms, "
            "prompt_tokens = $prompt_tokens, "
            "completion_tokens = $completion_tokens, "
            "total_tokens = $total_tokens, "
            "estimated_cost_usd = $estimated_cost_usd, "
            "prompt_text = $prompt_text, "
            "response_text = $response_text, "
            "system_prompt = $system_prompt, "
            "messages = $messages, "
            "fallback_used = $fallback_used, "
            "stream = $stream, "
            "has_tools = $has_tools, "
            "tool_count = $tool_count, "
            "agentic = $agentic, "
            "reasoning = $reasoning, "
            "requested_model = $requested_model;",
            record,
        )
    except Exception as e:
        logger.debug("SurrealDB insert failed (non-fatal): %s", e)


async def search_requests(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Full-text search across prompt_text, response_text, and system_prompt.

    Returns results ranked by BM25 relevance score.
    """
    if not is_connected():
        return []

    try:
        result = await _db.query(
            "SELECT *, "
            "search::score(1) + search::score(2) + search::score(3) AS relevance "
            "FROM request "
            "WHERE prompt_text @1@ $query "
            "OR response_text @2@ $query "
            "OR system_prompt @3@ $query "
            "ORDER BY relevance DESC "
            "LIMIT $limit;",
            {"query": query, "limit": limit},
        )
        return _extract_results(result)
    except Exception as e:
        logger.error("SurrealDB search failed: %s", e)
        return []


async def get_requests(
    since: Optional[str] = None,
    model: Optional[str] = None,
    tier: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Query request history with optional filters."""
    if not is_connected():
        return []

    try:
        conditions = []
        params: Dict[str, Any] = {"limit": limit}

        if since:
            from nadirclaw.report import parse_since
            since_dt = parse_since(since)
            conditions.append("timestamp >= type::datetime($since)")
            params["since"] = since_dt.isoformat()

        if model:
            conditions.append("selected_model CONTAINS $model")
            params["model"] = model

        if tier:
            conditions.append("tier = $tier")
            params["tier"] = tier

        where_clause = " AND ".join(conditions) if conditions else "true"
        query = f"SELECT * FROM request WHERE {where_clause} ORDER BY timestamp DESC LIMIT $limit;"

        result = await _db.query(query, params)
        return _extract_results(result)
    except Exception as e:
        logger.error("SurrealDB query failed: %s", e)
        return []


async def get_summary_stats(since: Optional[str] = None) -> Dict[str, Any]:
    """Aggregated stats via SurrealQL — totals, per-model, per-tier."""
    if not is_connected():
        return {}

    try:
        params: Dict[str, Any] = {}
        where_clause = "true"

        if since:
            from nadirclaw.report import parse_since
            since_dt = parse_since(since)
            where_clause = "timestamp >= type::datetime($since)"
            params["since"] = since_dt.isoformat()

        # Total counts
        totals_q = (
            f"SELECT count() AS total, "
            f"math::sum(prompt_tokens) AS total_prompt_tokens, "
            f"math::sum(completion_tokens) AS total_completion_tokens, "
            f"math::sum(total_tokens) AS total_tokens, "
            f"math::sum(estimated_cost_usd) AS total_cost_usd "
            f"FROM request WHERE {where_clause} GROUP ALL;"
        )

        # Per-model breakdown
        model_q = (
            f"SELECT selected_model, count() AS requests, "
            f"math::sum(prompt_tokens) AS prompt_tokens, "
            f"math::sum(completion_tokens) AS completion_tokens, "
            f"math::sum(total_tokens) AS total_tokens, "
            f"math::sum(estimated_cost_usd) AS cost_usd "
            f"FROM request WHERE {where_clause} GROUP BY selected_model;"
        )

        # Per-tier breakdown
        tier_q = (
            f"SELECT tier, count() AS requests "
            f"FROM request WHERE {where_clause} GROUP BY tier;"
        )

        totals_res = await _db.query(totals_q, params)
        model_res = await _db.query(model_q, params)
        tier_res = await _db.query(tier_q, params)

        return {
            "totals": _extract_results(totals_res),
            "by_model": _extract_results(model_res),
            "by_tier": _extract_results(tier_res),
        }
    except Exception as e:
        logger.error("SurrealDB stats query failed: %s", e)
        return {}


async def get_analytics(since: Optional[str] = None) -> Dict[str, Any]:
    """Comprehensive analytics: totals, per-model, per-tier, latency, strategy, pipeline health."""
    if not is_connected():
        return {}

    try:
        params: Dict[str, Any] = {}
        where = "true"

        if since:
            from nadirclaw.report import parse_since
            since_dt = parse_since(since)
            where = "timestamp >= type::datetime($since)"
            params["since"] = since_dt.isoformat()

        # Total counts
        totals_q = (
            f"SELECT count() AS total_requests, "
            f"math::sum(prompt_tokens) AS total_prompt_tokens, "
            f"math::sum(completion_tokens) AS total_completion_tokens, "
            f"math::sum(total_tokens) AS total_tokens, "
            f"math::sum(estimated_cost_usd) AS total_cost_usd "
            f"FROM request WHERE {where} GROUP ALL;"
        )

        # Per-model breakdown with latency
        model_q = (
            f"SELECT selected_model, count() AS requests, "
            f"math::sum(prompt_tokens) AS prompt_tokens, "
            f"math::sum(completion_tokens) AS completion_tokens, "
            f"math::sum(total_tokens) AS total_tokens, "
            f"math::sum(estimated_cost_usd) AS cost_usd, "
            f"math::mean(total_latency_ms) AS avg_latency_ms "
            f"FROM request WHERE {where} GROUP BY selected_model;"
        )

        # Per-tier breakdown
        tier_q = (
            f"SELECT tier, count() AS requests "
            f"FROM request WHERE {where} GROUP BY tier;"
        )

        # Strategy breakdown
        strategy_q = (
            f"SELECT strategy, count() AS requests "
            f"FROM request WHERE {where} GROUP BY strategy;"
        )

        # Success/error counts
        status_q = (
            f"SELECT status, count() AS count "
            f"FROM request WHERE {where} GROUP BY status;"
        )

        # Agentic/reasoning counts
        special_q = (
            f"SELECT "
            f"count(IF agentic = true THEN 1 ELSE NONE END) AS agentic_count, "
            f"count(IF reasoning = true THEN 1 ELSE NONE END) AS reasoning_count "
            f"FROM request WHERE {where} GROUP ALL;"
        )

        totals_res = await _db.query(totals_q, params)
        model_res = await _db.query(model_q, params)
        tier_res = await _db.query(tier_q, params)
        strategy_res = await _db.query(strategy_q, params)
        status_res = await _db.query(status_q, params)
        special_res = await _db.query(special_q, params)

        # Pipeline health metrics
        pipeline_stats = {}
        try:
            from nadirclaw.pipeline_db import get_pipeline_stats
            pipeline_stats = await get_pipeline_stats(since)
        except Exception:
            pass

        return {
            "totals": _extract_results(totals_res),
            "by_model": _extract_results(model_res),
            "by_tier": _extract_results(tier_res),
            "by_strategy": _extract_results(strategy_res),
            "by_status": _extract_results(status_res),
            "special": _extract_results(special_res),
            "pipeline": pipeline_stats,
        }
    except Exception as e:
        logger.error("SurrealDB analytics query failed: %s", e)
        return {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_int(val: Any) -> Optional[int]:
    """Convert to int, return None on failure."""
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _extract_results(result: Any) -> list:
    """Extract result rows from SurrealDB query response.

    SurrealDB returns results in varying formats depending on the client version.
    This normalizes them to a plain list of dicts.
    """
    if result is None:
        return []
    # surrealdb-py returns list of QueryResult or list of dicts
    if isinstance(result, list):
        out = []
        for item in result:
            if isinstance(item, dict):
                # Direct dict result
                out.append(item)
            elif hasattr(item, "result"):
                # QueryResult object
                r = item.result
                if isinstance(r, list):
                    out.extend(r)
                elif isinstance(r, dict):
                    out.append(r)
            elif isinstance(item, list):
                out.extend(item)
        return out
    return []
