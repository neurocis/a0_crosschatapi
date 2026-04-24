"""Short-circuit context.communicate() on bridged contexts.

When a user types in A0's UI on a bridged context, the user_message_ui
extension intercepts the message and sets data['_bridged'] = True.

However, message.py STILL calls context.communicate() with the emptied
message. This @extensible start hook catches that call and sets
data['result'] to prevent communicate() from executing, which would
otherwise trigger _process_chain() -> agent.monologue() -> inference.

Without this hook, A0 would run inference on an empty message even
after the user_message_ui extension cleared the message text.

IMPORTANT: This extension MUST be sync (not async) because
communicate() can be called from sync contexts (e.g. nudge()).
The @extensible decorator uses call_extensions_sync for sync callers
and rejects awaitables with ValueError.
"""

from helpers.extension import Extension
from helpers.print_style import PrintStyle

_PRINTER = PrintStyle(italic=True, font_color="#00CED1", padding=False)


class CrossChatBlockInference(Extension):
    def execute(self, **kwargs):
        data = kwargs.get("data", {})
        if not data:
            return

        # The first positional arg to communicate() is `self` (the AgentContext),
        # second is `msg` (the UserMessage)
        args = data.get("args", ())
        if len(args) < 1:
            return

        # Get the AgentContext instance (first arg — self for the bound method)
        context = args[0] if hasattr(args[0], 'id') and hasattr(args[0], 'data') else None
        if context is None:
            # Try getting from self.agent
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

        # Check if the message was intercepted by user_message_ui
        # The intercepted flag is stored in context.data by the intercept extension
        if not context.data.get("_bridge_intercept_active", False):
            # Not an intercepted message — could be an explicit inference request
            # or a programmatic communicate() call. Let it proceed.
            return

        _PRINTER.print(
            f"[CrossChat] Blocking inference on bridged context {context.id} — "
            f"message was forwarded to {conn.agent_name}"
        )

        # Clear the intercept flag
        context.data["_bridge_intercept_active"] = False

        # Short-circuit communicate() by setting data['result']
        # communicate() normally returns self.task (a DeferredTask)
        # We create a minimal DeferredTask that resolves immediately
        from helpers.defer import DeferredTask

        task = DeferredTask(thread_name="crosschat_noop")

        async def _noop_task(*a, **kw):
            return f"Message forwarded to {conn.agent_name} for processing."

        task.start_task(_noop_task)
        context.task = task  # Set on context so respond() can await it

        data["result"] = task
