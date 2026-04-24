"""REST fallback endpoint for polling bridge events.

POST /api/plugins/a0_crosschatapi/crosschat_rest_poll

Returns queued events (user_input, inference_delta, inference_complete)
since the last cursor for clients that cannot use WebSocket.
"""

from helpers.api import ApiHandler
from flask import Request, Response


class CrosschatRestPoll(ApiHandler):
    @classmethod
    def requires_auth(cls) -> bool:
        return True

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["POST"]

    async def process(self, input: dict, request: Request) -> dict | Response:
        from usr.plugins.a0_crosschatapi.helpers.bridge_manager import BridgeManager

        context_id = input.get("context_id", "")
        since_event_id = input.get("since_event_id", None)

        if not context_id:
            return {"ok": False, "error": "context_id is required"}

        mgr = BridgeManager.get_instance()
        conn = mgr.get_by_context(context_id)

        if not conn:
            return {
                "ok": False,
                "error": f"No active bridge for context {context_id}",
            }

        events = conn.drain_events(since_event_id=since_event_id)

        return {
            "ok": True,
            "context_id": context_id,
            "events": events,
            "event_count": len(events),
        }
