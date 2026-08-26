# Chat Server Core Communication

We need a production-grade chat server for team collaboration. Build core chat communication functionality in Go with durable persistence and integrity.

Data directory `/app/data/` writable, default persistence `/app/data/chat.json`.

## Task – Implement Go Chat Server at `/app/` (module `chat-server`), built via `go build -o <binary> .`

Stdlib only: `go list -f '{{join .Imports " "}}' .` must contain no dotted imports (only stdlib). `go.mod` must have no external require.

### CLI

Global flag: `--data` default `/app/data/chat.json`

Help: bare binary no args must print help containing keywords `create-room`, `delete-room`, `list-rooms`, `join`, `leave`, `list-users`, `send`, `get-messages`, `send-private`, `get-private`, `data`, `checksum` exit0. `--help`, `-h`, `help` also help exit0. Unknown command → exit2, missing required args → exit2, empty roomID or userID (after TrimSpace) → exit2, invalid limit → exit2, missing message → exit2.

Commands:
```
create-room <roomID>              -> idempotent exit0, fail exit2 if empty after TrimSpace
delete-room <roomID>              -> prints true/false, removes room and its messages, exit0 even if not exist, does not clear seen_users nor reset next_id except after corruption recovery
list-rooms                        -> JSON array sorted lexicographically
join <roomID> <userID>            -> idempotent exit0, fail exit2 if room not exist or empty args after TrimSpace, tracks seen_users
leave <roomID> <userID>           -> idempotent exit0 even if room/user not exist
list-users <roomID>               -> JSON array sorted, exit2 if room not exist
send <roomID> <userID> <message>  -> sends message, user must be member else exit2, prints JSON, message via strings.Join remaining args (requires message else exit2), special chars <>& must not be HTML-escaped in file or output, Unicode preservation required, large messages must be handled
get-messages <roomID> [limit]     -> JSON array oldest first sorted by id asc, limit optional integer ≥0, 0/omit=all (return all), if limit given returns latest N (suffix), if room not exist returns [] exit0, invalid limit exit2, limit zero returns all
send-private <from> <to> <msg>    -> sends private message via Join, requires message else exit2, tracks seen users for both parties, special chars and Unicode preserved
get-private <u1> <u2> [limit]     -> JSON array private messages both directions sorted asc by id, limit latest N, invalid limit exit2, limit zero returns all
list-all-users                    -> JSON array sorted unique ever seen even after room deletion
```

Message JSON:
- Room: `{"id": int64, "room_id": "general", "from": "alice", "content": "hi", "timestamp": int64}`
  Timestamp is `time.Now().UnixNano()`.
- Private: `{"id": int64, "from": "alice", "to": "bob", "content": "hi", "timestamp": int64}`

IDs: globally incrementing int64 starting at 1, unique across room and private messages, monotonic interleaved. Example: room1, priv1, room2, priv2 → ids 1,2,3,4. `next_id` persists across restarts and is not reset on delete; only after corruption recovery it resets to 1. The next send after many ops must have id == previous next_id.

### Persistence with Integrity – Explicit File Format

File at `--data` must use wrapper `{"data": <StoreData>, "checksum": "<md5 hex>"}` where checksum is MD5 of canonical JSON: `json.dumps(data, sort_keys=True, separators=(',',':'))` with no HTML escaping. In Go, `json.Encoder.SetEscapeHTML(false)` must be used both for checksum computation and for file write, so raw file must contain literal "<" and emoji characters, not `\u003c` or escaped unicode.

StoreData shape (keys sorted for checksum, Go struct tags must match):
```json
{
  "next_id": 1,
  "private_messages": [],
  "rooms": {
    "general": {
      "users": ["alice", "bob"],
      "messages": [ { "id":1, "room_id":"general", ... } ]
    }
  },
  "seen_users": { "alice": true, "bob": true }
}
```
- `next_id`: int64
- `private_messages`: array of private Message objects
- `rooms`: map from roomID string to object:
  - `users`: JSON array of strings, sorted and deduplicated
  - `messages`: JSON array of room messages, sorted by id asc
- `seen_users`: map/object from userID to true (presence of key indicates user has been seen via join or private message). Must persist even after room deletion.

On write: atomic via `os.CreateTemp` in same directory + `os.Rename` plus file lock `<data>.lock` using `O_CREATE|O_EXCL` retry loop 5ms, 2000 tries, with cleanup after each command (lock file must not remain). Global ordering must be preserved under concurrent execution.

On read:
- Missing file → empty store `{"next_id":1,"private_messages":[],"rooms":{},"seen_users":{}}`
- Empty file (TrimSpace empty) → empty store
- Wrapper missing `checksum`, or checksum empty, or checksum mismatch, or invalid JSON → corruption recovery:
  - Backup original file to `<original>.corrupt.<nanosec>` where `<nanosec>` is integer from `time.Now().UnixNano()` (must be digits only)
  - Write to stderr a warning containing case-insensitive "corrupt" or "checksum"
  - Recreate empty valid wrapper with correct checksum

Concurrency: The file must never be observed as invalid JSON during concurrent operations. Parallel sends to the same room and to different rooms, as well as parallel joins, must preserve every message and user with unique IDs. The file lock must be cleaned up after each operation.

Edge handling:
- Empty roomID or userID after TrimSpace → exit2
- `join` fails exit2 if room does not exist, otherwise idempotent
- `leave` idempotent exit0 even if room or user does not exist; after leaving all users, `list-users` returns `[]`; `send` after leave fails exit2
- `delete-room` prints `true` if deleted, `false` if not exist; after delete, `join` fails exit2
- `get-messages` for nonexistent room returns `[]` exit0, not error
- Invalid limit (non-integer, negative) → exit2 for both `get-messages` and `get-private`
- Limit zero means return all
- Message content must be obtained via `strings.Join(remainingArgs, " ")` to support spaces
- Must preserve `<>&` without HTML escaping and preserve Unicode emoji and newlines/tabs
- Must handle large messages (10KB) and large histories efficiently (<2s for 1000 messages)

### Exit Codes
0 success, 1 I/O error, 2 invalid input. `leave` and `delete-room` for nonexistent targets still exit0 (with appropriate output), but `join` and `send` fail with exit2 for invalid state.

### Constraints
- Go stdlib only, build via `go build -o <binary> .`
- Atomic CreateTemp+Rename + file lock + cleanup required for correctness under concurrency
- Use `/tmp/codimango` for temp if needed
- Respect `--data` flag default

### Examples
```bash
go build -o ./chat-server .
./chat-server --data /app/data/chat.json create-room general
./chat-server --data /app/data/chat.json join general alice
./chat-server --data /app/data/chat.json send general alice Hello World with spaces
./chat-server --data /app/data/chat.json get-messages general
./chat-server --data /app/data/chat.json send-private alice bob secret with spaces
./chat-server --data /app/data/chat.json get-private alice bob
./chat-server --data /app/data/chat.json list-rooms
./chat-server --data /app/data/chat.json list-users general
./chat-server --help
```
