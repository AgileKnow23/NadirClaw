"""Markdown-based knowledge file system for continuous learning."""

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

KNOWLEDGE_DIR = Path.home() / ".nadirclaw" / "knowledge"

_SEED_ROUTING_RULES = """\
# NadirClaw Routing Rules

## Tier Thresholds
- Confidence threshold: 0.06 (from settings)
- Agentic detection threshold: 0.35

## Learned Patterns
<!-- Auto-updated by NadirClaw learning cycle -->

## Manual Overrides
<!-- Add your own rules here — they won't be overwritten -->
"""

_SEED_MODEL_PROFILES = """\
# NadirClaw Model Profiles

## Model Stats
<!-- Auto-updated by NadirClaw learning cycle -->

## Notes
<!-- Add your own observations here — they won't be overwritten -->
"""

_SEED_SESSION_LOG = """\
# NadirClaw Session Log

Learning cycle summaries are appended below.

---
"""

_SEED_FILES = {
    "routing-rules.md": _SEED_ROUTING_RULES,
    "model-profiles.md": _SEED_MODEL_PROFILES,
    "session-log.md": _SEED_SESSION_LOG,
}


def seed_knowledge():
    """Create initial markdown files with template structure if they don't exist."""
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    for filename, content in _SEED_FILES.items():
        filepath = KNOWLEDGE_DIR / filename
        if not filepath.exists():
            filepath.write_text(content, encoding="utf-8")


def get_all_knowledge() -> Dict[str, str]:
    """Read all .md files from the knowledge dir, return as dict."""
    seed_knowledge()
    result = {}
    for filepath in sorted(KNOWLEDGE_DIR.glob("*.md")):
        result[filepath.name] = filepath.read_text(encoding="utf-8")
    return result


def learn_from_logs(log_path: Path) -> Dict[str, Any]:
    """Analyze recent log entries and update knowledge files.

    1. Load last 200 log entries
    2. Compute: fallback rate per model, avg confidence per tier,
       agentic override frequency, error rate
    3. Generate markdown summary
    4. Append to session-log.md with timestamp
    5. Update model-profiles.md with latest stats
    6. Return summary dict
    """
    seed_knowledge()

    # Load entries
    entries = _load_recent_entries(log_path, limit=200)
    if not entries:
        return {"status": "no_data", "message": "No log entries found."}

    # Compute stats
    stats = _compute_stats(entries)

    # Generate markdown summary
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    summary_md = _format_session_summary(stats, now)

    # Append to session-log.md
    session_log = KNOWLEDGE_DIR / "session-log.md"
    with open(session_log, "a", encoding="utf-8") as f:
        f.write(f"\n{summary_md}\n")

    # Update model-profiles.md
    _update_model_profiles(stats)

    # Update routing-rules.md learned patterns
    _update_routing_rules(stats)

    return {
        "status": "ok",
        "entries_analyzed": len(entries),
        "timestamp": now,
        "stats": stats,
    }


def _load_recent_entries(log_path: Path, limit: int = 200) -> List[Dict[str, Any]]:
    """Load the last N entries from a JSONL log file."""
    if not log_path.exists():
        return []
    entries = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries[-limit:]


def _compute_stats(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute learning stats from log entries."""
    total = len(entries)

    # Per-model stats
    model_stats: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"requests": 0, "fallbacks": 0, "errors": 0, "total_tokens": 0, "latencies": []}
    )
    for e in entries:
        model = e.get("selected_model", "unknown")
        model_stats[model]["requests"] += 1
        if e.get("fallback_used"):
            model_stats[model]["fallbacks"] += 1
        if e.get("status") == "error":
            model_stats[model]["errors"] += 1
        pt = _safe_int(e.get("prompt_tokens", 0))
        ct = _safe_int(e.get("completion_tokens", 0))
        model_stats[model]["total_tokens"] += pt + ct
        lat = e.get("total_latency_ms")
        if lat is not None:
            try:
                model_stats[model]["latencies"].append(float(lat))
            except (TypeError, ValueError):
                pass

    # Per-tier confidence
    tier_confidence: Dict[str, List[float]] = defaultdict(list)
    for e in entries:
        tier = e.get("tier")
        conf = e.get("confidence")
        if tier and conf is not None:
            try:
                tier_confidence[tier].append(float(conf))
            except (TypeError, ValueError):
                pass

    # Agentic/reasoning counts
    agentic_count = 0
    reasoning_count = 0
    for e in entries:
        rm = e.get("routing_modifiers", {})
        if isinstance(rm, dict):
            if rm.get("agentic", {}).get("is_agentic"):
                agentic_count += 1
            if rm.get("reasoning", {}).get("is_reasoning"):
                reasoning_count += 1

    # Error rate
    error_count = sum(1 for e in entries if e.get("status") == "error")
    fallback_count = sum(1 for e in entries if e.get("fallback_used"))

    # Build model summary (convert latencies to avg)
    model_summary = {}
    for model, s in model_stats.items():
        lats = s["latencies"]
        model_summary[model] = {
            "requests": s["requests"],
            "fallbacks": s["fallbacks"],
            "fallback_rate": round(s["fallbacks"] / s["requests"] * 100, 1) if s["requests"] else 0,
            "errors": s["errors"],
            "error_rate": round(s["errors"] / s["requests"] * 100, 1) if s["requests"] else 0,
            "total_tokens": s["total_tokens"],
            "avg_latency_ms": round(sum(lats) / len(lats), 1) if lats else None,
        }

    # Build tier confidence summary
    tier_summary = {}
    for tier, confs in tier_confidence.items():
        tier_summary[tier] = {
            "count": len(confs),
            "avg_confidence": round(sum(confs) / len(confs), 4) if confs else None,
        }

    return {
        "total_entries": total,
        "model_summary": model_summary,
        "tier_summary": tier_summary,
        "agentic_count": agentic_count,
        "reasoning_count": reasoning_count,
        "error_count": error_count,
        "error_rate": round(error_count / total * 100, 1) if total else 0,
        "fallback_count": fallback_count,
        "fallback_rate": round(fallback_count / total * 100, 1) if total else 0,
    }


def _format_session_summary(stats: Dict[str, Any], timestamp: str) -> str:
    """Format stats as a markdown session summary block."""
    lines = [
        f"## Learning Cycle — {timestamp}",
        "",
        f"**Entries analyzed:** {stats['total_entries']}",
        f"**Error rate:** {stats['error_rate']}%",
        f"**Fallback rate:** {stats['fallback_rate']}%",
        f"**Agentic requests:** {stats['agentic_count']}",
        f"**Reasoning requests:** {stats['reasoning_count']}",
        "",
    ]

    # Model breakdown
    if stats["model_summary"]:
        lines.append("### Model Breakdown")
        lines.append("")
        lines.append("| Model | Requests | Fallback % | Error % | Avg Latency | Tokens |")
        lines.append("|-------|----------|------------|---------|-------------|--------|")
        for model, s in sorted(stats["model_summary"].items()):
            lat = f"{s['avg_latency_ms']:.0f}ms" if s["avg_latency_ms"] else "N/A"
            lines.append(
                f"| {model} | {s['requests']} | {s['fallback_rate']}% | "
                f"{s['error_rate']}% | {lat} | {s['total_tokens']} |"
            )
        lines.append("")

    # Tier confidence
    if stats["tier_summary"]:
        lines.append("### Tier Confidence")
        lines.append("")
        for tier, s in sorted(stats["tier_summary"].items()):
            conf = f"{s['avg_confidence']:.4f}" if s["avg_confidence"] is not None else "N/A"
            lines.append(f"- **{tier}**: {s['count']} requests, avg confidence {conf}")
        lines.append("")

    lines.append("---")
    return "\n".join(lines)


def _update_model_profiles(stats: Dict[str, Any]):
    """Update model-profiles.md with latest stats."""
    filepath = KNOWLEDGE_DIR / "model-profiles.md"

    # Build new stats section
    lines = [
        "# NadirClaw Model Profiles",
        "",
        "## Model Stats",
        f"<!-- Last updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} -->",
        "",
    ]

    for model, s in sorted(stats["model_summary"].items()):
        lat = f"{s['avg_latency_ms']:.0f}ms" if s["avg_latency_ms"] else "N/A"
        lines.append(f"### {model}")
        lines.append(f"- Requests: {s['requests']}")
        lines.append(f"- Fallback rate: {s['fallback_rate']}%")
        lines.append(f"- Error rate: {s['error_rate']}%")
        lines.append(f"- Avg latency: {lat}")
        lines.append(f"- Total tokens: {s['total_tokens']}")
        lines.append("")

    # Preserve manual notes section
    existing = ""
    if filepath.exists():
        existing = filepath.read_text(encoding="utf-8")

    notes_marker = "## Notes"
    if notes_marker in existing:
        notes_section = existing[existing.index(notes_marker):]
        lines.append(notes_section)
    else:
        lines.append("## Notes")
        lines.append("<!-- Add your own observations here — they won't be overwritten -->")
        lines.append("")

    filepath.write_text("\n".join(lines), encoding="utf-8")


def _update_routing_rules(stats: Dict[str, Any]):
    """Update the learned patterns section of routing-rules.md."""
    filepath = KNOWLEDGE_DIR / "routing-rules.md"
    if not filepath.exists():
        seed_knowledge()

    existing = filepath.read_text(encoding="utf-8")

    # Build new learned patterns
    pattern_lines = [
        "## Learned Patterns",
        f"<!-- Auto-updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} -->",
        "",
    ]

    # High fallback rate warnings
    for model, s in stats["model_summary"].items():
        if s["fallback_rate"] > 50:
            pattern_lines.append(
                f"- **Warning**: {model} has {s['fallback_rate']}% fallback rate "
                f"({s['fallbacks']}/{s['requests']} requests)"
            )

    # Agentic upgrade pattern
    if stats["agentic_count"] > 0:
        pct = round(stats["agentic_count"] / stats["total_entries"] * 100, 1)
        pattern_lines.append(f"- Agentic requests: {stats['agentic_count']} ({pct}% of traffic)")

    # Reasoning pattern
    if stats["reasoning_count"] > 0:
        pct = round(stats["reasoning_count"] / stats["total_entries"] * 100, 1)
        pattern_lines.append(f"- Reasoning requests: {stats['reasoning_count']} ({pct}% of traffic)")

    # Tier confidence insights
    for tier, s in stats["tier_summary"].items():
        if s["avg_confidence"] is not None:
            pattern_lines.append(f"- Tier '{tier}' avg confidence: {s['avg_confidence']:.4f}")

    pattern_lines.append("")

    # Replace learned patterns section, preserve everything else
    start_marker = "## Learned Patterns"
    end_marker = "## Manual Overrides"

    if start_marker in existing and end_marker in existing:
        before = existing[:existing.index(start_marker)]
        after = existing[existing.index(end_marker):]
        new_content = before + "\n".join(pattern_lines) + "\n" + after
    else:
        new_content = existing + "\n" + "\n".join(pattern_lines)

    filepath.write_text(new_content, encoding="utf-8")


def _safe_int(val) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0
