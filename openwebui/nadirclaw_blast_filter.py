"""
title: NadirClaw BLAST Optimizer Filter
author: NadirClaw
version: 0.2.0
description: Shows BLAST prompt analysis and execution plan as a collapsible section, including which models and agents will be engaged.
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
        """Preview BLAST optimization on the incoming prompt."""
        if not self.valves.enabled:
            return body

        messages = body.get("messages", [])
        if not messages:
            return body

        # Get the last user message
        last_user = None
        for msg in reversed(messages):
            if msg.get("role") == "user":
                last_user = msg
                break

        if not last_user:
            return body

        prompt = last_user.get("content", "")
        if not prompt or len(prompt) < 10:
            return body

        # Call BLAST preview endpoint
        try:
            payload = json.dumps({"prompt": prompt}).encode("utf-8")
            req = urllib.request.Request(
                f"{self.valves.nadirclaw_url}/v1/blast",
                data=payload,
                headers={
                    "Authorization": f"Bearer {self.valves.auth_token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return body

        sections = data.get("sections", {})
        if not sections:
            return body

        intent = data.get("intent", "unknown")
        latency = data.get("latency_ms", 0)
        used_llm = data.get("used_llm", False)
        plan = data.get("execution_plan", {})

        # Build collapsible BLAST + execution plan block
        lines = [
            "\n\n<details>",
            f"<summary>BLAST Analysis ({intent}, {latency}ms"
            f"{', LLM' if used_llm else ', template'}"
            f", {plan.get('total_agents', '?')} agents)</summary>",
            "",
        ]

        # BLAST sections
        for label in ("blueprint", "link", "architect", "style", "trigger"):
            content = sections.get(label, "N/A")
            header = label.capitalize()
            lines.append(f"**{header}:** {content}")
            lines.append("")

        # Execution plan
        steps = plan.get("steps", [])
        if steps:
            lines.append("---")
            lines.append("")
            lines.append("**Execution Plan:**")
            lines.append("")
            lines.append("| Step | Agent | Model | Action |")
            lines.append("|------|-------|-------|--------|")
            for step in steps:
                step_num = step.get("step", "?")
                agent = step.get("agent", "?")
                model = step.get("model_short", step.get("model", "?"))
                action = step.get("action", "")
                # Truncate action for table readability
                if len(action) > 60:
                    action = action[:57] + "..."
                lines.append(f"| {step_num} | {agent} | {model} | {action} |")
            lines.append("")

        lines.append("</details>")

        # Store BLAST block to attach after the response
        body["_blast_block"] = "\n".join(lines)

        return body

    async def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        """Append stored BLAST block to the assistant response."""
        blast_block = body.pop("_blast_block", None)
        if not blast_block:
            return body

        messages = body.get("messages", [])
        if not messages:
            return body

        # Find the last assistant message
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                msg["content"] = msg.get("content", "") + blast_block
                break

        return body
