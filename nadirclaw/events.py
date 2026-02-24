"""In-memory async event bus for real-time SSE streaming."""

import asyncio
import time
from collections import deque
from typing import Any, Dict


class EventBus:
    """In-memory async event bus for real-time SSE streaming."""

    def __init__(self, max_history: int = 200):
        self._subscribers: list[asyncio.Queue] = []
        self._history: deque = deque(maxlen=max_history)

    async def publish(self, event: Dict[str, Any]):
        event.setdefault("event_time", time.time())
        self._history.append(event)
        dead = []
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._subscribers.remove(q)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        # Send recent history
        for event in self._history:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                break
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        if q in self._subscribers:
            self._subscribers.remove(q)

    def get_history(self, limit: int = 50) -> list:
        return list(self._history)[-limit:]


event_bus = EventBus()
