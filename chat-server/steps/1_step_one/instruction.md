# Chat Server Core Communication

We need a production-grade chat server for team collaboration. Build core chat communication functionality in Go with durable persistence and integrity.

Data directory `/app/data/` writable, default persistence `/app/data/chat.json`.

## Task – Implement Go Chat Server at `/app/` (module `chat-server`), built via `go build -o <binary> .`

Stdlib only: `go list -f '{{join .Imports " "}}' .` must contain no dotted imports (only stdlib). `go.mod` must have no external require.

### CLI

Global flags: `--data` default `/app/data/chat.json`, `--messages-per-second <float>` default 5, `--burst <int>` default 10. Flags must be accepted in both `--flag value` and `--flag=value` form. Non-numeric or non-positive values for rate/burst → exit2.

Help: bare binary no args must print help containing keywords `create-room`, `delete-room`, `purge`, `list-rooms`, `join`, `leave`, `list-users`, `send`, `get-messages`, `send-private`, `get-private`, `data`, `checksum` exit0. `--help`, `-h`, `help` also help exit0. Unknown command → exit2, missing required args → exit2, empty roomID or userID (after TrimSpace) → exit2, invalid limit → exit2, missing message → exit2.

Commands:
```
create-room <roomID>              -> idempotent exit0, fail exit2 if empty after TrimSpace
delete-room <roomID>              -> tombstone: retains history, prints true/false, exit0 even if not exist
purge <roomID>                    -> removes tombstone, prints true/false, exit2 if no tombstone exists
list-rooms                        -> JSON array sorted lexicographically
join <roomID> <userID>            -> idempotent exit0, fail exit2 if room not exist or empty args after TrimSpace, tracks seen_users
leave <roomID> <userID>           -> idempotent exit0 even if room/user not exist
list-users <roomID>               -> JSON array sorted, exit2 if room not exist
send <roomID> <userID> <message>  -> sends message, user must be member else exit2, prints JSON, message via strings.Join remaining args (requires message else exit2), special chars <>& must not be HTML-escaped in file or output, Unicode preservation required, large messages must be handled
get-messages <roomID> [limit]     -> JSON array oldest first sorted by id asc, limit optional integer ≥0, 0/omit=all, if limit given returns latest N (suffix), if room not exist returns [] exit0, invalid limit exit2, limit zero returns all
send-private <from> <to> <msg>    -> sends private message via Join, requires message else exit2, tracks seen users for both parties, special chars and Unicode preserved
get-private <u1> <u2> [limit]     -> JSON array private messages both directions sorted asc by id, limit latest N, invalid limit exit2, limit zero returns all
list-all-users                    -> JSON array sorted unique ever seen
```

Message JSON:
- Room: `{"id": int64, "room_id": "general", "from": "alice", "content": "hi", "timestamp": int64}`
  Timestamp is `time.Now().UnixNano()`.
- Private: `{"id": int64, "from": "alice", "to": "bob", "content": "hi", "timestamp": int64}`

IDs: globally incrementing int64 starting at 1, unique across room and private messages, monotonic interleaved. Example: room1, priv1, room2, priv2 → ids 1,2,3,4. `next_id` persists across restarts and is not reset on delete; only after corruption recovery it resets to 1. The next send after many ops must have id == previous next_id.

### Persistence with Integrity – Split Files (Realistic Layout)

To mirror production sharded layout, persistence is split across four files in the same directory as `--data`, each with its own wrapper `{"data": <Data>, "checksum": "<md5 hex>"}` where checksum is MD5 of canonical JSON `json.dumps(data, sort_keys=True, separators=(',',':'))` with no HTML escaping. In Go, `json.Encoder.SetEscapeHTML(false)` must be used both for checksum computation and file write, so raw files must contain literal "<" and emoji, not `\u003c`.

Derived paths: if `--data` is `/app/data/chat.json`, then private messages are at `/app/data/private.json` (same dir, basename `private.json`), counter at `/app/data/counter.json`, and rate limiting at `/app/data/rate_limit.json`. Custom `--data` paths use their directory.

File formats:

- **chat.json** (`--data`): `Data = {"rooms": {roomID: {users: []string, messages: []Message}}, "deleted_rooms": {roomID: {users: []string, messages: []Message, deleted_at: int64}}, "seen_users": {userID: bool}}`
  - Room object: `users` sorted array, `messages` sorted by id asc
- **private.json**: `Data = {"private_messages": []Message}`
- **counter.json**: `Data = {"next_id": int64}` starting at 1, globally monotonic across room and private messages
- **rate_limit.json**: `Data = {"<userID>": {"tokens": float, "last_refill": int64}}`, empty object `{}` when no user has sent yet. See Rate Limiting section.

All four files must use wrapper checksum, atomic write via `os.CreateTemp` in same directory + `os.Rename`, plus file locking. A global lock file `/app/data/global.lock` (in data directory) must be used for any operation touching multiple files (send, send-private, delete-room, purge) to keep ordering and avoid races; per-file `.lock` files are also allowed but global lock is mandatory for multi-file atomicity. The lock is acquired by creating it with O_CREATE|O_EXCL; if it already exists the command retries and ultimately fails rather than proceeding. If the lock cannot be acquired within 3 seconds the command gives up and exits nonzero; it must not block indefinitely. Lock files must be cleaned after each command (must not remain) and no `tmp-*.json` residue must remain after a burst.

On read per file:
- Missing file → empty data: chat → `{"rooms":{},"deleted_rooms":{},"seen_users":{}}`, private → `{"private_messages":[]}`, counter → `{"next_id":1}`, rate_limit → `{}`
- Empty file (TrimSpace empty) → empty data (same as missing)
- Wrapper missing `checksum`, empty checksum, checksum mismatch, or invalid JSON → corruption: backup to `<original>.corrupt.<nanosec>` integer `UnixNano()`, stderr warning containing "corrupt" or "checksum", recreate empty valid wrapper

### Rate Limiting

Global flags: `--messages-per-second <float>` (default 5) and `--burst <int>` (default 10). Both must be accepted in `--flag value` and `--flag=value` form. Non-numeric or non-positive values → exit2.

Token bucket per user, single bucket shared across all message sends (`send` and `send-private` share the same quota per user): if a user is rate-limited for `send`, their `send-private` is also rate-limited.

- State: per user `tokens = burst` initially, `last_refill = now nano`
- Refill: `elapsed = max(0, (now - last_refill)/1e9)` seconds, `tokens = min(burst, tokens + elapsed*rate)`, update `last_refill = now`
- Consume: if `tokens >= 1`, `tokens -= 1`, allow and persist; else fail rate-limited and persist the refilled tokens
- Per-user independent (bob succeeds when alice is limited)
- Persistence: `rate_limit.json`, in the same directory as `--data`, with the same `{"data": ..., "checksum": ...}` wrapper, atomic CreateTemp+Rename, and the same corruption handling as the other files. Corruption → reset the bucket, so the next send succeeds.
- `Data = {"<userID>": {"tokens": float, "last_refill": int64}}`, empty object `{}` when no user has sent yet
- Exit semantics: if rate-limited, exit code 1, stderr contains case-insensitive "rate limit", no stdout, and the global `next_id` counter must NOT be incremented
- `--messages-per-second` may be fractional (e.g. 0.05 means 1 token per 20s), so use float64
- A token is consumed only when a message is actually appended. All exit-2 validation for a send (blank IDs, missing message, non-member, nonexistent or tombstoned room) is decided before the bucket is read, and a rejected send leaves `rate_limit.json` byte-identical.

### Tombstone deletion

delete-room retains history: the room's members and messages move to deleted_rooms under the room ID with a deleted_at nanosecond timestamp. A tombstoned room behaves as though it does not exist for every command except list-all-users, which still reports everyone who was ever a member. Re-creating a room with same ID after deletion starts empty and does NOT clear existing tombstone. purge <roomID> is the only way to remove a tombstone. deleted_rooms participates in wrapper checksum and corruption recovery like any other field.

Concurrency: The files must never be observed as invalid JSON during concurrent operations. Parallel sends to same room and different rooms, as well as parallel joins, must preserve every message and user with unique IDs. File locks must be cleaned up after each operation, and no temporary files may remain.

Edge handling:
- Identifier validation happens before any existence check or idempotent handling: if a roomID or userID argument is empty after strings.TrimSpace, the command exits 2 without reading or writing state, including for otherwise-idempotent commands (delete-room, leave, purge) and read commands (list-users, get-messages, get-private).
- Empty roomID or userID after TrimSpace → exit2 (see precedence above)
- `leave` idempotent exit0 even if room/user not exist; after leaving all users, `list-users` returns `[]`; `send` after leave fails exit2
- `get-messages` for nonexistent room returns `[]` exit0, not error
- Invalid limit (non-integer, negative) → exit2 for both `get-messages` and `get-private`
- Limit zero means return all
- Message content must be obtained via `strings.Join(remainingArgs, " ")` to support spaces
- Must preserve `<>&` without HTML escaping and preserve Unicode emoji and newlines/tabs
- Must handle large messages (10KB) and large histories efficiently (<2s for 1000 messages)
- Every command that prints a JSON array prints `[]` when the result is empty, never `null`. Every map-valued field in a persisted file serializes as `{}` when empty and every array-valued field as `[]`, never `null`.

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
