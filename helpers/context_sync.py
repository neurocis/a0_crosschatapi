"""Context synchronization helpers.

Manipulate a context's log and history entries without triggering inference.
Used by the cross-chat bridge to sync conversation state from an external agent.
"""

import time
import uuid
from typing import Any, Optional

from helpers.print_style import PrintStyle

_PRINTER = PrintStyle(italic=True, font_color="#00CED1", padding=False)


def sync_messages_to_context(
    context: Any,
    messages: list[dict],
) -> int:
    """Replace a context's log entries with the provided messages.

    This writes directly to the context's Log without calling
    context.communicate() or triggering any inference.

    Args:
        context: An AgentContext instance.
        messages: List of message dicts with keys:
            - id: str (message uuid)
            - role: str ("user" or "assistant")
            - content: str (message text)
            - timestamp: float (unix timestamp)

    Returns:
        Number of messages synced.
    """
    from helpers.persist_chat import save_tmp_chat

    log = context.log

    # Clear existing log entries
    with log._lock:
        log.logs.clear()
        log.updates.clear()
        log.guid = str(uuid.uuid4())

    # Map roles to log types
    role_to_type = {
        "user": "user",
        "assistant": "response",
        "system": "info",
        "tool": "tool",
    }

    count = 0
    for msg in messages:
        msg_role = msg.get("role", "user")
        msg_content = msg.get("content", "")
        msg_id = msg.get("id", str(uuid.uuid4()))
        msg_timestamp = msg.get("timestamp", time.time())
        log_type = role_to_type.get(msg_role, "info")

        # Create log entry directly
        item = log.log(
            type=log_type,
            heading="" if msg_role != "user" else "User message",
            content=msg_content,
            id=msg_id,
        )
        # Override timestamp
        item.timestamp = msg_timestamp
        count += 1

    # Also sync to agent history for model context
    _sync_history(context, messages)

    # Persist the chat state
    try:
        save_tmp_chat(context)
    except Exception as e:
        _PRINTER.print(f"[CrossChat] Warning: failed to persist chat: {e}")

    # Notify all UI connections that state changed
    _mark_dirty(context)

    _PRINTER.print(f"[CrossChat] Synced {count} messages to context {context.id}")
    return count


def _sync_history(context: Any, messages: list[dict]) -> None:
    """Sync messages to the agent's conversation history.

    This updates the History object on the agent so the LLM has
    proper context if inference is later requested.
    """
    try:
        agent = context.agent0
        if not agent:
            return

        # Clear existing history
        agent.history.clear()

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "user":
                agent.history.add(
                    role="user",
                    msg=content,
                )
            elif role == "assistant":
                agent.history.add(
                    role="assistant",
                    msg=content,
                )
    except Exception as e:
        _PRINTER.print(f"[CrossChat] Warning: failed to sync history: {e}")


def _mark_dirty(context: Any) -> None:
    """Notify the UI that the context state has changed."""
    try:
        from helpers.state_monitor_integration import mark_dirty_for_context
        mark_dirty_for_context(context.id, reason="crosschat_sync")
    except ImportError:
        try:
            from helpers.log import _lazy_mark_dirty_all
            _lazy_mark_dirty_all(reason="crosschat_sync")
        except Exception:
            pass
    except Exception as e:
        _PRINTER.print(f"[CrossChat] Warning: mark_dirty failed: {e}")


def add_user_message_to_log(
    context: Any,
    text: str,
    message_id: Optional[str] = None,
) -> None:
    """Add a single user message to the context log without triggering inference.

    Used when forwarding a user_input event from the A0 UI to the external agent.
    """
    from helpers.persist_chat import save_tmp_chat

    msg_id = message_id or str(uuid.uuid4())

    context.log.log(
        type="user",
        heading="User message",
        content=text,
        id=msg_id,
    )

    try:
        save_tmp_chat(context)
    except Exception:
        pass

    _mark_dirty(context)
