"""
title: NadirClaw Pipeline Progress Filter
author: NadirClaw
version: 0.1.0
description: Appends a collapsible section to assistant responses showing pipeline step details (model, role, status, latency).
required_open_webui_version: 0.4.0
"""

import json
import urllib.request
from typing import Optional


class Filter:
    class Valves:
        nadirclaw_url: str = "http://localhost:8856"
        auth_token: str = "local"
        enabled: bool = True

    def __init__(self):
        self.valves = self.Valves()

    async def inlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        """Pass-through on inlet."""
        return body

    async def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        """Append pipeline step details to the last assistant message."""
        if not self.valves.enabled:
            return body

        messages = body.get("messages", [])
        if not messages:
            return body

        last_assistant = None
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                last_assistant = msg
                break

        if not last_assistant:
            return body

        # Fetch latest pipeline trace
        try:
            req = urllib.request.Request(
                f"{self.valves.nadirclaw_url}/v1/pipeline/latest",
                headers={
                    "Authorization": f"Bearer {self.valves.auth_token}",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return body

        pipeline = data.get("pipeline")
        if not pipeline:
            return body

        steps = pipeline.get("steps", [])
        if not steps:
            return body

        # Build collapsible details section
        lines = [
            "\n\n<details>",
            f"<summary>Pipeline: {pipeline.get('intent', '?')} "
            f"({pipeline.get('status', '?')}, {pipeline.get('total_latency_ms', 0)}ms)</summary>",
            "",
            "| Step | Model | Status | Latency | Tokens |",
            "|------|-------|--------|---------|--------|",
        ]

        for step in steps:
            role = step.get("role", "?")
            model = step.get("model", "?")
            # Shorten model names for display
            short_model = model.split("/")[-1] if "/" in model else model
            status = step.get("status", "?")
            latency = step.get("latency_ms", 0)
            tokens = step.get("prompt_tokens", 0) + step.get("completion_tokens", 0)
            status_icon = "ok" if status == "ok" else "err"
            lines.append(f"| {role} | {short_model} | {status_icon} | {latency}ms | {tokens} |")

        lines.append("")
        lines.append(f"Pipeline ID: `{pipeline.get('pipeline_id', '?')}`")
        lines.append("</details>")

        content = last_assistant.get("content", "")
        last_assistant["content"] = content + "\n".join(lines)

        return body
