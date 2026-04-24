"""Short-circuit context.communicate() on bridged contexts.

Intercepts ALL messages on contexts with an active bridge — whether
from the UI, superordinate_message, API calls, or any other source.

- If `_bridge_intercept_active` is True: the user_message_ui extension
  already forwarded the message. Just block inference.
- If `_bridge_intercept_active` is False/missing: this is a programmatic
  message (e.g. from superordinate_message). Forward it to the remote
  agent ourselves, then block inference.

IMPORTANT: This extension MUST be sync (not async) because
communicate() can be called from sync contexts (e.g. nudge()).
The @extensible decorator uses call_extensions_sync for sync callers
and rejects awaitables with ValueError.
"""

import asyncio
import uuid
from helpers.extension import Extension
from helpers.print_style import PrintStyle

_PRINTER = PrintStyle(italic=True, font_color="#00CED1", padding=False)


def _extract_message_text(args) -> str:
    """Extract message text from communicate() args.

    communicate(self, msg: UserMessage, broadcast_level=1)
    args[0] = AgentContext (self), args[1] = UserMessage
    """
    if len(args) < 2:
        return ""
    msg = args[1]
    # UserMessage is a dataclass with .message attribute
    text = getattr(msg, "message", None)
    if text:
        return text
    # Fallback: try string conversion
    if isinstance(msg, str):
        return msg
    return ""


def _schedule_ws_forward(conn, text: str, msg_id: str):
    """Schedule async WebSocket forwarding from a sync context."""
    async def _do_forward():
        try:
            handler = conn.ws_handler
            await handler.send_user_input(
                sid=conn.ws_sid,
                text=text,
                message_id=msg_id,
            )
            _PRINTER.print(
                f"[CrossChat] Forwarded programmatic message to {conn.agent_name}: "
                f"{text[:80]}{'...' if len(text) > 80 else ''}"
            )
        except Exception as e:
            _PRINTER.print(f"[CrossChat] Failed to forward via WS: {e}")

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_do_forward())
        else:
            loop.run_until_complete(_do_forward())
    except RuntimeError:
        # No event loop available — rely on REST fallback
        _PRINTER.print(
            "[CrossChat] No event loop for WS forward — using REST fallback only"
        )


class CrossChatBlockInference(Extension):
    def execute(self, **kwargs):
        data = kwargs.get("data", {})
        if not data:
            return

        args = data.get("args", ())
        if len(args) < 1:
            return

        # Get the AgentContext instance (first arg — self for the bound method)
        context = args[0] if hasattr(args[0], 'id') and hasattr(args[0], 'data') else None
        if context is None:
            if self.agent and self.agent.context:
                context = self.agent.context
            else:
                return

        # Check if this context has an active bridge
        try:
            from usr.plugins.a0_crosschatapi.helpers.bridge_manager import BridgeManager
            mgr = BridgeManager.get_instance()
            conn = mgr.get_by_context(context.id)
        except Exception:
            return

        if not conn:
            return  # No bridge — let normal communicate() proceed

        # Case 1: UI already forwarded the message (flag is set)
        if context.data.get("_bridge_intercept_active", False):
            _PRINTER.print(
                f"[CrossChat] Blocking inference on bridged context {context.id} — "
                f"UI message was forwarded to {conn.agent_name}"
            )
            context.data["_bridge_intercept_active"] = False

        else:
            # Case 2: Programmatic message (superordinate_message, API, etc.)
            # We need to forward it ourselves.
            text = _extract_message_text(args)
            if not text:
                _PRINTER.print(
                    f"[CrossChat] Bridged context {context.id} got empty programmatic "
                    f"message — blocking inference anyway"
                )
            else:
                msg_id = str(uuid.uuid4())

                _PRINTER.print(
                    f"[CrossChat] Intercepting programmatic message on bridged "
                    f"context {context.id} -> forwarding to {conn.agent_name}"
                )

                # Schedule async WS forwarding
                _schedule_ws_forward(conn, text, msg_id)

                # Also queue for REST poll fallback (sync-safe)
                try:
                    conn.queue_event("user_input", {
                        "text": text,
                        "message_id": msg_id,
                    })
                    conn.touch()
                except Exception as e:
                    _PRINTER.print(
                        f"[CrossChat] Failed to queue REST fallback: {e}"
                    )

                # Log in the context that the message was forwarded
                context.log.log(
                    type="user",
                    heading="",
                    content=text,
                    id=msg_id,
                )
                context.log.log(
                    type="info",
                    heading="Message forwarded",
                    content=f"Sent to {conn.agent_name} for processing.",
                )

        # Block inference by short-circuiting communicate()
        from helpers.defer import DeferredTask

        task = DeferredTask(thread_name="crosschat_noop")

        async def _noop_task(*a, **kw):
            return f"Message forwarded to {conn.agent_name} for processing."

        task.start_task(_noop_task)
        context.task = task

        data["result"] = task
