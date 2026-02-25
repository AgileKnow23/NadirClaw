"""
title: NadirClaw Pipeline Replay
author: NadirClaw
version: 0.1.0
description: Shows the full pipeline trace for a past conversation — models used, roles, latency, cost estimate, and fallback information.
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
        """Fetch and display the latest pipeline trace for replay."""
        if __event_emitter__ is None:
            return None

        await __event_emitter__(
            {"type": "status", "data": {"description": "Fetching pipeline trace...", "done": False}}
        )

        # Try to get pipeline ID from body metadata, otherwise fetch latest
        pipeline_id = None
        messages = body.get("messages", [])
        for msg in reversed(messages):
            content = msg.get("content", "")
            if "Pipeline ID:" in content:
                # Extract pipeline ID from content
                idx = content.find("Pipeline ID: `") + len("Pipeline ID: `")
                end_idx = content.find("`", idx)
                if end_idx > idx:
                    pipeline_id = content[idx:end_idx]
                break

        try:
            if pipeline_id:
                url = f"{self.valves.nadirclaw_url}/v1/pipeline/{pipeline_id}"
            else:
                url = f"{self.valves.nadirclaw_url}/v1/pipeline/latest"

            req = urllib.request.Request(
                url,
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

        pipeline = data.get("pipeline")
        if not pipeline:
            await __event_emitter__(
                {"type": "status", "data": {"description": "No pipeline trace found", "done": True}}
            )
            return None

        md = _format_pipeline_trace(pipeline)

        await __event_emitter__(
            {"type": "status", "data": {"description": "Pipeline trace loaded", "done": True}}
        )
        await __event_emitter__(
            {"type": "message", "data": {"content": md}}
        )
        return None


def _format_pipeline_trace(pipeline: dict) -> str:
    """Format a pipeline trace as markdown."""
    lines = ["# Pipeline Trace", ""]

    pid = pipeline.get("pipeline_id", "?")
    intent = pipeline.get("intent", "?")
    status = pipeline.get("status", "?")
    total_ms = pipeline.get("total_latency_ms", 0)

    lines.append(f"**Pipeline ID:** `{pid}`")
    lines.append(f"**Intent:** {intent}")
    lines.append(f"**Status:** {status}")
    lines.append(f"**Total Time:** {total_ms}ms")
    lines.append("")

    # Steps table
    steps = pipeline.get("steps", [])
    if steps:
        lines.append("## Steps")
        lines.append("| # | Role | Model | Status | Latency | Prompt Tokens | Completion Tokens |")
        lines.append("|---|------|-------|--------|---------|---------------|-------------------|")

        total_prompt = 0
        total_completion = 0

        for i, step in enumerate(steps, 1):
            role = step.get("role", "?")
            model = step.get("model", "?")
            short_model = model.split("/")[-1] if "/" in model else model
            s_status = step.get("status", "?")
            latency = step.get("latency_ms", 0)
            pt = step.get("prompt_tokens", 0)
            ct = step.get("completion_tokens", 0)
            total_prompt += pt
            total_completion += ct

            error = step.get("error")
            status_display = s_status
            if error:
                status_display = f"err: {error[:30]}"

            lines.append(
                f"| {i} | {role} | {short_model} | {status_display} | {latency}ms | {pt:,} | {ct:,} |"
            )

        lines.append("")
        lines.append(f"**Total tokens:** {total_prompt + total_completion:,} "
                      f"(prompt: {total_prompt:,}, completion: {total_completion:,})")

    # Request/response preview (if available from DB result)
    prompt_preview = pipeline.get("user_prompt_preview", "")
    output_preview = pipeline.get("final_output_preview", "")

    if prompt_preview:
        lines.append("")
        lines.append("## Request Preview")
        lines.append(f"> {prompt_preview[:200]}")

    if output_preview:
        lines.append("")
        lines.append("## Response Preview")
        lines.append(f"> {output_preview[:200]}")

    lines.append("")
    lines.append(f"*Replayed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
    return "\n".join(lines)
