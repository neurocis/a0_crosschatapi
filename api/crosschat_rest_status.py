"""REST endpoint for bridge status.

GET/POST /api/plugins/a0_crosschatapi/crosschat_rest_status

Returns the status of all active bridges or a specific bridge.
"""

from helpers.api import ApiHandler
from flask import Request, Response


class CrosschatRestStatus(ApiHandler):
    @classmethod
    def requires_auth(cls) -> bool:
        return True

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["GET", "POST"]

    async def process(self, input: dict, request: Request) -> dict | Response:
        from usr.plugins.a0_crosschatapi.helpers.bridge_manager import BridgeManager

        mgr = BridgeManager.get_instance()
        context_id = input.get("context_id", None)

        if context_id:
            conn = mgr.get_by_context(context_id)
            if not conn:
                return {
                    "ok": True,
                    "bridged": False,
                    "context_id": context_id,
                }
            return {
                "ok": True,
                "bridged": True,
                "context_id": conn.context_id,
                "agent_name": conn.agent_name,
                "connected_at": conn.connected_at,
                "last_activity": conn.last_activity,
                "inference_active": conn.inference_active,
            }

        # Return all bridges
        bridges = mgr.list_bridges()
        return {
            "ok": True,
            "active_count": mgr.active_count,
            "bridges": bridges,
        }
