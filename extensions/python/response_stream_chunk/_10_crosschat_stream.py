"""Forward response stream chunks to the cross-chat bridge.

When inference is running on a bridged context, this extension
captures each streaming chunk and relays it to the external agent
via the WebSocket bridge as an inference_delta event.
"""

import asyncio
from agent import LoopData
from helpers.extension import Extension
from helpers.print_style import PrintStyle

_PRINTER = PrintStyle(italic=True, font_color="#00CED1", padding=False)


class CrossChatStreamChunk(Extension):
    async def execute(self, loop_data=LoopData(), stream_data=None, **kwargs):
        if not self.agent or stream_data is None:
            return

        context = self.agent.context
        if not context:
            return

        # Check if this context has an active bridge with inference running
        try:
            from usr.plugins.a0_crosschatapi.helpers.bridge_manager import BridgeManager
            mgr = BridgeManager.get_instance()
            conn = mgr.get_by_context(context.id)
        except Exception:
            return

        if not conn or not conn.inference_active:
            return

        # Extract the chunk text from stream_data
        chunk = stream_data.get("chunk", "")
        if not chunk:
            return

        # Accumulate in buffer
        conn.inference_buffer += chunk

        # Forward chunk to external client via WebSocket
        try:
            handler = conn.ws_handler
            await handler.send_inference_delta(
                sid=conn.ws_sid,
                text=chunk,
                message_id=conn.inference_message_id or "",
            )
        except Exception as e:
            _PRINTER.print(f"[CrossChat] Failed to send inference delta: {e}")

        # Also queue for REST poll fallback
        conn.queue_event("inference_delta", {
            "text": chunk,
            "message_id": conn.inference_message_id or "",
        })
