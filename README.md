# a0_crosschatapi — Cross-Chat Bridge API

A WebSocket-based bidirectional bridge plugin that allows an external agent to maintain a persistent 2-way streaming connection with Agent Zero. Messages flow through without triggering A0's local inference unless explicitly requested.

## Architecture

```
┌──────────────────┐         WebSocket (Socket.IO)         ┌──────────────────┐
│  External Agent   │ ◄──────────────────────────────────► │   Agent Zero      │
│  (Onscreen, etc.) │   crosschat_* events on /ws          │   (this plugin)   │
└──────────────────┘                                       └──────────────────┘
```

The bridge creates a dedicated `AgentContext` for each connection and:
- **Relays messages** typed in A0's UI to the external agent (bypassing local inference)
- **Receives responses** from the external agent and syncs them into A0's chat log
- **Supports on-demand inference** where A0 processes a message and streams the response back
- **Falls back to REST** for environments where WebSocket isn't available

## Quick Start

### 1. Enable the Plugin
The plugin is auto-discovered. Ensure it exists at `/a0/usr/plugins/a0_crosschatapi/`.

### 2. Connect via WebSocket
Connect to A0's Socket.IO namespace `/ws` with the handler path in your auth:

```javascript
const socket = io('http://your-a0-host:50001/ws', {
  auth: {
    handlers: ['plugins/a0_crosschatapi/crosschat_sync'],
    csrf_token: '<your-csrf-token>',
  },
});
```

### 3. Initialize the Bridge
```javascript
socket.emit('crosschat_init', {
  agent_name: 'My External Agent',
  context_id: null,  // null = create new, or provide existing
}, (response) => {
  console.log('Bridge established:', response.context_id);
});
```

### 4. Sync Messages (No Inference)
```javascript
socket.emit('crosschat_sync', {
  messages: [
    { id: 'msg-1', role: 'user', content: 'Hello!', timestamp: Date.now()/1000 },
    { id: 'msg-2', role: 'assistant', content: 'Hi there!', timestamp: Date.now()/1000 },
  ],
}, (response) => {
  console.log('Synced:', response.message_count, 'messages');
});
```

### 5. Request A0 Inference
```javascript
socket.emit('crosschat_inference', {
  message: 'Please analyze this code...',
  message_id: 'msg-3',
});

// Listen for streaming response
socket.on('crosschat_inference_delta', (data) => {
  process.stdout.write(data.text);
});

socket.on('crosschat_inference_complete', (data) => {
  console.log('\nFull response:', data.text);
});
```

### 6. Receive A0 UI Messages
```javascript
// When someone types in A0's web UI on the bridged chat:
socket.on('crosschat_user_input', (data) => {
  console.log('A0 user typed:', data.text);
  // Process and sync response back via crosschat_sync
});
```

## WebSocket Events

### Client → Server

| Event | Purpose | Key Fields |
|-------|---------|------------|
| `crosschat_init` | Establish bridge | `agent_name`, `context_id` (null=new) |
| `crosschat_sync` | Bulk sync messages (no inference) | `messages[]` |
| `crosschat_inference` | Request A0 to process a message | `message`, `message_id` |
| `crosschat_ping` | Keepalive | _(none)_ |

### Server → Client

| Event | Purpose | Key Fields |
|-------|---------|------------|
| `crosschat_user_input` | User typed in A0's UI | `text`, `message_id`, `timestamp` |
| `crosschat_inference_delta` | Streaming inference chunk | `text`, `message_id` |
| `crosschat_inference_complete` | Inference finished | `text`, `message_id` |
| `crosschat_context_updated` | Context state changed | `events[]` |
| `crosschat_error` | Error notification | `message`, `code` |

## REST Fallback Endpoints

For environments where WebSocket isn't available:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/plugins/a0_crosschatapi/crosschat_rest_sync` | POST | Bulk sync messages |
| `/api/plugins/a0_crosschatapi/crosschat_rest_poll` | POST | Poll for queued events |
| `/api/plugins/a0_crosschatapi/crosschat_rest_status` | GET/POST | Bridge status |

### REST Sync Example
```bash
curl -X POST http://localhost:50001/api/plugins/a0_crosschatapi/crosschat_rest_sync \
  -H 'Content-Type: application/json' \
  -d '{
    "context_id": "abc123",
    "messages": [
      {"id": "m1", "role": "user", "content": "Hello", "timestamp": 1234567890}
    ]
  }'
```

### REST Poll Example
```bash
curl -X POST http://localhost:50001/api/plugins/a0_crosschatapi/crosschat_rest_poll \
  -H 'Content-Type: application/json' \
  -d '{
    "context_id": "abc123",
    "since_event_id": null
  }'
```

## Message Interception

When the bridge is active on a context, messages typed in A0's web UI are:
1. **Intercepted** before reaching `context.communicate()`
2. **Forwarded** to the external agent via `crosschat_user_input`
3. **Logged** in the context as "Message forwarded"
4. A0's inference is **NOT triggered** — the external agent processes the message

If the bridge disconnects, normal A0 processing resumes automatically.

## File Structure

```
/a0/usr/plugins/a0_crosschatapi/
├── plugin.yaml                                          # Plugin manifest
├── README.md                                            # This file
├── helpers/
│   ├── __init__.py
│   ├── bridge_manager.py                                # Connection registry (singleton)
│   └── context_sync.py                                  # Log/history sync without inference
├── api/
│   ├── crosschat_sync.py                                # WsHandler: WebSocket bridge endpoint
│   ├── crosschat_rest_sync.py                           # REST: bulk message sync
│   ├── crosschat_rest_poll.py                           # REST: poll queued events
│   └── crosschat_rest_status.py                         # REST: bridge status
└── extensions/
    └── python/
        ├── user_message_ui/
        │   └── _10_crosschat_intercept.py                # Intercept UI messages on bridged contexts
        ├── response_stream_chunk/
        │   └── _10_crosschat_stream.py                   # Forward inference chunks
        └── response_stream_end/
            └── _10_crosschat_complete.py                  # Forward inference completion
```

## Key Constraints

- **sync_messages NEVER triggers inference** — it only writes to log and history
- **Message interception prevents context.communicate()** on bridged contexts
- **Bridge disconnect restores normal A0 flow** automatically
- Uses existing `persist_chat` helpers for serialization
- Uses existing `Log` and history classes for manipulation
- Follows same auth patterns as the core WebSocket infrastructure
