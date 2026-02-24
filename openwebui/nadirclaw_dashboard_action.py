"""
title: NadirClaw Dashboard
author: NadirClaw
version: 0.1.0
description: Shows a NadirClaw routing dashboard with model utilization, latency stats, tier distribution, and recent routing decisions.
required_open_webui_version: 0.4.0
"""

import json
import urllib.request
from datetime import datetime
from typing import Optional


class Action:
    class Valves:
        nadirclaw_url: str = "http://localhost:8856"
        auth_token: str = "local"

    def __init__(self):
        self.valves = self.Valves()

    async def action(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__=None,
    ) -> Optional[dict]:
        """Fetch and display NadirClaw dashboard metrics."""
        if __event_emitter__ is None:
            return None

        await __event_emitter__(
            {"type": "status", "data": {"description": "Fetching NadirClaw dashboard...", "done": False}}
        )

        try:
            req = urllib.request.Request(
                f"{self.valves.nadirclaw_url}/v1/dashboard?limit=200",
                headers={
                    "Authorization": f"Bearer {self.valves.auth_token}",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            await __event_emitter__(
                {"type": "status", "data": {"description": f"Error: {e}", "done": True}}
            )
            return None

        report = data.get("report", {})
        models_config = data.get("models", {})
        recent_events = data.get("recent_events", [])

        md = _format_dashboard(report, models_config, recent_events)

        await __event_emitter__(
            {"type": "status", "data": {"description": "Dashboard loaded", "done": True}}
        )
        await __event_emitter__(
            {"type": "message", "data": {"content": md}}
        )

        return None


def _format_dashboard(report: dict, models_config: dict, recent_events: list) -> str:
    """Format dashboard data as markdown."""
    lines = ["# NadirClaw Dashboard", ""]

    # Model configuration
    lines.append("## Active Models")
    lines.append(f"- **Simple:** {models_config.get('simple', 'N/A')}")
    lines.append(f"- **Complex:** {models_config.get('complex', 'N/A')}")
    lines.append(f"- **Reasoning:** {models_config.get('reasoning', 'N/A')}")
    lines.append("")

    total = report.get("total_requests", 0)
    lines.append(f"## Overview ({total} total requests)")
    lines.append("")

    # Tier distribution
    tiers = report.get("tier_distribution", {})
    if tiers:
        lines.append("### Tier Distribution")
        lines.append("| Tier | Count | % |")
        lines.append("|------|-------|---|")
        for tier, info in sorted(tiers.items()):
            lines.append(f"| {tier} | {info['count']} | {info['percentage']}% |")
        lines.append("")

    # Model usage
    model_usage = report.get("model_usage", {})
    if model_usage:
        lines.append("### Model Usage")
        lines.append("| Model | Requests | Tokens |")
        lines.append("|-------|----------|--------|")
        for model, info in sorted(model_usage.items(), key=lambda x: x[1]["requests"], reverse=True):
            lines.append(f"| {model} | {info['requests']} | {info['total_tokens']:,} |")
        lines.append("")

    # Latency stats
    latency = report.get("latency", {})
    if latency:
        lines.append("### Latency")
        lines.append("| Metric | Avg | p50 | p95 |")
        lines.append("|--------|-----|-----|-----|")
        for key in ("classifier", "total"):
            stats = latency.get(key)
            if stats:
                lines.append(f"| {key} | {stats['avg']:.0f}ms | {stats['p50']:.0f}ms | {stats['p95']:.0f}ms |")
        lines.append("")

    # Token usage
    tokens = report.get("tokens", {})
    if tokens and tokens.get("total_tokens", 0) > 0:
        lines.append("### Token Usage")
        lines.append(f"- Prompt: {tokens['prompt_tokens']:,}")
        lines.append(f"- Completion: {tokens['completion_tokens']:,}")
        lines.append(f"- **Total: {tokens['total_tokens']:,}**")
        lines.append("")

    # Fallback/errors
    fb = report.get("fallback_count", 0)
    err = report.get("error_count", 0)
    if fb or err:
        lines.append("### Issues")
        if fb:
            lines.append(f"- Fallbacks: {fb}")
        if err:
            lines.append(f"- Errors: {err}")
        lines.append("")

    # Recent events
    if recent_events:
        lines.append("### Recent Routing Decisions (last 10)")
        lines.append("| Model | Tier | Confidence | Latency | Tokens |")
        lines.append("|-------|------|------------|---------|--------|")
        for event in recent_events[-10:]:
            if event.get("event_type") == "heartbeat":
                continue
            model = event.get("selected_model", "?")
            tier = event.get("tier", "?")
            conf = event.get("confidence")
            conf_str = f"{conf:.2f}" if conf is not None else "?"
            lat = event.get("total_latency_ms", "?")
            lat_str = f"{lat}ms" if lat != "?" else "?"
            pt = event.get("prompt_tokens", 0)
            ct = event.get("completion_tokens", 0)
            preview = event.get("prompt_preview", "")[:40]
            lines.append(f"| {model} | {tier} | {conf_str} | {lat_str} | {pt + ct} |")
        lines.append("")

    lines.append(f"*Generated at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
    return "\n".join(lines)
