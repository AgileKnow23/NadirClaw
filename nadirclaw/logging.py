"""Request log writer.

Writes JSON-line entries to ``settings.LOG_DIR/requests.jsonl`` and mirrors
each entry into SurrealDB when enabled. Extracted from ``server.py`` during
the A4 refactor (BACKLOG P2).

The module name shadows the stdlib ``logging`` package only when accessed
as ``nadirclaw.logging``; bare ``import logging`` continues to resolve to
the stdlib via Python's absolute-import rules.
"""

from __future__ import annotations

import asyncio
import json
import logging as _stdlib_logging
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict

from nadirclaw.settings import settings

_logger = _stdlib_logging.getLogger("nadirclaw")
_log_lock = Lock()


def log_request(entry: Dict[str, Any]) -> None:
    """Append a JSON line to the request log and print a summary."""
    log_dir = settings.LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    request_log = log_dir / "requests.jsonl"

    entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    line = json.dumps(entry, default=str) + "\n"
    with _log_lock:
        with open(request_log, "a") as f:
            f.write(line)

    if settings.SURREALDB_ENABLED:
        try:
            from nadirclaw.db import insert_request
            asyncio.get_event_loop().create_task(insert_request(entry))
        except Exception:
            pass

    tier = entry.get("tier", "?")
    model = entry.get("selected_model", "?")
    conf = entry.get("confidence", 0)
    score = entry.get("complexity_score", 0)
    prompt_preview = entry.get("prompt", "")[:80]
    latency = entry.get("classifier_latency_ms", "?")
    total = entry.get("total_latency_ms", "?")
    _logger.info(
        '%-8s model=%-35s conf=%.3f score=%.2f lat=%sms total=%sms  "%s"',
        tier, model, conf, score, latency, total, prompt_preview,
    )
