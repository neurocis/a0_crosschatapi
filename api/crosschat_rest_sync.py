"""REST fallback endpoint for bulk message sync.

POST /api/plugins/a0_crosschatapi/crosschat_rest_sync

Accepts the same payload as the crosschat_sync WebSocket event
and syncs messages to a bridged context without triggering inference.
"""

from helpers.api import ApiHandler
from flask import Request, Response


class CrosschatRestSync(ApiHandler):
    @classmethod
    def requires_auth(cls) -> bool:
        return True

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["POST"]

    async def process(self, input: dict, request: Request) -> dict | Response:
        from agent import AgentContext
        from usr.plugins.a0_crosschatapi.helpers.bridge_manager import BridgeManager
        from usr.plugins.a0_crosschatapi.helpers.context_sync import (
            sync_messages_to_context,
        )

        context_id = input.get("context_id", "")
        messages = input.get("messages", [])

        if not context_id:
            return {"ok": False, "error": "context_id is required"}

        if not isinstance(messages, list):
            return {"ok": False, "error": "messages must be a list"}

        # Verify the context exists and has a bridge (or allow standalone sync)
        context = AgentContext.get(context_id)
        if not context:
            return {"ok": False, "error": f"Context {context_id} not found"}

        count = sync_messages_to_context(context, messages)

        return {
            "ok": True,
            "type": "sync_ack",
            "context_id": context_id,
            "message_count": count,
            "status": "ok",
        }
