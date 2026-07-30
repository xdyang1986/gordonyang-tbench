# Turn 1: Chat Server Core Communication (Go) – Hard

## Background

We need a production-grade chat server for team collaboration. Build core chat communication functionality in Go with durable persistence and integrity. This turn is deliberately hard: checksum canonicalization, atomic multi-process safety, global ID monotonicity, and message content with spaces.

Data directory `/app/data/` writable, default persistence `/app/data/chat.json`.

## Background

We need a production-grade chat server for team collaboration. Build core chat communication functionality in Go with durable persistence and integrity.

Data directory `/app/data/` writable, default persistence `/app/data/chat.json`.

## Task – Implement Go Chat Server at `/app/` (module `chat-server`), built via `go build -o <binary> .`

Stdlib only: `go list -f '{{join .Imports " "}}' .` must contain no dotted imports (only stdlib). `go.mod` must have no external require.

### CLI (MUST)

Global: `--data` default `/app/data/chat.json`

Help:
- Bare binary no args must print help to stdout containing keywords `create-room`, `delete-room`, `list-rooms`, `join`, `leave`, `list-users`, `send`, `get-messages`, `send-private`, `get-private`, `data`, `checksum` and exit 0
- `--help`, `-h`, `help` also help exit 0
- Unknown command → exit 2, missing required args → exit 2

Commands:
```
create-room <roomID>              -> idempotent exit 0, creates room if not exists
delete-room <roomID>              -> prints true/false, removes room and its messages, exit 0 even if not exist
list-rooms                        -> JSON array sorted of room IDs
join <roomID> <userID>            -> idempotent exit 0, fail if room not exist exit 2
leave <roomID> <userID>           -> idempotent exit 0 even if room/user not exist
list-users <roomID>               -> JSON array sorted of users in room, exit 2 if room not exist
send <roomID> <userID> <message>  -> sends message to room, user must be member else exit 2, prints JSON message object
get-messages <roomID> [limit]     -> JSON array messages in room, oldest first (sorted by id asc), limit optional integer >=0, 0 or omitted = all, if limit given returns latest N
send-private <from> <to> <msg>    -> sends private message, prints JSON
get-private <u1> <u2> [limit]     -> JSON array private messages between two users either direction, oldest first, limit optional latest N
list-all-users                    -> JSON array sorted unique users seen ever (from joins or private)
```

Message JSON (room):
```json
{"id":1,"room_id":"general","from":"alice","content":"hi","timestamp":1234567890123}
```
Private: `{"id":2,"from":"alice","to":"bob","content":"hi","timestamp":...}`

- IDs globally incrementing int64 starting at 1, unique across both room and private, persists across restarts
- Timestamp `time.Now().UnixNano()`

### Persistence with Integrity – Explicit File Format (MUST)

File at `--data` path must use **either** simple flat JSON **or** wrapper with checksum. For **hard** integrity, reference solution uses wrapper format – tests accept both but one test requires wrapper to ensure integrity is implementable.

**File Format Simple (accepted for Turn1 functional tests):**
```json
{
  "rooms": {
    "general": {
      "users": ["alice"],
      "messages": [{"id":1,"room_id":"general","from":"alice","content":"hi","timestamp":...}]
    }
  },
  "private_messages": [{"id":2,"from":"alice","to":"bob","content":"hi","timestamp":...}],
  "next_id": 3,
  "seen_users": {"alice": true, "bob": true}
}
```
- Explicitly required schema: top-level must have `rooms` map, where `rooms[roomID]` must have `users` array and `messages` array. Must have `private_messages` array, `next_id` integer, `seen_users` map/set. If you use flat format, these keys must exist. If you use wrapper format, they are inside `data` field.

**File Format Wrapper with Checksum (for integrity tests, recommended):**
```json
{
  "data": {
    "rooms": {...},
    "private_messages": [...],
    "next_id": 3,
    "seen_users": {...}
  },
  "checksum": "hex md5 of canonical JSON of data without HTML escaping"
}
```
- Canonical data JSON: `json.dumps(data, sort_keys=True, separators=(',', ':'))` with no HTML escaping – Go must use `json.Encoder.SetEscapeHTML(false)` for checksum calculation and file write. Test includes `<>&` in message content to ensure no escaping, for both room and private messages.
- On write: atomic via `os.CreateTemp` in same dir + `os.Rename` plus file lock `<data>.lock` with `O_CREATE|O_EXCL` retry loop (5ms sleep, 2000 tries). Behavioral hard check but solvable: during 10 concurrent `send` processes to same room, file must never become invalid JSON and must preserve most successful sends – at least **8 messages** after 10 concurrent sends (hard but solvable; reference gets 10), IDs unique, no partial writes. Lock file must be cleaned up after each command.
- Message content with spaces: CLI receives `<message>` as remaining args. Implementation must use `strings.Join(remainingArgs, " ")` to support `send general alice Hello World with spaces` where message contains spaces without quoting. Tests will invoke binary with multiple args – hard but essential.
- Global ID uniqueness: IDs globally incrementing int64 starting at 1, unique across room+private, monotonic, persists across restarts and interleaved sends. next_id must not reset on delete.
- Large history: must handle 500 messages efficiently, pagination via limit (latest N semantics) and performance (<2s). Offset is Turn2 feature, not required in Turn1.
- Concurrent different rooms: 10 parallel sends to 10 different rooms must not corrupt file, total at least 8 msgs preserved, IDs unique.
- SeenUsers persistence: `list-all-users` persists even after room deletion; delete does not clear seen_users or reset next_id.
- On read: validation before any command:
  - Missing file → empty store `rooms={}, private_messages=[], next_id=1, seen_users={}`
  - Empty file → empty store
  - If file has `data` field (wrapped):
    - If `checksum` missing or empty → treat as corruption: backup to `<original_path>.corrupt.<nanosec>` where `<nanosec>` is `time.Now().UnixNano()` integer, stderr warning containing "corrupt" or "checksum" (case-insensitive), recreate empty valid file with correct checksum
    - If `checksum` present: compute expected MD5 of canonical data JSON, mismatch → corruption handling as above (backup, warning, recreate empty)
  - If file has no `data` field (flat): try to parse as `Data` directly. If valid with `rooms` key, accept (for Turn1 ease). If invalid JSON → corruption handling: backup to `<path>.corrupt.<nanosec>` with integer nanosec, stderr warning containing "corrupt", recreate empty valid.

### Business Rules
- `create-room` idempotent exit 0
- `delete-room` idempotent exit 0 prints true/false
- `join` fails exit 2 if room not exist, idempotent else
- `leave` idempotent exit 0 even if room/user not exist
- `send`: user must be member else exit 2
- `get-messages`: sorted id asc, if room not exist return `[]` exit 0, limit returns latest N (oldest-first order preserved, but only last N when limit)
- `list-rooms`, `list-users`, `list-all-users` sorted
- `send-private` always allowed, tracks users
- `get-private` both directions sorted asc
- `list-all-users` union ever seen (rooms users current + private participants + seen_users persisted)

### Integrity Coverage (Turn1 will test – hard but solvable)
- `test_checksum_integrity`: strict wrapper with data+checksum, checksum matches canonical Python sort_keys+separators, Go SetEscapeHTML(false). Checks both room and private special chars.
- `test_checksum_mismatch_handling`: wrong checksum → backup <original>.corrupt.<nanosec> integer, stderr warning "corrupt"/"checksum", recreated empty valid wrapper.
- `test_missing_checksum_handling`: missing/empty checksum → same.
- `test_invalid_json_backup_naming`: invalid JSON → backup naming integer nanosec, recreated valid.
- `test_stderr_warnings`: corruption stderr warning "corrupt".
- `test_atomic_behavior_concurrent`: hard but solvable – 10 concurrent sends same room, file never invalid JSON, must preserve at least 9 messages (reference gets 10), IDs unique, lock cleaned.
- `test_atomic_write_advisory`: source-string advisory – behavioral test reward-critical.
- `test_stdlib_only`: no external imports.
- `test_send_with_spaces_via_join`: multiple args joined to single message (hard CLI detail).
- `test_global_id_uniqueness_interleaved`: interleaved room+private globally monotonic IDs.
- `test_large_history_and_pagination_performance`: 500 msgs, limit latest N semantics (Turn1), <2s, not offset.
- `test_concurrent_different_rooms`: 10 parallel different rooms, each ≥1 msg, total ≥9, no corruption, IDs unique.
- `test_seen_users_persists_after_delete`, `test_file_lock_cleanup`, `test_delete_room_cleans`, `test_private_special_chars_no_escape` as extra hard but solvable.

### Exit Codes
0 success, 1 I/O error, 2 invalid input. For `leave` nonexistent, exit 0 idempotent.

### Constraints
- Go stdlib only, build via `go build -o <binary> .`
- Must handle `<>&` without escaping
- Atomic writes via `CreateTemp` and `Rename` recommended, verified behaviorally
- No hardcoded `/tmp/chat`, use `/tmp/codimango` if temp needed
- Respect `--data` flag default `/app/data/chat.json`

### Examples
```bash
go build -o ./chat-server .
./chat-server --data /app/data/chat.json create-room general
./chat-server --data /app/data/chat.json join general alice
./chat-server --data /app/data/chat.json send general alice "Hello"
./chat-server --data /app/data/chat.json get-messages general
./chat-server --data /app/data/chat.json send-private alice bob "secret"
./chat-server --data /app/data/chat.json get-private alice bob
./chat-server --data /app/data/chat.json list-rooms
./chat-server --data /app/data/chat.json list-users general
./chat-server --help
```

Implement at `/app` – Turn1.

### Success Criteria – Hard but Solvable
- Binary builds, help contains keywords, bare help works
- Rooms, joins, leaves idempotent, messages ordered globally monotonic across room+private (interleaved IDs), private isolation, persistence via CLI, sorted lists
- Integrity hard but solvable: strict wrapper checksum canonical no HTML escape, corruption backup naming integer nanosec, stderr warnings, stdlib-only, atomic CreateTemp+Rename + file lock preserving at least 8/10 concurrent sends (reference gets 10), lock cleanup, spaces via Join (multiple args)
- Large history 300 msgs performance <2s, latest N semantics, seen_users persists after delete, next_id not reset
- Hard edge cases make naive WriteFile or per-room counter fail, but solvable with proper locking and Join
