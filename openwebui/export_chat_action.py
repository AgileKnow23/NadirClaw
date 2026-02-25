"""
title: Export Chat History
author: NadirClaw
version: 0.1.0
description: Export the full chat conversation as an editable Markdown document. Outputs the entire history (all user and assistant messages) as a clean, formatted document you can copy, save, or edit.
required_open_webui_version: 0.4.0
"""

from datetime import datetime
from typing import Optional


class Action:
    class Valves:
        include_system_prompts: bool = False
        include_metadata: bool = True
        separator: str = "---"

    def __init__(self):
        self.valves = self.Valves()

    async def action(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__=None,
    ) -> Optional[dict]:
        """Export full chat history as a Markdown document."""
        if __event_emitter__ is None:
            return None

        await __event_emitter__(
            {"type": "status", "data": {"description": "Exporting chat...", "done": False}}
        )

        messages = body.get("messages", [])
        if not messages:
            await __event_emitter__(
                {"type": "status", "data": {"description": "No messages to export", "done": True}}
            )
            return None

        user_name = "User"
        if __user__ and isinstance(__user__, dict):
            user_name = __user__.get("name", "User")

        model = body.get("model", "unknown")
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        sep = self.valves.separator

        # --- Build the document ---
        lines = []

        # Header
        lines.append("# Chat Export")
        lines.append("")
        if self.valves.include_metadata:
            lines.append(f"- **Date:** {now}")
            lines.append(f"- **User:** {user_name}")
            lines.append(f"- **Model:** {model}")
            lines.append(f"- **Messages:** {len(messages)}")
            lines.append("")
            lines.append(sep)
            lines.append("")

        # Messages
        for i, msg in enumerate(messages):
            role = msg.get("role", "unknown")

            # Skip system messages unless configured to include them
            if role == "system" and not self.valves.include_system_prompts:
                continue

            content = _extract_content(msg.get("content", ""))

            if role == "user":
                lines.append(f"## {user_name}")
            elif role == "assistant":
                lines.append("## Assistant")
            elif role == "system":
                lines.append("## System")
            else:
                lines.append(f"## {role.title()}")

            lines.append("")
            lines.append(content)
            lines.append("")

            # Add separator between messages (but not after the last one)
            if i < len(messages) - 1:
                lines.append(sep)
                lines.append("")

        # Footer
        lines.append("")
        lines.append(sep)
        lines.append("")
        lines.append(f"*Exported from Open WebUI on {now}*")

        document = "\n".join(lines)

        await __event_emitter__(
            {"type": "status", "data": {"description": "Chat exported", "done": True}}
        )

        # Emit as a code block so the user can copy the full document easily
        output = (
            "**Full chat exported below.** Copy the contents of the block below "
            "and paste into any Markdown editor, Google Docs, Notion, or save as a `.md` file.\n\n"
            f"<details open>\n<summary>Chat Export ({len(messages)} messages) — click to collapse</summary>\n\n"
            f"{document}\n\n"
            "</details>"
        )

        await __event_emitter__(
            {"type": "message", "data": {"content": output}}
        )
        return None


def _extract_content(content) -> str:
    """Extract text from message content (handles string and list formats)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif item.get("type") == "image_url":
                    url = item.get("image_url", {}).get("url", "")
                    if url.startswith("data:"):
                        parts.append("*[embedded image]*")
                    else:
                        parts.append(f"![image]({url})")
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return str(content)
