# Chat Server Core Communication

We need a production-grade chat server for team collaboration. Build core chat communication functionality in Go with durable persistence and integrity.

Data directory `/app/data/` writable, default persistence `/app/data/chat.json`.

## Task – Implement Go Chat Server at `/app/` (module `chat-server`), built via `go build -o <binary> .`

Stdlib only: `go list -f '{{join .Imports " "}}' .` must contain no dotted imports (only stdlib). `go.mod` must have no external require.

### CLI

Global flag: `--data` default `/app/data/chat.json`

Help: bare binary no args must print help containing keywords `create-room`, `delete-room`, `purge`, `list-rooms`, `join`, `leave`, `list-users`, `send`, `get-messages`, `send-private`, `get-private`, `data`, `checksum` exit0. `--help`, `-h`, `help` also help exit0. Unknown command → exit2, missing required args → exit2, empty roomID or userID (after TrimSpace) → exit2, invalid limit → exit2, missing message → exit2.

Commands:
```
create-room <roomID>              -> idempotent exit0, fail exit2 if empty after TrimSpace
delete-room <roomID>              -> tombstone: moves room's messages and member list into deleted_rooms object in the store, keyed by room ID, with deletion timestamp (time.Now().UnixNano()). Prints true/false, exit0 even if not exist, does not clear seen_users nor reset next_id except after corruption recovery
purge <roomID>                    -> removes tombstone for room, prints true/false, exit2 if no tombstone exists, exit0 otherwise
list-rooms                        -> JSON array sorted lexicographically, omits deleted rooms
join <roomID> <userID>            -> idempotent exit0, fail exit2 if room not exist or empty args after TrimSpace, tracks seen_users, join fails exit2 if room is deleted
leave <roomID> <userID>           -> idempotent exit0 even if room/user not exist, leave on deleted room is idempotent exit0 (room stays deleted)
list-users <roomID>               -> JSON array sorted, exit2 if room not exist or deleted
send <roomID> <userID> <message>  -> sends message, user must be member else exit2, prints JSON, message via strings.Join remaining args (requires message else exit2), special chars <>& must not be HTML-escaped in file or output, Unicode preservation required, large messages must be handled, cannot send to deleted room (exit2)
get-messages <roomID> [limit]     -> JSON array oldest first sorted by id asc, limit optional integer ≥0, 0/omit=all (return all), if limit given returns latest N (suffix), if room not exist or deleted returns [] exit0, invalid limit exit2, limit zero returns all
send-private <from> <to> <msg>    -> sends private message via Join, requires message else exit2, tracks seen users for both parties, special chars and Unicode preserved
get-private <u1> <u2> [limit]     -> JSON array private messages both directions sorted asc by id, limit latest N, invalid limit exit2, limit zero returns all
list-all-users                    -> JSON array sorted unique ever seen even after room deletion and even if room is in tombstone
```

Message JSON:
- Room: `{"id": int64, "room_id": "general", "from": "alice", "content": "hi", "timestamp": int64}`
  Timestamp is `time.Now().UnixNano()`.
- Private: `{"id": int64, "from": "alice", "to": "bob", "content": "hi", "timestamp": int64}`

IDs: globally incrementing int64 starting at 1, unique across room and private messages, monotonic interleaved. Example: room1, priv1, room2, priv2 → ids 1,2,3,4. `next_id` persists across restarts and is not reset on delete; only after corruption recovery it resets to 1. The next send after many ops must have id == previous next_id.

### Persistence with Integrity – Split Files (Realistic Layout)

To mirror production sharded layout, persistence is split across three files in the same directory as `--data`, each with its own wrapper `{"data": <Data>, "checksum": "<md5 hex>"}` where checksum is MD5 of canonical JSON `json.dumps(data, sort_keys=True, separators=(',',':'))` with no HTML escaping. In Go, `json.Encoder.SetEscapeHTML(false)` must be used both for checksum computation and file write, so raw files must contain literal "<" and emoji, not `\u003c`.

Derived paths: if `--data` is `/app/data/chat.json`, then private messages are at `/app/data/private.json` (same dir, basename `private.json`) and counter at `/app/data/counter.json`. Custom `--data` paths use their directory.

File formats:

- **chat.json** (`--data`): `Data = {"rooms": {roomID: {users: []string, messages: []Message}}, "deleted_rooms": {roomID: {users: []string, messages: []Message, deleted_at: int64}}, "seen_users": {userID: bool}}`
  - Room object: `users` sorted array, `messages` sorted by id asc
  - Deleted room tombstone: `users` array (original members at deletion), `messages` array (original messages at deletion), `deleted_at` timestamp `time.Now().UnixNano()`
  - Re-creating a room with same ID after deletion starts empty and does NOT clear existing tombstone
- **private.json**: `Data = {"private_messages": []Message}`
- **counter.json**: `Data = {"next_id": int64}` starting at 1, globally monotonic across room and private messages

All three files must use wrapper checksum, atomic write via `os.CreateTemp` in same directory + `os.Rename`, plus file locking. A global lock file `/app/data/global.lock` (in data directory) must be used for any operation touching multiple files (send, send-private, delete-room, purge) to keep ordering and avoid races; per-file `.lock` files are also allowed but global lock is mandatory for multi-file atomicity. Lock files must be cleaned after each command (must not remain) and no `tmp-*.json` residue must remain after a burst.

On read per file:
- Missing file → empty data: chat → `{"rooms":{},"deleted_rooms":{},"seen_users":{}}`, private → `{"private_messages":[]}`, counter → `{"next_id":1}`
- Empty file (TrimSpace empty) → empty data (same as missing)
- Wrapper missing `checksum`, empty checksum, checksum mismatch, or invalid JSON → corruption: backup to `<original>.corrupt.<nanosec>` integer `UnixNano()`, stderr warning containing "corrupt" or "checksum", recreate empty valid wrapper

IDs: globally incrementing int64 starting at 1, unique across room and private, monotonic interleaved. `next_id` in counter.json persists across restarts, not reset on delete or purge, only after corruption recovery resets to 1. Deleting a room does not discard history – it moves to `deleted_rooms` with timestamp, and `deleted_rooms` participates in wrapper checksum and corruption recovery like every other field.

Tombstone semantics:
- `delete-room <roomID>`: moves room's messages and member list into `deleted_rooms` keyed by roomID with `deleted_at` timestamp. Prints `true` if room existed and was moved, `false` if not exist. After deletion: `list-rooms` omits room, `get-messages` returns `[]` exit0, `list-users` exits 2, `join` and `send` fail exit2, but `list-all-users` still includes everyone ever member, and `next_id` unaffected.
- `purge <roomID>`: removes tombstone for room, prints true/false, exits 2 if no tombstone exists.
- Re-creating room with same ID after deletion starts empty and does NOT clear existing tombstone.

Concurrency: The files must never be observed as invalid JSON during concurrent operations. Parallel sends to same room and different rooms, as well as parallel joins, must preserve every message and user with unique IDs. File locks must be cleaned up after each operation, and no temporary files may remain.

Edge handling:
- Empty roomID or userID after TrimSpace → exit2
- `join` fails exit2 if room does not exist or is deleted (tombstoned), otherwise idempotent
- `leave` idempotent exit0 even if room/user not exist or room deleted; after leaving all users, `list-users` returns `[]`; `send` after leave fails exit2
- `delete-room` is tombstone: prints `true` if room existed and moved to deleted_rooms, `false` if not exist; after delete, `list-rooms` omits, `get-messages` returns `[]`, `list-users` exits 2, `join`/`send` fail exit2. Re-creating same ID starts empty, does NOT clear tombstone.
- `purge` removes tombstone, prints true/false, exits 2 if no tombstone exists, exit0 otherwise
- `get-messages` for nonexistent or deleted room returns `[]` exit0, not error
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
