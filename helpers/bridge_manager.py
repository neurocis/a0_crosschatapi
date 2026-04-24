"""Singleton registry of active cross-chat bridge connections.

Maps context_id -> BridgeConnection so that extension hooks can detect
whether a given context is currently bridged to an external agent and
forward messages accordingly.

Reconnect resilience:
  - On WS disconnect the bridge is marked *disconnected* (not removed).
  - A grace period (default 60s) allows the client to reconnect and
    rebind with the same context_id.
  - After the grace period expires the bridge is torn down for real.
"""

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from helpers.print_style import PrintStyle

_PRINTER = PrintStyle(italic=True, font_color="#00CED1", padding=False)

# How long (seconds) to keep a disconnected bridge alive before teardown.
RECONNECT_GRACE_SECONDS = 60.0


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
    # Reconnect state
    disconnected: bool = False
    disconnected_at: Optional[float] = None

    def touch(self):
        """Update last activity timestamp."""
        self.last_activity = time.time()

    def mark_disconnected(self):
        """Mark as disconnected (but keep alive for grace period)."""
        self.disconnected = True
        self.disconnected_at = time.time()

    def mark_reconnected(self, ws_handler: Any, ws_sid: str):
        """Rebind to a new WebSocket session after reconnect."""
        self.ws_handler = ws_handler
        self.ws_sid = ws_sid
        self.disconnected = False
        self.disconnected_at = None
        self.last_activity = time.time()

    @property
    def is_connected(self) -> bool:
        return not self.disconnected

    @property
    def grace_expired(self) -> bool:
        if not self.disconnected or self.disconnected_at is None:
            return False
        return (time.time() - self.disconnected_at) > RECONNECT_GRACE_SECONDS

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
        self._reaper_started = False

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
        self._ensure_reaper()

    def reconnect(self, context_id: str, ws_handler: Any, ws_sid: str) -> Optional[BridgeConnection]:
        """Rebind a disconnected bridge to a new WebSocket session.

        Returns the reconnected BridgeConnection, or None if no bridge
        exists for that context_id.
        """
        with self._rlock:
            conn = self._bridges.get(context_id)
            if conn is None:
                return None

            # Remove old sid mapping
            self._sid_to_context.pop(conn.ws_sid, None)

            # Rebind
            conn.mark_reconnected(ws_handler, ws_sid)
            self._sid_to_context[ws_sid] = context_id

            _PRINTER.print(
                f"[CrossChat] Bridge reconnected: context={context_id} "
                f"agent={conn.agent_name} new_sid={ws_sid}"
            )
            return conn

    def mark_disconnected(self, sid: str) -> Optional[BridgeConnection]:
        """Mark a bridge as disconnected (keep alive for grace period).

        Returns the BridgeConnection if found, None otherwise.
        """
        with self._rlock:
            context_id = self._sid_to_context.pop(sid, None)
            if context_id is None:
                return None

            conn = self._bridges.get(context_id)
            if conn is None:
                return None

            conn.mark_disconnected()
            _PRINTER.print(
                f"[CrossChat] Bridge marked disconnected: context={context_id} "
                f"agent={conn.agent_name} (grace={RECONNECT_GRACE_SECONDS}s)"
            )
            return conn

    def unregister_by_sid(self, sid: str) -> Optional[BridgeConnection]:
        """Remove a bridge connection by WebSocket session id (immediate teardown)."""
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
        """Remove a bridge connection by context id (immediate teardown)."""
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
        """Get a bridge connection by context id.

        Returns the connection even if disconnected (within grace period),
        so that messages can still be queued for delivery on reconnect.
        """
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
        """Check if a context has an active bridge (connected or in grace period)."""
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
                    "disconnected": conn.disconnected,
                    "disconnected_at": conn.disconnected_at,
                }
                for conn in self._bridges.values()
            ]

    @property
    def active_count(self) -> int:
        with self._rlock:
            return len(self._bridges)

    # ── Grace Period Reaper ──────────────────────────────────────

    def _ensure_reaper(self):
        """Start the background reaper thread if not already running."""
        if self._reaper_started:
            return
        with self._rlock:
            if self._reaper_started:
                return
            self._reaper_started = True
            t = threading.Thread(
                target=self._reaper_loop,
                name="crosschat-bridge-reaper",
                daemon=True,
            )
            t.start()

    def _reaper_loop(self):
        """Periodically check for expired disconnected bridges and tear them down."""
        while True:
            time.sleep(10)  # Check every 10 seconds
            self._reap_expired()

    def _reap_expired(self):
        """Remove bridges whose grace period has expired."""
        to_remove = []
        with self._rlock:
            for context_id, conn in self._bridges.items():
                if conn.grace_expired:
                    to_remove.append(context_id)

        for context_id in to_remove:
            with self._rlock:
                conn = self._bridges.pop(context_id, None)
                if conn:
                    self._sid_to_context.pop(conn.ws_sid, None)
                    _PRINTER.print(
                        f"[CrossChat] Bridge grace period expired — torn down: "
                        f"context={context_id} agent={conn.agent_name}"
                    )
