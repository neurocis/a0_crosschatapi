"""WebSocket handler for the cross-chat bridge.

Activated by clients including 'plugins/a0_crosschatapi/crosschat_sync'
in their auth.handlers list during the Socket.IO connect handshake.

Handles bidirectional message relay between A0 and an external agent
without triggering local inference unless explicitly requested.
"""

import time
import uuid
from typing import Any, Optional

from helpers.ws import WsHandler
from helpers.print_style import PrintStyle
from helpers.errors import format_error

_PRINTER = PrintStyle(italic=True, font_color="#00CED1", padding=False)


class CrossChatSync(WsHandler):
    """WebSocket handler for cross-chat bridge synchronization."""

    @classmethod
    def requires_auth(cls) -> bool:
        return False

    @classmethod
    def requires_csrf(cls) -> bool:
        return False

    async def on_connect(self, sid: str) -> None:
        _PRINTER.print(f"[CrossChat] WS client connected: {sid}")

    async def on_disconnect(self, sid: str) -> None:
        _PRINTER.print(f"[CrossChat] WS client disconnected: {sid}")
        # Unregister bridge on disconnect
        try:
            from usr.plugins.a0_crosschatapi.helpers.bridge_manager import BridgeManager
            mgr = BridgeManager.get_instance()
            conn = mgr.unregister_by_sid(sid)
            if conn:
                _PRINTER.print(
                    f"[CrossChat] Bridge torn down for context={conn.context_id} "
                    f"agent={conn.agent_name}"
                )
        except Exception as e:
            _PRINTER.print(f"[CrossChat] Error on disconnect cleanup: {e}")

    async def process(self, event: str, data: dict, sid: str) -> dict | None:
        """Route incoming events to the appropriate handler method."""
        correlation_id = data.get("correlationId", str(uuid.uuid4()))

        # Route based on event name
        handlers = {
            "crosschat_init": self._handle_init,
            "crosschat_sync": self._handle_sync,
            "crosschat_inference": self._handle_inference,
            "crosschat_ping": self._handle_ping,
        }

        handler = handlers.get(event)
        if handler is None:
            # Not a crosschat event — ignore silently
            return None

        try:
            result = await handler(sid, data, correlation_id)
            return result
        except Exception as e:
            error_text = format_error(e)
            _PRINTER.print(f"[CrossChat] Error handling {event}: {error_text}")
            return {
                "type": "error",
                "message": str(e),
                "code": "HANDLER_ERROR",
                "correlationId": correlation_id,
            }

    # ── Event Handlers ────────────────────────────────────────────

    async def _handle_init(
        self, sid: str, data: dict, correlation_id: str
    ) -> dict:
        """Handle the init handshake from the external agent.

        Creates or reuses an AgentContext and registers the bridge.
        """
        from agent import AgentContext, AgentContextType
        from initialize import initialize_agent
        from usr.plugins.a0_crosschatapi.helpers.bridge_manager import (
            BridgeManager, BridgeConnection,
        )

        agent_name = data.get("agent_name", "External Agent")
        context_id = data.get("context_id")

        # Try to reuse existing context
        context = None
        if context_id:
            context = AgentContext.get(context_id)

        # Create a new context if needed
        if context is None:
            cfg = initialize_agent()
            context = AgentContext(cfg, type=AgentContextType.USER)
            context.name = f"\U0001f517 {agent_name}"
            context_id = context.id
            _PRINTER.print(
                f"[CrossChat] Created new context {context_id} for {agent_name}"
            )
        else:
            context_id = context.id
            # Update name in case agent_name changed on reconnect
            context.name = f"\U0001f517 {agent_name}"
            _PRINTER.print(
                f"[CrossChat] Reusing context {context_id} for {agent_name}"
            )

        # Set context metadata to indicate bridge is active
        context.data["_bridge_active"] = True
        context.data["_bridge_agent_name"] = agent_name

        # Log the bridge connection in the context
        display_name = f"\U0001f517 {agent_name}"  # 🔗 emoji
        context.log.log(
            type="info",
            heading=f"Bridge connected: {display_name}",
            content=f"Bidirectional bridge established with {agent_name}.",
        )

        # Register the bridge connection
        mgr = BridgeManager.get_instance()
        conn = BridgeConnection(
            context_id=context_id,
            agent_name=agent_name,
            ws_handler=self,
            ws_sid=sid,
        )
        mgr.register(conn)

        # Notify the UI so the new chat appears in sidebar immediately
        try:
            from helpers.state_monitor_integration import mark_dirty_all
            mark_dirty_all(reason="crosschat_init")
        except Exception:
            pass

        # Persist the chat state
        try:
            from helpers.persist_chat import save_tmp_chat
            save_tmp_chat(context)
        except Exception:
            pass

        return {
            "type": "init_ack",
            "context_id": context_id,
            "agent_name": agent_name,
            "correlationId": correlation_id,
        }

    async def _handle_sync(
        self, sid: str, data: dict, correlation_id: str
    ) -> dict:
        """Handle bulk message sync (no inference).

        Replaces the context's log and history with the provided messages.
        """
        from agent import AgentContext
        from usr.plugins.a0_crosschatapi.helpers.bridge_manager import BridgeManager
        from usr.plugins.a0_crosschatapi.helpers.context_sync import (
            sync_messages_to_context,
        )

        mgr = BridgeManager.get_instance()
        conn = mgr.get_by_sid(sid)
        if not conn:
            return {
                "type": "error",
                "message": "No active bridge. Send crosschat_init first.",
                "code": "NO_BRIDGE",
                "correlationId": correlation_id,
            }

        conn.touch()

        context = AgentContext.get(conn.context_id)
        if not context:
            return {
                "type": "error",
                "message": f"Context {conn.context_id} not found.",
                "code": "CONTEXT_NOT_FOUND",
                "correlationId": correlation_id,
            }

        messages = data.get("messages", [])
        if not isinstance(messages, list):
            return {
                "type": "error",
                "message": "'messages' must be a list.",
                "code": "INVALID_PAYLOAD",
                "correlationId": correlation_id,
            }

        count = sync_messages_to_context(context, messages)

        return {
            "type": "sync_ack",
            "context_id": conn.context_id,
            "message_count": count,
            "status": "ok",
            "correlationId": correlation_id,
        }

    async def _handle_inference(
        self, sid: str, data: dict, correlation_id: str
    ) -> dict:
        """Handle an inference request — triggers A0's agent.

        The response streams back via crosschat_inference_delta and
        crosschat_inference_complete events.
        """
        from agent import AgentContext, UserMessage
        from usr.plugins.a0_crosschatapi.helpers.bridge_manager import BridgeManager
        from helpers import message_queue as mq

        mgr = BridgeManager.get_instance()
        conn = mgr.get_by_sid(sid)
        if not conn:
            return {
                "type": "error",
                "message": "No active bridge. Send crosschat_init first.",
                "code": "NO_BRIDGE",
                "correlationId": correlation_id,
            }

        conn.touch()

        context = AgentContext.get(conn.context_id)
        if not context:
            return {
                "type": "error",
                "message": f"Context {conn.context_id} not found.",
                "code": "CONTEXT_NOT_FOUND",
                "correlationId": correlation_id,
            }

        message_text = data.get("message", "")
        message_id = data.get("message_id", str(uuid.uuid4()))

        if not message_text:
            return {
                "type": "error",
                "message": "'message' cannot be empty.",
                "code": "EMPTY_MESSAGE",
                "correlationId": correlation_id,
            }

        # Mark inference as active so streaming extensions know to relay
        conn.inference_active = True
        conn.inference_message_id = message_id
        conn.inference_buffer = ""

        # Log the user message
        mq.log_user_message(context, message_text, [], message_id=message_id)

        # Trigger inference via context.communicate()
        # The response will stream via extension hooks
        user_msg = UserMessage(
            message=message_text,
            attachments=[],
            id=message_id,
        )
        context.communicate(user_msg)

        return {
            "type": "inference_started",
            "message_id": message_id,
            "context_id": conn.context_id,
            "correlationId": correlation_id,
        }

    async def _handle_ping(
        self, sid: str, data: dict, correlation_id: str
    ) -> dict:
        """Handle keepalive ping."""
        from usr.plugins.a0_crosschatapi.helpers.bridge_manager import BridgeManager

        mgr = BridgeManager.get_instance()
        conn = mgr.get_by_sid(sid)
        if conn:
            conn.touch()

        return {
            "type": "pong",
            "timestamp": time.time(),
            "correlationId": correlation_id,
        }

    # ── Outbound Event Helpers ────────────────────────────────────

    async def send_user_input(
        self,
        sid: str,
        text: str,
        message_id: Optional[str] = None,
    ) -> None:
        """Send a user_input event to the external client.

        Called when someone types in A0's UI on a bridged context.
        """
        msg_id = message_id or str(uuid.uuid4())
        await self.emit_to(sid, "crosschat_user_input", {
            "type": "user_input",
            "text": text,
            "message_id": msg_id,
            "timestamp": time.time(),
        })

    async def send_inference_delta(
        self,
        sid: str,
        text: str,
        message_id: str,
    ) -> None:
        """Send a streaming inference chunk to the external client."""
        await self.emit_to(sid, "crosschat_inference_delta", {
            "type": "inference_delta",
            "text": text,
            "message_id": message_id,
            "timestamp": time.time(),
        })

    async def send_inference_complete(
        self,
        sid: str,
        text: str,
        message_id: str,
    ) -> None:
        """Send inference completion to the external client."""
        await self.emit_to(sid, "crosschat_inference_complete", {
            "type": "inference_complete",
            "text": text,
            "message_id": message_id,
            "timestamp": time.time(),
        })

    async def send_context_updated(
        self,
        sid: str,
        events: list[dict],
    ) -> None:
        """Send context update events to the external client."""
        await self.emit_to(sid, "crosschat_context_updated", {
            "type": "context_updated",
            "events": events,
            "timestamp": time.time(),
        })

    async def send_error(
        self,
        sid: str,
        message: str,
        code: str = "ERROR",
    ) -> None:
        """Send an error event to the external client."""
        await self.emit_to(sid, "crosschat_error", {
            "type": "error",
            "message": message,
            "code": code,
            "timestamp": time.time(),
        })
