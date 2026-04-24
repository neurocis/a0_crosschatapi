"""Forward response stream completion to the cross-chat bridge.

When inference finishes on a bridged context, this extension sends the
complete response text to the external agent via the WebSocket bridge
as an inference_complete event, then resets the inference tracking state.
"""

import asyncio
from agent import LoopData
from helpers.extension import Extension
from helpers.print_style import PrintStyle

_PRINTER = PrintStyle(italic=True, font_color="#00CED1", padding=False)


class CrossChatStreamComplete(Extension):
    async def execute(
        self,
        loop_data: LoopData = LoopData(),
        text: str = "",
        parsed: dict = {},
        **kwargs,
    ):
        if not self.agent:
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

        # Use the full response text, falling back to accumulated buffer
        full_text = text or conn.inference_buffer
        message_id = conn.inference_message_id or ""

        _PRINTER.print(
            f"[CrossChat] Inference complete on context {context.id}, "
            f"sending {len(full_text)} chars to {conn.agent_name}"
        )

        # Send completion to external client via WebSocket
        try:
            handler = conn.ws_handler
            await handler.send_inference_complete(
                sid=conn.ws_sid,
                text=full_text,
                message_id=message_id,
            )
        except Exception as e:
            _PRINTER.print(f"[CrossChat] Failed to send inference complete: {e}")

        # Also queue for REST poll fallback
        conn.queue_event("inference_complete", {
            "text": full_text,
            "message_id": message_id,
        })

        # Reset inference tracking state
        conn.inference_active = False
        conn.inference_message_id = None
        conn.inference_buffer = ""
        conn.touch()
