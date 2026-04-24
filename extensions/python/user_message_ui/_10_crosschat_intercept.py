"""Intercept user messages on bridged contexts.

When a user types in A0's UI on a context that has an active bridge,
this extension intercepts the message BEFORE context.communicate() runs.
Instead of triggering A0's inference, it:
1. Forwards the message to the external agent via WebSocket
2. Sets _bridge_intercept_active flag so the communicate/start hook can block inference
3. Logs the forwarded message in the context

The external agent will process the message and sync the response
back via the sync_messages WebSocket event.

IMPORTANT: Clearing data['message'] alone does NOT prevent inference.
message.py still calls context.communicate(UserMessage(message='')).
The actual inference block happens in the @extensible communicate/start
hook (_10_crosschat_block_inference.py) which checks the
_bridge_intercept_active flag we set here.
"""

import asyncio
import uuid
from helpers.extension import Extension
from helpers.print_style import PrintStyle

_PRINTER = PrintStyle(italic=True, font_color="#00CED1", padding=False)


class CrossChatIntercept(Extension):
    async def execute(self, **kwargs):
        # data dict contains 'message' and 'attachment_paths'
        data = kwargs.get("data", {})
        agent = self.agent

        if not agent or not agent.context:
            return

        context = agent.context

        # Check if this context has an active bridge
        try:
            from usr.plugins.a0_crosschatapi.helpers.bridge_manager import BridgeManager
            mgr = BridgeManager.get_instance()
            conn = mgr.get_by_context(context.id)
        except Exception:
            return

        if not conn:
            return  # No bridge — let normal A0 flow proceed

        message = data.get("message", "")
        if not message:
            return

        _PRINTER.print(
            f"[CrossChat] Intercepting UI message on bridged context "
            f"{context.id} -> forwarding to {conn.agent_name}"
        )

        # Forward the message to the external agent via WebSocket
        msg_id = str(uuid.uuid4())
        try:
            handler = conn.ws_handler
            await handler.send_user_input(
                sid=conn.ws_sid,
                text=message,
                message_id=msg_id,
            )
            _PRINTER.print(
                f"[CrossChat] Forwarded message to {conn.agent_name}: "
                f"{message[:80]}{'...' if len(message) > 80 else ''}"
            )

            # Also queue for REST poll fallback
            conn.queue_event("user_input", {
                "text": message,
                "message_id": msg_id,
            })
            conn.touch()

        except Exception as e:
            _PRINTER.print(f"[CrossChat] Failed to forward message: {e}")
            # Don't intercept — let A0 process it as fallback
            return

        # Set the intercept flag so the communicate/start hook blocks inference
        # This is CRITICAL — clearing data['message'] alone does NOT prevent
        # inference because message.py still calls context.communicate()
        context.data["_bridge_intercept_active"] = True

        # Clear the message to prevent it from being logged as a normal user message
        data["message"] = ""
        data["_bridged"] = True
        data["_bridge_message_id"] = msg_id

        # Log in the context that the message was forwarded
        context.log.log(
            type="user",
            heading="",
            content=message,
            id=msg_id,
        )
        context.log.log(
            type="info",
            heading="Message forwarded",
            content=f"Sent to {conn.agent_name} for processing.",
        )
