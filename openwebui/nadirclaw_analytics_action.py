"""
title: NadirClaw Analytics Dashboard
author: NadirClaw
version: 0.1.0
description: Full analytics dashboard showing model usage, token costs, latency, tier distribution, strategy breakdown, and pipeline health metrics.
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
        since: str = "30d"

    def __init__(self):
        self.valves = self.Valves()

    async def action(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__=None,
    ) -> Optional[dict]:
        """Fetch and display comprehensive NadirClaw analytics."""
        if __event_emitter__ is None:
            return None

        await __event_emitter__(
            {"type": "status", "data": {"description": "Fetching analytics...", "done": False}}
        )

        try:
            req = urllib.request.Request(
                f"{self.valves.nadirclaw_url}/v1/analytics?since={self.valves.since}",
                headers={
                    "Authorization": f"Bearer {self.valves.auth_token}",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            await __event_emitter__(
                {"type": "status", "data": {"description": f"Error: {e}", "done": True}}
            )
            return None

        analytics = data.get("analytics", {})
        since = data.get("since", self.valves.since)
        md = _format_analytics(analytics, since)

        await __event_emitter__(
            {"type": "status", "data": {"description": "Analytics loaded", "done": True}}
        )
        await __event_emitter__(
            {"type": "message", "data": {"content": md}}
        )
        return None


def _format_analytics(analytics: dict, since: str) -> str:
    """Format analytics data as markdown."""
    lines = [f"# NadirClaw Analytics (last {since})", ""]

    # --- Monthly Summary ---
    totals_list = analytics.get("totals", [])
    totals = totals_list[0] if totals_list else {}
    total_reqs = totals.get("total_requests", 0)
    total_tokens = totals.get("total_tokens", 0)
    total_cost = totals.get("total_cost_usd") or 0

    status_list = analytics.get("by_status", [])
    ok_count = sum(s.get("count", 0) for s in status_list if s.get("status") == "ok")
    err_count = sum(s.get("count", 0) for s in status_list if s.get("status") == "error")
    success_rate = (ok_count / total_reqs * 100) if total_reqs > 0 else 0

    lines.append("## Summary")
    lines.append(f"- **Total requests:** {total_reqs:,}")
    lines.append(f"- **Total tokens:** {total_tokens:,}")
    lines.append(f"- **Estimated cost:** ${total_cost:.4f}")
    lines.append(f"- **Success rate:** {success_rate:.1f}%")
    lines.append("")

    # --- Special counts ---
    special_list = analytics.get("special", [])
    if special_list:
        special = special_list[0]
        agentic = special.get("agentic_count", 0)
        reasoning = special.get("reasoning_count", 0)
        if agentic or reasoning:
            lines.append(f"- Agentic requests: {agentic}")
            lines.append(f"- Reasoning requests: {reasoning}")
            lines.append("")

    # --- Token Usage by Model ---
    by_model = analytics.get("by_model", [])
    if by_model:
        lines.append("## Token Usage by Model")
        lines.append("| Model | Requests | Prompt | Completion | Total | Cost | Avg Latency |")
        lines.append("|-------|----------|--------|------------|-------|------|-------------|")
        for m in sorted(by_model, key=lambda x: x.get("requests", 0), reverse=True):
            model = m.get("selected_model", "?")
            short = model.split("/")[-1] if "/" in model else model
            reqs = m.get("requests", 0)
            pt = m.get("prompt_tokens", 0)
            ct = m.get("completion_tokens", 0)
            tt = m.get("total_tokens", 0)
            cost = m.get("cost_usd") or 0
            avg_lat = m.get("avg_latency_ms") or 0
            lines.append(
                f"| {short} | {reqs} | {pt:,} | {ct:,} | {tt:,} | ${cost:.4f} | {avg_lat:.0f}ms |"
            )
        lines.append("")

    # --- Tier Distribution ---
    by_tier = analytics.get("by_tier", [])
    if by_tier:
        lines.append("## Tier Distribution")
        lines.append("| Tier | Requests | % |")
        lines.append("|------|----------|---|")
        for t in by_tier:
            tier = t.get("tier", "?")
            reqs = t.get("requests", 0)
            pct = (reqs / total_reqs * 100) if total_reqs > 0 else 0
            lines.append(f"| {tier} | {reqs} | {pct:.1f}% |")
        lines.append("")

    # --- Strategy Breakdown ---
    by_strategy = analytics.get("by_strategy", [])
    if by_strategy:
        lines.append("## Routing Strategy")
        lines.append("| Strategy | Requests |")
        lines.append("|----------|----------|")
        for s in sorted(by_strategy, key=lambda x: x.get("requests", 0), reverse=True):
            lines.append(f"| {s.get('strategy', '?')} | {s.get('requests', 0)} |")
        lines.append("")

    # --- Pipeline Health ---
    pipeline = analytics.get("pipeline", {})
    if pipeline:
        ptotals = pipeline.get("totals", [])
        pstatus = pipeline.get("by_status", [])
        pintent = pipeline.get("by_intent", [])

        if ptotals or pstatus or pintent:
            lines.append("## Pipeline Health")

            if ptotals:
                pt = ptotals[0] if ptotals else {}
                lines.append(f"- Total pipeline runs: {pt.get('total', 0)}")

            if pstatus:
                for ps in pstatus:
                    lines.append(f"- {ps.get('status', '?')}: {ps.get('count', 0)}")

            if pintent:
                lines.append("")
                lines.append("| Intent | Runs | Avg Latency |")
                lines.append("|--------|------|-------------|")
                for pi in pintent:
                    lines.append(
                        f"| {pi.get('intent', '?')} | {pi.get('runs', 0)} | "
                        f"{pi.get('avg_latency_ms', 0):.0f}ms |"
                    )
            lines.append("")

    # --- Optimization Suggestions ---
    suggestions = _generate_suggestions(analytics, total_reqs)
    if suggestions:
        lines.append("## Optimization Suggestions")
        for s in suggestions:
            lines.append(f"- {s}")
        lines.append("")

    lines.append(f"*Generated at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
    return "\n".join(lines)


def _generate_suggestions(analytics: dict, total_reqs: int) -> list:
    """Auto-generate optimization suggestions based on analytics."""
    suggestions = []

    by_model = analytics.get("by_model", [])
    for m in by_model:
        avg_lat = m.get("avg_latency_ms") or 0
        if avg_lat > 10000:
            model = m.get("selected_model", "?")
            suggestions.append(
                f"High latency alert: {model} averaging {avg_lat:.0f}ms. "
                f"Consider switching to a faster model."
            )

    totals_list = analytics.get("totals", [])
    if totals_list:
        cost = (totals_list[0].get("total_cost_usd") or 0)
        if cost > 10:
            suggestions.append(
                f"Cost alert: ${cost:.2f} in this period. "
                f"Review cloud model usage for cost optimization."
            )

    by_status = analytics.get("by_status", [])
    err_count = sum(s.get("count", 0) for s in by_status if s.get("status") == "error")
    if total_reqs > 0 and err_count / total_reqs > 0.05:
        suggestions.append(
            f"Error rate is {err_count / total_reqs * 100:.1f}%. "
            f"Investigate failing requests."
        )

    return suggestions
