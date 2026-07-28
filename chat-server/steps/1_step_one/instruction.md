# Turn 1: Chat Server Core Communication (Go)

## Background

We need a production-grade chat server for team collaboration. The current prototype loses data on restart and has no persistence. Build the core chat communication functionality in Go.

Data directory: `/app/data/` is writable, binary should default to `/app/data/chat.json`. It must survive restarts.

## Task – Implement Go Chat Server at `/app/` (module `chat-server`), built via `go build -o <binary> .`

Stdlib only: `go.mod` must have no external require (check via `go list -f '{{join .Imports " "}}' .` should contain no dotted imports). Must build.

### CLI (MUST)

Global flags:
- `--data` default `/app/data/chat.json` – path to persistence file

Help explicitly required:
- Bare binary with no args (`chat-server`) must print help to stdout containing keywords: `create-room`, `delete-room`, `list-rooms`, `join`, `leave`, `list-users`, `send`, `get-messages`, `send-private`, `get-private`, `data`, `checksum` and exit 0.
- `--help`, `-h`, `help` must also print same help and exit 0.
- Unknown command → exit 2.
- Missing required args → exit 2.

Commands:

```
chat-server --data /app/data/chat.json create-room <roomID>         -> exit 0, creates room if not exists (idempotent)
chat-server --data /app/data/chat.json delete-room <roomID>         -> prints true/false, removes room and its messages
chat-server --data /app/data/chat.json list-rooms                    -> JSON array sorted
chat-server --data /app/data/chat.json join <roomID> <userID>        -> exit 0, idempotent, auto-creates room if not exists? NO, must fail if room not exist with exit 2
chat-server --data /app/data/chat.json leave <roomID> <userID>       -> exit 0
chat-server --data /app/data/chat.json list-users <roomID>           -> JSON array sorted of users in room
chat-server --data /app/data/chat.json send <roomID> <userID> <message>  -> sends message to room, user must be member else exit 2; prints JSON message object
chat-server --data /app/data/chat.json get-messages <roomID> [limit] -> JSON array of messages in order (oldest first), limit optional integer >=0, 0 or omitted = all
chat-server --data /app/data/chat.json send-private <fromUser> <toUser> <message> -> sends private message, prints JSON
chat-server --data /app/data/chat.json get-private <user1> <user2> [limit] -> JSON array of private messages between two users (either direction), oldest first
chat-server --data /app/data/chat.json list-all-users               -> JSON array sorted unique users seen (from joins or private)
```

### Data Model

Message JSON shape (both room and private):
```json
{
  "id": 1,
  "room_id": "general" ,   // for room messages, null or omitted for private? Use "room_id" for room, keep for private null
  "from": "alice",
  "to": "bob",             // only for private, empty for room
  "content": "hello",
  "timestamp": 1234567890123456789  // unix nano
}
```
- Room message: must have `room_id`, `from`, `content`, `id`, `timestamp`. `to` empty or omitted.
- Private message: must have `from`, `to`, `content`, `id`, `timestamp`. `room_id` empty.

- IDs: globally incrementing int64, starting at 1, unique across both room and private messages. Persists across restarts via file.
- Timestamp: `time.Now().UnixNano()` at send time.

### Persistence with Integrity (HARD)

File format (new):
```json
{
  "data": {
    "rooms": {
      "general": {
        "users": ["alice"],
        "messages": [ { ... }, ... ]
      }
    },
    "private_messages": [ ... ],
    "next_id": 2
  },
  "checksum": "hex md5 of canonical JSON of data without HTML escaping"
}
```
- Canonical data JSON: sorted keys, no spaces, without HTML escaping: Python `json.dumps(data, sort_keys=True, separators=(',', ':'))`. Go must use `json.Encoder.SetEscapeHTML(false)` for checksum. Test includes `<>&` in message content.
- On write: atomic via `os.CreateTemp` in same dir + `os.Rename`. Source inspection will check for `CreateTemp` and `Rename`.
- On read/init: `NewServer` must validate file before any command:
  - Missing → empty store `rooms={}, private_messages=[], next_id=1`
  - Empty file → empty store
  - Has `data` field: require `checksum` present non-empty else corruption. Compute expected checksum from `data` canonical. Mismatch → corruption: backup to `<path>.corrupt.<nanosec>` + stderr warning containing "corrupt" or "checksum", recreate empty with valid checksum and empty data.
  - No `data` field → old flat format: try to interpret as data directly (old format without checksum). If valid with rooms field, migrate to new format on next write. If invalid JSON → corruption handling backup/recreate.
- Must not escape HTML in persisted file's data canonical for checksum. Use `SetEscapeHTML(false)` in Go.

### Business Rules

- `create-room` idempotent: if exists, still success.
- `join` fails with exit 2 if room not exist.
- `join` idempotent: duplicate join same user same room still success, list-users deduped.
- `send` to room: user must be member else exit 2 stderr, no stdout.
- `get-messages`: sorted by id asc (which equals time order). If room not exist → empty array `[]`? Actually should be exit 2? For simplicity: if room not exist, return `[]` and exit 0 (tests expect empty for non-existent). However `list-users` for non-existent room should exit 2.
- `list-rooms` sorted ascending.
- `list-users` sorted ascending, exit 2 if room not exist.
- `send-private`: always allowed, even if users never joined any room. Auto-track users in global list.
- `get-private`: messages where (from=user1,to=user2) or (from=user2,to=user1), sorted asc by id.
- `list-all-users`: sorted unique users who ever joined any room or sent private (track explicitly). Store in file as derived from rooms users + private participants + maybe explicit? Simplest: compute union on the fly from rooms users plus private from/to, but also need to include users who joined then left? Keep them in seen set. Store `seen_users` map persisting. For MVP, union of room users (current) + private participants is acceptable, but requirement says unique users seen ever. So need to persist seen set even after leave. Implement `seen_users` set that accumulates.

- Concurrency: single process CLI, but file writes must handle concurrent CLI invocations from tests (python spawning multiple processes). Use file locking: attempt advisory lock via creating lock file `<data>.lock` with `os.Create` + `O_EXCL` retry loop? Simpler: use `syscall.Flock` if possible, but stdlib way: implement retry loop with temp file and rename is atomic, but read-modify-write race still possible. Mitigate via lock file with retries: before reading, create lock file with `O_CREATE|O_EXCL`, if exists, sleep 10ms retry up to 500 tries. Remove lock after write. This prevents concurrent corruption. Tests will spawn concurrent senders.

### Exit Codes
- 0 success (including help, empty lists)
- 1 I/O error
- 2 invalid input (bad args, room not exist for join, user not member for send, unknown command, missing id)

### Constraints
- Go stdlib only, builds via `go build -o <binary> .`
- Must handle special chars `<>&` without escaping corruption (checksum must match python canonical)
- Source must contain `CreateTemp` and `Rename` for atomic write
- No hardcoded `/tmp/chat`, use `/tmp/codimango` if temp needed
- Respect `--data` flag, default `/app/data/chat.json`
- Must be deterministic, messages ordered by id

### Examples

```bash
go build -o ./chat-server .

./chat-server --data /app/data/chat.json create-room general
./chat-server --data /app/data/chat.json join general alice
./chat-server --data /app/data/chat.json join general bob
./chat-server --data /app/data/chat.json send general alice "Hello world"
./chat-server --data /app/data/chat.json get-messages general
# -> [{"id":1,"room_id":"general","from":"alice","content":"Hello world","timestamp":...}]

./chat-server --data /app/data/chat.json send-private alice bob "secret hi"
./chat-server --data /app/data/chat.json get-private alice bob
# -> [{"id":2,"from":"alice","to":"bob","content":"secret hi",...}]

./chat-server --data /app/data/chat.json list-rooms
# -> ["general"]
./chat-server --data /app/data/chat.json list-users general
# -> ["alice","bob"]

./chat-server --help
# contains all keywords
```

Implement at `/app` – Turn 1.

### Success Criteria
- Binary builds, help works, bare help works
- Rooms, joins, leaves, messages work, private messages work, persistence across invocations
- Sorted lists, validation, error codes
- Checksum integrity, atomic writes, corruption backup
- Special chars `<>&` round-trip and checksum valid
- Concurrent sends don't corrupt file
