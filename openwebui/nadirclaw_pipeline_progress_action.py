"""
title: NadirClaw Pipeline Progress
author: NadirClaw
version: 0.1.0
description: Polls pipeline execution progress and shows a live status bar with step-by-step updates.
required_open_webui_version: 0.4.0
"""

import asyncio
import json
import urllib.request
from typing import Optional


class Action:
    class Valves:
        nadirclaw_url: str = "http://localhost:8856"
        auth_token: str = "local"
        poll_interval_ms: int = 500
        max_polls: int = 120  # 60 seconds at 500ms

    def __init__(self):
        self.valves = self.Valves()

    async def action(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__=None,
    ) -> Optional[dict]:
        """Poll pipeline progress and emit status updates."""
        if __event_emitter__ is None:
            return None

        # Extract pipeline ID from most recent assistant message metadata
        pipeline_id = None
        messages = body.get("messages", [])
        for msg in reversed(messages):
            content = msg.get("content", "")
            if "Pipeline ID:" in content:
                idx = content.find("Pipeline ID: `") + len("Pipeline ID: `")
                end_idx = content.find("`", idx)
                if end_idx > idx:
                    pipeline_id = content[idx:end_idx]
                break

        if not pipeline_id:
            await __event_emitter__(
                {"type": "status", "data": {"description": "No pipeline ID found", "done": True}}
            )
            return None

        interval = self.valves.poll_interval_ms / 1000.0
        url = f"{self.valves.nadirclaw_url}/v1/pipeline/{pipeline_id}/progress"

        for _ in range(self.valves.max_polls):
            try:
                req = urllib.request.Request(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.valves.auth_token}",
                        "Accept": "application/json",
                    },
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                await __event_emitter__(
                    {"type": "status", "data": {"description": f"Error: {e}", "done": True}}
                )
                return None

            progress = data.get("progress", {})
            status = progress.get("status", "unknown")
            percent = progress.get("percent", 0)
            current = progress.get("current_step", "")
            completed = progress.get("completed_steps", 0)
            total = progress.get("total_steps", 0)

            desc = f"Pipeline {percent}% — {completed}/{total} steps"
            if current:
                desc += f" (running: {current})"

            done = status in ("completed", "ok", "error", "partial", "interrupted")

            await __event_emitter__(
                {"type": "status", "data": {"description": desc, "done": done}}
            )

            if done:
                break

            await asyncio.sleep(interval)

        return None
