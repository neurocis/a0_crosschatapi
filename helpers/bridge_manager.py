"""Singleton registry of active cross-chat bridge connections.

Maps context_id -> BridgeConnection so that extension hooks can detect
whether a given context is currently bridged to an external agent and
forward messages accordingly.
"""

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from helpers.print_style import PrintStyle

_PRINTER = PrintStyle(italic=True, font_color="#00CED1", padding=False)


@dataclass
class PendingEvent:
    """An event queued for delivery to the external client."""
    event_id: str
    event_type: str
    data: dict
    timestamp: float


@dataclass
class BridgeConnection:
    """Represents an active bridge between A0 and an external agent."""
    context_id: str
    agent_name: str
    ws_handler: Any  # Reference to the WsHandler instance
    ws_sid: str  # Socket.IO session id
    connected_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    # Queue for events to deliver to the external client (used by REST poll fallback)
    pending_events: list = field(default_factory=list)
    # Track whether inference is currently running
    inference_active: bool = False
    inference_message_id: Optional[str] = None
    # Accumulated inference text for streaming
    inference_buffer: str = ""

    def touch(self):
        """Update last activity timestamp."""
        self.last_activity = time.time()

    def queue_event(self, event_type: str, data: dict) -> str:
        """Queue an event for REST poll delivery. Returns event_id."""
        event_id = str(uuid.uuid4())
        self.pending_events.append(PendingEvent(
            event_id=event_id,
            event_type=event_type,
            data=data,
            timestamp=time.time(),
        ))
        # Cap pending events at 1000 to prevent unbounded growth
        if len(self.pending_events) > 1000:
            self.pending_events = self.pending_events[-500:]
        return event_id

    def drain_events(self, since_event_id: Optional[str] = None) -> list[dict]:
        """Return and remove pending events. If since_event_id is given,
        return only events after that id."""
        if since_event_id is None:
            events = [
                {"event_id": e.event_id, "type": e.event_type,
                 "data": e.data, "timestamp": e.timestamp}
                for e in self.pending_events
            ]
            self.pending_events.clear()
            return events

        # Find the cursor position
        cursor_idx = -1
        for i, e in enumerate(self.pending_events):
            if e.event_id == since_event_id:
                cursor_idx = i
                break

        if cursor_idx == -1:
            # Cursor not found, return all
            events = [
                {"event_id": e.event_id, "type": e.event_type,
                 "data": e.data, "timestamp": e.timestamp}
                for e in self.pending_events
            ]
            self.pending_events.clear()
            return events

        # Return events after cursor, remove everything up to and including cursor
        after = self.pending_events[cursor_idx + 1:]
        events = [
            {"event_id": e.event_id, "type": e.event_type,
             "data": e.data, "timestamp": e.timestamp}
            for e in after
        ]
        self.pending_events = list(after)
        return events


class BridgeManager:
    """Thread-safe singleton registry of active bridge connections."""

    _instance: Optional["BridgeManager"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._bridges: dict[str, BridgeConnection] = {}  # context_id -> BridgeConnection
        self._sid_to_context: dict[str, str] = {}  # ws_sid -> context_id
        self._rlock = threading.RLock()

    @classmethod
    def get_instance(cls) -> "BridgeManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def register(self, connection: BridgeConnection) -> None:
        """Register a new bridge connection."""
        with self._rlock:
            # Remove any existing bridge for this context
            old = self._bridges.get(connection.context_id)
            if old:
                self._sid_to_context.pop(old.ws_sid, None)

            self._bridges[connection.context_id] = connection
            self._sid_to_context[connection.ws_sid] = connection.context_id
            _PRINTER.print(
                f"[CrossChat] Bridge registered: context={connection.context_id} "
                f"agent={connection.agent_name} sid={connection.ws_sid}"
            )

    def unregister_by_sid(self, sid: str) -> Optional[BridgeConnection]:
        """Remove a bridge connection by WebSocket session id."""
        with self._rlock:
            context_id = self._sid_to_context.pop(sid, None)
            if context_id:
                conn = self._bridges.pop(context_id, None)
                if conn:
                    _PRINTER.print(
                        f"[CrossChat] Bridge unregistered: context={context_id} "
                        f"agent={conn.agent_name}"
                    )
                    return conn
        return None

    def unregister_by_context(self, context_id: str) -> Optional[BridgeConnection]:
        """Remove a bridge connection by context id."""
        with self._rlock:
            conn = self._bridges.pop(context_id, None)
            if conn:
                self._sid_to_context.pop(conn.ws_sid, None)
                _PRINTER.print(
                    f"[CrossChat] Bridge unregistered: context={context_id} "
                    f"agent={conn.agent_name}"
                )
                return conn
        return None

    def get_by_context(self, context_id: str) -> Optional[BridgeConnection]:
        """Get a bridge connection by context id."""
        with self._rlock:
            return self._bridges.get(context_id)

    def get_by_sid(self, sid: str) -> Optional[BridgeConnection]:
        """Get a bridge connection by WebSocket session id."""
        with self._rlock:
            context_id = self._sid_to_context.get(sid)
            if context_id:
                return self._bridges.get(context_id)
        return None

    def is_bridged(self, context_id: str) -> bool:
        """Check if a context has an active bridge."""
        with self._rlock:
            return context_id in self._bridges

    def list_bridges(self) -> list[dict]:
        """List all active bridges."""
        with self._rlock:
            return [
                {
                    "context_id": conn.context_id,
                    "agent_name": conn.agent_name,
                    "connected_at": conn.connected_at,
                    "last_activity": conn.last_activity,
                    "inference_active": conn.inference_active,
                }
                for conn in self._bridges.values()
            ]

    @property
    def active_count(self) -> int:
        with self._rlock:
            return len(self._bridges)
