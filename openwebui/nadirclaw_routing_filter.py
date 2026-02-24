"""
title: NadirClaw Routing Filter
author: NadirClaw
version: 0.1.0
description: Appends routing metadata footer to assistant responses showing which model NadirClaw selected, confidence, and latency.
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
        """Pass-through on inlet — no modification needed."""
        return body

    async def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        """Append routing metadata to the last assistant message."""
        if not self.valves.enabled:
            return body

        messages = body.get("messages", [])
        if not messages:
            return body

        # Find the last assistant message
        last_assistant = None
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                last_assistant = msg
                break

        if not last_assistant:
            return body

        # Fetch latest event from dashboard
        try:
            req = urllib.request.Request(
                f"{self.valves.nadirclaw_url}/v1/dashboard?limit=10",
                headers={
                    "Authorization": f"Bearer {self.valves.auth_token}",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return body

        recent = data.get("recent_events", [])
        if not recent:
            return body

        # Use the most recent event
        event = recent[-1]
        model = event.get("selected_model", "unknown")
        tier = event.get("tier", "?")
        confidence = event.get("confidence")
        latency = event.get("total_latency_ms")

        conf_str = f"{confidence:.2f}" if confidence is not None else "?"
        lat_str = f"{latency}ms" if latency is not None else "?"

        footer = f"\n\n---\n*[NadirClaw: routed to {model} ({tier}, confidence: {conf_str}, {lat_str})]*"

        content = last_assistant.get("content", "")
        last_assistant["content"] = content + footer

        return body
