# Agent Zero CrossChat Bridge — API Specification

**Version:** 1.0.0  
**Protocol:** Socket.IO 4.x (over WebSocket) + REST fallback  
**Base URL:** `http://<a0-host>:50001`

---

## Table of Contents

1. [Overview](#overview)
2. [Authentication](#authentication)
3. [WebSocket Protocol](#websocket-protocol)
   - [Connection](#connection)
   - [Client → Server Events](#client--server-events)
   - [Server → Client Events](#server--client-events)
4. [REST Fallback API](#rest-fallback-api)
5. [Message Schema](#message-schema)
6. [Connection Lifecycle](#connection-lifecycle)
7. [Error Handling](#error-handling)
8. [Implementation Examples](#implementation-examples)

---

## 1. Overview

The CrossChat Bridge enables an **External Agent** to share a chat context with Agent Zero (A0). When the bridge is active:

- Messages synced from the External Agent appear in A0's chat UI (without triggering A0 inference).
- Messages typed by users in A0's chat UI are forwarded to the External Agent (without triggering A0 inference).
- The External Agent can request A0 to run inference and receive the response via streaming.
- Both sides see a unified conversation history.

```
┌──────────────────┐                        ┌──────────────────┐
│  External Agent   │◄──── Socket.IO ──────►│   Agent Zero      │
│  (your system)    │     /ws namespace      │   (A0 server)     │
│                   │                        │                   │
│  Sends messages   │──crosschat_sync──────►│  Displays in UI   │
│  Gets UI input    │◄─crosschat_user_input─│  User types here  │
│  Requests A0 AI   │──crosschat_inference─►│  Runs inference   │
│  Gets AI stream   │◄─crosschat_inf_delta──│  Streams response │
│  Gets AI complete │◄─crosschat_inf_done───│  Response done    │
└──────────────────┘                        └──────────────────┘
```

---

## 2. Authentication

All endpoints require authentication. A0 supports two auth methods:

### Method A: Session Cookie Authentication

1. **Obtain session**: `POST /api/csrf_token` to get a CSRF token and session cookie.
2. **Login**: Authenticate via A0's login flow to establish a session.
3. **Connect WebSocket**: Pass the session cookie and CSRF token in the Socket.IO `auth` payload.

### Method B: API Key Authentication (Recommended for agents)

1. **Configure API key**: Set `API_KEY` in A0's settings.
2. **Pass in auth payload**: Include `api_key` in the Socket.IO `auth` object.

### WebSocket Auth Payload

```json
{
  "auth": {
    "handlers": ["plugins/a0_crosschatapi/crosschat_sync"],
    "api_key": "<your-api-key>",
    "csrf_token": "<optional-csrf-token>"
  }
}
```

### REST Auth Headers

For REST endpoints, include session cookies or:
```
Cookie: session=<session-cookie>
X-CSRF-Token: <csrf-token>
```

Or use the API key mechanism configured in your A0 instance.

---

## 3. WebSocket Protocol

### Connection

**Namespace:** `/ws`  
**Transport:** WebSocket (preferred) or HTTP long-polling  

```javascript
const socket = io('http://<a0-host>:50001/ws', {
  auth: {
    handlers: ['plugins/a0_crosschatapi/crosschat_sync'],
    api_key: '<your-api-key>'
  },
  transports: ['websocket']
});
```

The `handlers` array tells A0 which WebSocket handler to activate for this connection. You **must** include `plugins/a0_crosschatapi/crosschat_sync`.

---

### Client → Server Events

All client events follow this envelope format:

```json
{
  "type": "<event_type>",
  "correlationId": "<unique-request-id>",
  ...event-specific fields
}
```

The `correlationId` is echoed back in responses for request-response correlation.

---

#### `crosschat_init`

Establish or resume a bridge session. **Must be called first.**

**Emit event name:** `crosschat_sync`  
**Payload:**

```json
{
  "type": "init",
  "correlationId": "uuid-1",
  "agent_name": "My External Agent",
  "context_id": null
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | ✅ | Must be `"init"` |
| `correlationId` | string | ✅ | Unique ID for this request |
| `agent_name` | string | ❌ | Display name shown in A0 UI. Default: `"External Agent"` |
| `context_id` | string \| null | ❌ | Existing A0 context ID to reuse. `null` creates a new chat. |

**Response event:** `crosschat_sync`

```json
{
  "type": "init_ack",
  "correlationId": "uuid-1",
  "context_id": "aBcDeFgH",
  "agent_name": "My External Agent"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `context_id` | string | The A0 context ID for this bridge. **Save this** — use it for all subsequent calls and to reconnect. |
| `agent_name` | string | Confirmed agent name |

**Side effects:**
- A new chat appears in A0's sidebar (or the existing one is reused)
- A "🔗 Bridge connected" info entry is logged in the chat
- User messages typed in this chat will be forwarded to the External Agent instead of triggering A0 inference

---

#### `crosschat_sync` (message sync)

Sync conversation messages into A0's chat **without triggering inference**.

Use this to populate A0's chat with the External Agent's conversation history.

**Emit event name:** `crosschat_sync`  
**Payload:**

```json
{
  "type": "sync",
  "correlationId": "uuid-2",
  "messages": [
    {
      "role": "user",
      "content": "What is the capital of France?",
      "timestamp": "2025-04-23T10:30:00Z",
      "id": "msg-001"
    },
    {
      "role": "assistant",
      "content": "The capital of France is Paris.",
      "timestamp": "2025-04-23T10:30:05Z",
      "id": "msg-002"
    },
    {
      "role": "info",
      "content": "Searched knowledge base",
      "heading": "Tool: search",
      "timestamp": "2025-04-23T10:30:03Z",
      "id": "msg-003"
    }
  ]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | ✅ | Must be `"sync"` |
| `correlationId` | string | ✅ | Unique ID |
| `messages` | array | ✅ | Array of message objects (see [Message Schema](#5-message-schema)) |

**Response event:** `crosschat_sync`

```json
{
  "type": "sync_ack",
  "correlationId": "uuid-2",
  "accepted": 3,
  "status": "ok"
}
```

**Important:** Synced messages appear in A0's UI but **never trigger A0 inference**. They are for display and context only.

---

#### `crosschat_inference`

Request A0 to process a message using its full AI pipeline (inference).

**Emit event name:** `crosschat_sync`  
**Payload:**

```json
{
  "type": "inference",
  "correlationId": "uuid-3",
  "text": "Analyze this code and suggest improvements.",
  "message_id": "msg-004"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | ✅ | Must be `"inference"` |
| `correlationId` | string | ✅ | Unique ID |
| `text` | string | ✅ | The message to process |
| `message_id` | string | ❌ | Optional client-side message ID |

**Immediate response event:** `crosschat_sync`

```json
{
  "type": "inference_ack",
  "correlationId": "uuid-3",
  "message_id": "msg-004",
  "status": "processing"
}
```

**Streaming response:** As A0 generates its response, you'll receive:

1. Multiple `crosschat_inference_delta` events (streaming chunks)
2. One `crosschat_inference_complete` event (final response)

See [Server → Client Events](#server--client-events) below.

---

#### `crosschat_ping`

Keepalive heartbeat.

**Emit event name:** `crosschat_sync`  
**Payload:**

```json
{
  "type": "ping",
  "correlationId": "uuid-4"
}
```

**Response event:** `crosschat_sync`

```json
{
  "type": "pong",
  "correlationId": "uuid-4"
}
```

---

### Server → Client Events

These events are emitted by A0 to the External Agent.

---

#### `crosschat_user_input`

A user typed a message in A0's chat UI on the bridged context.

**Event name:** `crosschat_user_input`

```json
{
  "text": "Can you also check the database schema?",
  "message_id": "a0-msg-uuid-123"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `text` | string | The message the user typed in A0 |
| `message_id` | string | A0-generated message ID |

**Expected behavior:** The External Agent should process this message and optionally sync the response back via `crosschat_sync`.

---

#### `crosschat_inference_delta`

A streaming chunk of A0's inference response.

**Event name:** `crosschat_inference_delta`

```json
{
  "text": "Here are my suggestions for improving",
  "message_id": "msg-004"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `text` | string | Incremental text chunk (append to previous chunks) |
| `message_id` | string | Matches the `message_id` from the inference request |

---

#### `crosschat_inference_complete`

A0's inference is complete.

**Event name:** `crosschat_inference_complete`

```json
{
  "text": "Here are my suggestions for improving the code:\n\n1. Extract the database logic...\n2. Add error handling...",
  "message_id": "msg-004"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `text` | string | The complete response text |
| `message_id` | string | Matches the `message_id` from the inference request |

---

#### `crosschat_error`

An error occurred during processing.

**Event name:** `crosschat_error`

```json
{
  "error": "Context not found",
  "correlationId": "uuid-2",
  "code": "CONTEXT_NOT_FOUND"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `error` | string | Human-readable error message |
| `correlationId` | string \| null | Request that caused the error (if applicable) |
| `code` | string | Machine-readable error code |

**Error codes:**

| Code | Description |
|------|-------------|
| `AUTH_REQUIRED` | Authentication failed |
| `CSRF_COOKIE` | CSRF validation failed |
| `INVALID_PAYLOAD` | Missing or malformed request fields |
| `CONTEXT_NOT_FOUND` | The specified context_id doesn't exist |
| `NOT_INITIALIZED` | Sent an event before `crosschat_init` |
| `INFERENCE_ACTIVE` | Another inference is already running |
| `UNKNOWN_TYPE` | Unrecognized event type |
| `INTERNAL_ERROR` | Server-side error |

---

## 4. REST Fallback API

For environments where persistent WebSocket connections aren't feasible, three REST endpoints are available.

**All endpoints require authentication** (session cookie or API key).

---

### `POST /api/crosschat_rest_sync`

Sync messages (same as WebSocket `crosschat_sync` type `sync`).

**Request:**
```json
{
  "context_id": "aBcDeFgH",
  "messages": [
    {
      "role": "user",
      "content": "Hello",
      "timestamp": "2025-04-23T10:30:00Z"
    },
    {
      "role": "assistant",
      "content": "Hi there!",
      "timestamp": "2025-04-23T10:30:02Z"
    }
  ]
}
```

**Response:**
```json
{
  "ok": true,
  "type": "sync_ack",
  "context_id": "aBcDeFgH",
  "message_count": 2,
  "status": "ok"
}
```

---

### `POST /api/crosschat_rest_poll`

Poll for queued events (user input, inference deltas, etc.).

**Request:**
```json
{
  "context_id": "aBcDeFgH",
  "since_event_id": 0
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `context_id` | string | ✅ | The bridged context ID |
| `since_event_id` | int | ❌ | Only return events after this ID. Default: `0` (all) |

**Response:**
```json
{
  "ok": true,
  "events": [
    {
      "event_id": 1,
      "type": "user_input",
      "data": {
        "text": "Check the logs please",
        "message_id": "a0-msg-uuid-456"
      }
    },
    {
      "event_id": 2,
      "type": "inference_complete",
      "data": {
        "text": "I've analyzed the logs and found...",
        "message_id": "msg-005"
      }
    }
  ]
}
```

**Event types returned:** `user_input`, `inference_delta`, `inference_complete`

---

### `POST /api/crosschat_rest_status`

Check bridge status.

**Request (specific context):**
```json
{
  "context_id": "aBcDeFgH"
}
```

**Response:**
```json
{
  "ok": true,
  "bridge": {
    "context_id": "aBcDeFgH",
    "agent_name": "My External Agent",
    "inference_active": false,
    "last_activity": "2025-04-23T10:35:00Z",
    "queued_events": 2
  }
}
```

**Request (all bridges):**
```json
{}
```

**Response:**
```json
{
  "ok": true,
  "active_count": 1,
  "bridges": [
    {
      "context_id": "aBcDeFgH",
      "agent_name": "My External Agent",
      "inference_active": false
    }
  ]
}
```

---

## 5. Message Schema

Messages in `crosschat_sync` follow this schema:

```json
{
  "role": "user | assistant | info | tool",
  "content": "Message text content",
  "heading": "Optional heading (for info/tool messages)",
  "timestamp": "2025-04-23T10:30:00Z",
  "id": "optional-unique-id"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `role` | string | ✅ | Message role: `user`, `assistant`, `info`, `tool` |
| `content` | string | ✅ | Message text content (supports Markdown) |
| `heading` | string | ❌ | Optional heading/title (used for `info` and `tool` roles) |
| `timestamp` | string | ❌ | ISO 8601 timestamp. Default: current time |
| `id` | string | ❌ | Unique message ID for deduplication |

### Role Mapping

| Role | A0 Display | Description |
|------|-----------|-------------|
| `user` | User message bubble | Messages from the user/human |
| `assistant` | Agent response bubble | Messages from an AI agent |
| `info` | Info log entry | Informational/status messages |
| `tool` | Tool result log entry | Tool execution results |

---

## 6. Connection Lifecycle

### Typical Flow

```
1. CONNECT      Socket.IO connect with auth
2. INIT         crosschat_init → get context_id
3. SYNC         crosschat_sync → populate history
4. LISTEN       Listen for crosschat_user_input events
5. INFERENCE    (optional) crosschat_inference → get AI response
6. SYNC         crosschat_sync → add new messages as conversation progresses
7. PING         Periodic crosschat_ping keepalive
8. DISCONNECT   Socket.IO disconnect → bridge torn down
```

### Reconnection

To reconnect after a disconnect:

1. Re-establish Socket.IO connection with same auth
2. Send `crosschat_init` with the **saved `context_id`** to resume the session
3. Optionally re-sync any messages that may have been missed

### Bridge Teardown

When the External Agent disconnects:

- The bridge is automatically removed from A0's registry
- The chat context remains in A0 (messages are preserved)
- User messages typed in the chat will resume normal A0 inference behavior
- Reconnecting with the same `context_id` re-establishes the bridge

---

## 7. Error Handling

### Connection Errors

| Scenario | Socket.IO Event | HTTP Status |
|----------|----------------|-------------|
| Invalid credentials | `connect_error` | 401 |
| CSRF mismatch | `connect_error` | 403 |
| Handler not found | `connect_error` | 404 |
| Server error | `connect_error` | 500 |

### Runtime Errors

Runtime errors are delivered as `crosschat_error` events (WebSocket) or error response bodies (REST).

### Best Practices

- Always handle `crosschat_error` events
- Implement exponential backoff for reconnection
- Save `context_id` persistently to survive process restarts
- Use `correlationId` to match responses to requests
- Check `inference_active` status before requesting new inference

---

## 8. Implementation Examples

### Python (python-socketio)

```python
import socketio
import uuid
import asyncio

sio = socketio.AsyncClient()

A0_URL = "http://localhost:50001"
API_KEY = "your-api-key-here"
AGENT_NAME = "My External Agent"

context_id = None  # Will be set after init


@sio.on('crosschat_user_input', namespace='/ws')
async def on_user_input(data):
    """A0 user typed something in the bridged chat."""
    print(f"User said: {data['text']}")
    # Process the message with your agent...
    response = await my_agent.process(data['text'])
    # Sync the response back to A0
    await sio.emit('crosschat_sync', {
        'type': 'sync',
        'correlationId': str(uuid.uuid4()),
        'messages': [{
            'role': 'assistant',
            'content': response,
        }]
    }, namespace='/ws')


@sio.on('crosschat_inference_delta', namespace='/ws')
async def on_inference_delta(data):
    """Streaming chunk from A0's inference."""
    print(data['text'], end='', flush=True)


@sio.on('crosschat_inference_complete', namespace='/ws')
async def on_inference_complete(data):
    """A0 inference finished."""
    print(f"\nA0 responded: {data['text']}")


@sio.on('crosschat_error', namespace='/ws')
async def on_error(data):
    """Error from A0."""
    print(f"Error: {data['error']} (code: {data.get('code')})")


async def main():
    global context_id

    # Connect
    await sio.connect(A0_URL, namespaces=['/ws'], auth={
        'handlers': ['plugins/a0_crosschatapi/crosschat_sync'],
        'api_key': API_KEY,
    }, transports=['websocket'])

    # Initialize bridge
    init_response = await sio.call('crosschat_sync', {
        'type': 'init',
        'correlationId': str(uuid.uuid4()),
        'agent_name': AGENT_NAME,
        'context_id': None,  # Create new chat
    }, namespace='/ws')

    context_id = init_response['context_id']
    print(f"Bridge established! Context: {context_id}")

    # Sync initial conversation
    await sio.emit('crosschat_sync', {
        'type': 'sync',
        'correlationId': str(uuid.uuid4()),
        'messages': [
            {'role': 'user', 'content': 'Hello Agent Zero!'},
            {'role': 'assistant', 'content': 'Hello from the External Agent!'},
        ]
    }, namespace='/ws')

    # Request A0 inference
    await sio.emit('crosschat_sync', {
        'type': 'inference',
        'correlationId': str(uuid.uuid4()),
        'text': 'What tools do you have available?',
        'message_id': str(uuid.uuid4()),
    }, namespace='/ws')

    # Keep alive
    await sio.wait()


asyncio.run(main())
```

### JavaScript (socket.io-client)

```javascript
import { io } from 'socket.io-client';
import { v4 as uuidv4 } from 'uuid';

const A0_URL = 'http://localhost:50001';
const API_KEY = 'your-api-key-here';

const socket = io(`${A0_URL}/ws`, {
  auth: {
    handlers: ['plugins/a0_crosschatapi/crosschat_sync'],
    api_key: API_KEY,
  },
  transports: ['websocket'],
});

let contextId = null;

socket.on('connect', () => {
  console.log('Connected to A0');

  // Initialize bridge
  socket.emit('crosschat_sync', {
    type: 'init',
    correlationId: uuidv4(),
    agent_name: 'My External Agent',
    context_id: null,
  });
});

// Handle init acknowledgment
socket.on('crosschat_sync', (data) => {
  if (data.type === 'init_ack') {
    contextId = data.context_id;
    console.log(`Bridge established! Context: ${contextId}`);
  }
  if (data.type === 'sync_ack') {
    console.log(`Synced ${data.accepted} messages`);
  }
  if (data.type === 'inference_ack') {
    console.log('Inference started...');
  }
});

// User typed in A0's chat
socket.on('crosschat_user_input', (data) => {
  console.log(`A0 user said: ${data.text}`);
  // Process with your agent and sync response back
});

// Streaming inference chunks
socket.on('crosschat_inference_delta', (data) => {
  process.stdout.write(data.text);
});

// Inference complete
socket.on('crosschat_inference_complete', (data) => {
  console.log(`\nA0 responded: ${data.text}`);
});

// Errors
socket.on('crosschat_error', (data) => {
  console.error(`Error: ${data.error} (${data.code})`);
});

socket.on('disconnect', () => {
  console.log('Disconnected from A0');
});
```

### REST Polling Pattern

For environments without WebSocket support:

```python
import requests
import time

A0_URL = "http://localhost:50001"
SESSION = requests.Session()
# Authenticate first (obtain session cookies)

# Sync messages
SESSION.post(f"{A0_URL}/api/crosschat_rest_sync", json={
    "context_id": "aBcDeFgH",
    "messages": [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
    ]
})

# Poll for events
last_event_id = 0
while True:
    resp = SESSION.post(f"{A0_URL}/api/crosschat_rest_poll", json={
        "context_id": "aBcDeFgH",
        "since_event_id": last_event_id,
    }).json()

    for event in resp.get("events", []):
        print(f"Event: {event['type']} -> {event['data']}")
        last_event_id = max(last_event_id, event['event_id'])

    time.sleep(2)  # Poll interval
```

---

## Quick Reference Card

### Events You Send

| Purpose | Event Name | Type Field |
|---------|-----------|------------|
| Establish bridge | `crosschat_sync` | `init` |
| Sync messages | `crosschat_sync` | `sync` |
| Request A0 inference | `crosschat_sync` | `inference` |
| Keepalive | `crosschat_sync` | `ping` |

### Events You Receive

| Purpose | Event Name |
|---------|------------|
| Responses to your events | `crosschat_sync` |
| User typed in A0 | `crosschat_user_input` |
| Inference streaming chunk | `crosschat_inference_delta` |
| Inference complete | `crosschat_inference_complete` |
| Error | `crosschat_error` |

### Dependencies

| Language | Package |
|----------|---------|
| Python | `python-socketio[asyncio_client]>=5.0` |
| JavaScript | `socket.io-client@^4.0` |
| Any HTTP client | REST endpoints at `/api/crosschat_rest_*` |
