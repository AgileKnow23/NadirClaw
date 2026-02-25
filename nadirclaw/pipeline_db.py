"""SurrealDB schema + CRUD for pipeline tables.

Three tables alongside the existing `request` table:
- pipeline_run  — pipeline execution tracking
- decision      — architecture decisions & patterns (persistent memory)
- repo_context  — module-level summaries

Includes SOC2-safe redaction gate that strips secrets before storage.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("nadirclaw.pipeline_db")


# ---------------------------------------------------------------------------
# SOC2 redaction gate
# ---------------------------------------------------------------------------

_SECRET_PATTERNS = [
    re.compile(r"sbp_[A-Za-z0-9_\-]{10,}"),           # Supabase tokens
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),             # OpenAI / generic API keys
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),         # Anthropic tokens
    re.compile(r"eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}"),  # JWTs
    re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),  # Email addresses
    re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b"),       # Phone numbers (US)
    re.compile(r"(?:password|secret|token|api_key|apikey)\s*[:=]\s*\S+", re.IGNORECASE),
]


def redact_secrets(text: str) -> str:
    """Strip tokens, API keys, JWTs, emails, and phone numbers from text."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


# ---------------------------------------------------------------------------
# Schema DDL for pipeline tables
# ---------------------------------------------------------------------------

PIPELINE_SCHEMA_DDL = """
-- Pipeline run tracking table
DEFINE TABLE IF NOT EXISTS pipeline_run SCHEMALESS;

DEFINE FIELD IF NOT EXISTS pipeline_id    ON pipeline_run TYPE string;
DEFINE FIELD IF NOT EXISTS timestamp      ON pipeline_run TYPE datetime;
DEFINE FIELD IF NOT EXISTS intent         ON pipeline_run TYPE string;
DEFINE FIELD IF NOT EXISTS status         ON pipeline_run TYPE string;
DEFINE FIELD IF NOT EXISTS total_latency_ms ON pipeline_run TYPE int DEFAULT 0;
DEFINE FIELD IF NOT EXISTS steps          ON pipeline_run TYPE array DEFAULT [];
DEFINE FIELD IF NOT EXISTS user_prompt_preview ON pipeline_run TYPE string DEFAULT '';
DEFINE FIELD IF NOT EXISTS final_output_preview ON pipeline_run TYPE string DEFAULT '';

DEFINE INDEX IF NOT EXISTS idx_pipeline_id ON pipeline_run FIELDS pipeline_id UNIQUE;
DEFINE INDEX IF NOT EXISTS idx_pipeline_ts ON pipeline_run FIELDS timestamp;
DEFINE INDEX IF NOT EXISTS idx_pipeline_intent ON pipeline_run FIELDS intent;

-- Decision table — architecture decisions & patterns (persistent memory)
DEFINE TABLE IF NOT EXISTS decision SCHEMALESS;

DEFINE FIELD IF NOT EXISTS summary        ON decision TYPE string DEFAULT '';
DEFINE FIELD IF NOT EXISTS patterns       ON decision TYPE array DEFAULT [];
DEFINE FIELD IF NOT EXISTS trade_offs     ON decision TYPE array DEFAULT [];
DEFINE FIELD IF NOT EXISTS tags           ON decision TYPE array DEFAULT [];
DEFINE FIELD IF NOT EXISTS intent         ON decision TYPE string DEFAULT '';
DEFINE FIELD IF NOT EXISTS pipeline_id    ON decision TYPE string DEFAULT '';
DEFINE FIELD IF NOT EXISTS timestamp      ON decision TYPE datetime;

DEFINE INDEX IF NOT EXISTS idx_decision_ts ON decision FIELDS timestamp;
DEFINE INDEX IF NOT EXISTS idx_decision_intent ON decision FIELDS intent;

-- Full-text search on decision summaries
DEFINE INDEX IF NOT EXISTS idx_decision_ft ON decision FIELDS summary
    FULLTEXT ANALYZER nadirclaw_analyzer BM25;

-- Repo context table — module-level summaries
DEFINE TABLE IF NOT EXISTS repo_context SCHEMALESS;

DEFINE FIELD IF NOT EXISTS module_path    ON repo_context TYPE string;
DEFINE FIELD IF NOT EXISTS summary        ON repo_context TYPE string DEFAULT '';
DEFINE FIELD IF NOT EXISTS language       ON repo_context TYPE string DEFAULT '';
DEFINE FIELD IF NOT EXISTS patterns       ON repo_context TYPE array DEFAULT [];
DEFINE FIELD IF NOT EXISTS timestamp      ON repo_context TYPE datetime;

DEFINE INDEX IF NOT EXISTS idx_repo_module ON repo_context FIELDS module_path UNIQUE;

DEFINE INDEX IF NOT EXISTS idx_repo_ft ON repo_context FIELDS summary
    FULLTEXT ANALYZER nadirclaw_analyzer BM25;

-- Pipeline state tracking table (crash recovery + live progress)
DEFINE TABLE IF NOT EXISTS pipeline_state SCHEMALESS;

DEFINE FIELD IF NOT EXISTS pipeline_id     ON pipeline_state TYPE string;
DEFINE FIELD IF NOT EXISTS status          ON pipeline_state TYPE string;
DEFINE FIELD IF NOT EXISTS intent          ON pipeline_state TYPE string;
DEFINE FIELD IF NOT EXISTS concurrent      ON pipeline_state TYPE bool DEFAULT false;
DEFINE FIELD IF NOT EXISTS total_steps     ON pipeline_state TYPE int DEFAULT 0;
DEFINE FIELD IF NOT EXISTS completed_steps ON pipeline_state TYPE int DEFAULT 0;
DEFINE FIELD IF NOT EXISTS current_step    ON pipeline_state TYPE string DEFAULT '';
DEFINE FIELD IF NOT EXISTS steps           ON pipeline_state TYPE array DEFAULT [];
DEFINE FIELD IF NOT EXISTS user_prompt_preview ON pipeline_state TYPE string DEFAULT '';
DEFINE FIELD IF NOT EXISTS started_at      ON pipeline_state TYPE datetime;
DEFINE FIELD IF NOT EXISTS updated_at      ON pipeline_state TYPE datetime;
DEFINE FIELD IF NOT EXISTS finished_at     ON pipeline_state TYPE option<datetime>;
DEFINE FIELD IF NOT EXISTS error           ON pipeline_state TYPE option<string>;

DEFINE INDEX IF NOT EXISTS idx_pstate_pid    ON pipeline_state FIELDS pipeline_id UNIQUE;
DEFINE INDEX IF NOT EXISTS idx_pstate_status ON pipeline_state FIELDS status;
"""


# ---------------------------------------------------------------------------
# CRUD operations
# ---------------------------------------------------------------------------

async def insert_pipeline_run(
    pipeline_id: str,
    intent: str,
    status: str,
    total_latency_ms: int,
    steps: List[Dict[str, Any]],
    user_prompt_preview: str = "",
    final_output_preview: str = "",
) -> None:
    """Store a pipeline execution record in SurrealDB."""
    from nadirclaw.db import _db, is_connected

    if not is_connected():
        return

    try:
        record = {
            "pipeline_id": pipeline_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "intent": intent,
            "status": status,
            "total_latency_ms": total_latency_ms,
            "steps": steps,
            "user_prompt_preview": redact_secrets(user_prompt_preview[:500]),
            "final_output_preview": redact_secrets(final_output_preview[:500]),
        }

        await _db.query(
            "CREATE pipeline_run SET "
            "pipeline_id = $pipeline_id, "
            "timestamp = type::datetime($timestamp), "
            "intent = $intent, "
            "status = $status, "
            "total_latency_ms = $total_latency_ms, "
            "steps = $steps, "
            "user_prompt_preview = $user_prompt_preview, "
            "final_output_preview = $final_output_preview;",
            record,
        )
    except Exception as e:
        logger.debug("Failed to insert pipeline_run: %s", e)


async def insert_decision(
    summary: str,
    intent: str = "",
    pipeline_id: str = "",
    user_prompt_preview: str = "",
    patterns: Optional[List[str]] = None,
    trade_offs: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
) -> None:
    """Store a compressor-generated decision in SurrealDB."""
    from nadirclaw.db import _db, is_connected

    if not is_connected():
        return

    # SOC2 redaction
    summary = redact_secrets(summary)

    try:
        record = {
            "summary": summary,
            "patterns": patterns or [],
            "trade_offs": trade_offs or [],
            "tags": tags or [],
            "intent": intent,
            "pipeline_id": pipeline_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        await _db.query(
            "CREATE decision SET "
            "summary = $summary, "
            "patterns = $patterns, "
            "trade_offs = $trade_offs, "
            "tags = $tags, "
            "intent = $intent, "
            "pipeline_id = $pipeline_id, "
            "timestamp = type::datetime($timestamp);",
            record,
        )
    except Exception as e:
        logger.debug("Failed to insert decision: %s", e)


async def upsert_repo_context(
    module_path: str,
    summary: str,
    language: str = "",
    patterns: Optional[List[str]] = None,
) -> None:
    """Insert or update a module-level summary in repo_context."""
    from nadirclaw.db import _db, is_connected

    if not is_connected():
        return

    summary = redact_secrets(summary)

    try:
        record = {
            "module_path": module_path,
            "summary": summary,
            "language": language,
            "patterns": patterns or [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Upsert on module_path
        await _db.query(
            "DELETE repo_context WHERE module_path = $module_path;",
            {"module_path": module_path},
        )
        await _db.query(
            "CREATE repo_context SET "
            "module_path = $module_path, "
            "summary = $summary, "
            "language = $language, "
            "patterns = $patterns, "
            "timestamp = type::datetime($timestamp);",
            record,
        )
    except Exception as e:
        logger.debug("Failed to upsert repo_context: %s", e)


async def search_decisions(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Full-text search across decision summaries."""
    from nadirclaw.db import _db, _extract_results, is_connected

    if not is_connected():
        return []

    try:
        result = await _db.query(
            "SELECT *, search::score(1) AS relevance "
            "FROM decision "
            "WHERE summary @1@ $query "
            "ORDER BY relevance DESC "
            "LIMIT $limit;",
            {"query": query, "limit": limit},
        )
        return _extract_results(result)
    except Exception as e:
        logger.debug("Failed to search decisions: %s", e)
        return []


async def get_pipeline_run(pipeline_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a pipeline run by its ID."""
    from nadirclaw.db import _db, _extract_results, is_connected

    if not is_connected():
        return None

    try:
        result = await _db.query(
            "SELECT * FROM pipeline_run WHERE pipeline_id = $pid LIMIT 1;",
            {"pid": pipeline_id},
        )
        rows = _extract_results(result)
        return rows[0] if rows else None
    except Exception as e:
        logger.debug("Failed to get pipeline_run: %s", e)
        return None


async def get_pipeline_stats(since: Optional[str] = None) -> Dict[str, Any]:
    """Aggregated pipeline statistics."""
    from nadirclaw.db import _db, _extract_results, is_connected

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

        # Total pipeline runs
        totals_q = (
            f"SELECT count() AS total, "
            f"math::sum(total_latency_ms) AS total_latency "
            f"FROM pipeline_run WHERE {where} GROUP ALL;"
        )

        # Per-intent breakdown
        intent_q = (
            f"SELECT intent, count() AS runs, "
            f"math::mean(total_latency_ms) AS avg_latency_ms "
            f"FROM pipeline_run WHERE {where} GROUP BY intent;"
        )

        # Status distribution
        status_q = (
            f"SELECT status, count() AS count "
            f"FROM pipeline_run WHERE {where} GROUP BY status;"
        )

        totals_res = await _db.query(totals_q, params)
        intent_res = await _db.query(intent_q, params)
        status_res = await _db.query(status_q, params)

        return {
            "totals": _extract_results(totals_res),
            "by_intent": _extract_results(intent_res),
            "by_status": _extract_results(status_res),
        }
    except Exception as e:
        logger.debug("Failed to get pipeline stats: %s", e)
        return {}


# ---------------------------------------------------------------------------
# Pipeline state CRUD (crash recovery + live progress)
# ---------------------------------------------------------------------------

async def upsert_pipeline_state(
    pipeline_id: str,
    status: str,
    intent: str,
    concurrent: bool = False,
    total_steps: int = 0,
    completed_steps: int = 0,
    current_step: str = "",
    steps: Optional[List[Dict[str, Any]]] = None,
    user_prompt_preview: str = "",
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """Insert or update a pipeline state record."""
    from nadirclaw.db import _db, is_connected

    if not is_connected():
        return

    try:
        now = datetime.now(timezone.utc).isoformat()
        record = {
            "pipeline_id": pipeline_id,
            "status": status,
            "intent": intent,
            "concurrent": concurrent,
            "total_steps": total_steps,
            "completed_steps": completed_steps,
            "current_step": current_step,
            "steps": steps or [],
            "user_prompt_preview": redact_secrets(user_prompt_preview[:500]),
            "started_at": started_at or now,
            "updated_at": now,
            "finished_at": finished_at,
            "error": error,
        }

        # Upsert: delete existing then create
        await _db.query(
            "DELETE pipeline_state WHERE pipeline_id = $pipeline_id;",
            {"pipeline_id": pipeline_id},
        )
        await _db.query(
            "CREATE pipeline_state SET "
            "pipeline_id = $pipeline_id, "
            "status = $status, "
            "intent = $intent, "
            "concurrent = $concurrent, "
            "total_steps = $total_steps, "
            "completed_steps = $completed_steps, "
            "current_step = $current_step, "
            "steps = $steps, "
            "user_prompt_preview = $user_prompt_preview, "
            "started_at = type::datetime($started_at), "
            "updated_at = type::datetime($updated_at), "
            "finished_at = IF $finished_at THEN type::datetime($finished_at) ELSE NONE END, "
            "error = $error;",
            record,
        )
    except Exception as e:
        logger.debug("Failed to upsert pipeline_state: %s", e)


async def get_pipeline_state(pipeline_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a pipeline state record by pipeline_id."""
    from nadirclaw.db import _db, _extract_results, is_connected

    if not is_connected():
        return None

    try:
        result = await _db.query(
            "SELECT * FROM pipeline_state WHERE pipeline_id = $pid LIMIT 1;",
            {"pid": pipeline_id},
        )
        rows = _extract_results(result)
        return rows[0] if rows else None
    except Exception as e:
        logger.debug("Failed to get pipeline_state: %s", e)
        return None


async def mark_interrupted_pipelines() -> int:
    """Mark any running/pending pipeline states as interrupted.

    Called on startup for crash recovery. Returns count of affected records.
    """
    from nadirclaw.db import _db, _extract_results, is_connected

    if not is_connected():
        return 0

    try:
        result = await _db.query(
            "UPDATE pipeline_state SET status = 'interrupted', "
            "updated_at = type::datetime($now) "
            "WHERE status IN ['running', 'pending'];",
            {"now": datetime.now(timezone.utc).isoformat()},
        )
        rows = _extract_results(result)
        return len(rows)
    except Exception as e:
        logger.debug("Failed to mark interrupted pipelines: %s", e)
        return 0


async def cleanup_old_pipeline_states(older_than_hours: int = 72) -> int:
    """Delete old completed/error/interrupted pipeline states.

    Returns count of deleted records.
    """
    from nadirclaw.db import _db, _extract_results, is_connected

    if not is_connected():
        return 0

    try:
        cutoff = datetime.now(timezone.utc)
        from datetime import timedelta
        cutoff = (cutoff - timedelta(hours=older_than_hours)).isoformat()

        result = await _db.query(
            "DELETE pipeline_state WHERE status IN ['completed', 'error', 'interrupted'] "
            "AND updated_at < type::datetime($cutoff);",
            {"cutoff": cutoff},
        )
        rows = _extract_results(result)
        return len(rows)
    except Exception as e:
        logger.debug("Failed to cleanup old pipeline states: %s", e)
        return 0
