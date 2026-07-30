# Turn 1: Chat Server Core Communication (Go) – Hard but Solvable

We need a production-grade chat server for team collaboration. Build core chat communication functionality in Go with durable persistence and integrity. This turn is hard but solvable: checksum canonicalization, atomic multi-process safety with lock cleanup, global ID monotonicity across room+private, and message content with spaces via Join.

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
send <roomID> <userID> <message>  -> sends message to room, user must be member else exit 2, prints JSON message object, message via strings.Join remaining args
get-messages <roomID> [limit]     -> JSON array messages in room, oldest first (sorted by id asc), limit optional integer >=0, 0 or omitted = all, if limit given returns latest N (oldest-first order preserved but only last N)
send-private <from> <to> <msg>    -> sends private message, message via Join, prints JSON
get-private <u1> <u2> [limit]     -> JSON array private messages between two users either direction, oldest first, limit optional latest N
list-all-users                    -> JSON array sorted unique users seen ever (from joins or private)
```

Message JSON (room):
```json
{"id":1,"room_id":"general","from":"alice","content":"hi","timestamp":1234567890123}
```
Private: `{"id":2,"from":"alice","to":"bob","content":"hi","timestamp":...}`

- IDs globally incrementing int64 starting at 1, unique across both room and private, monotonic increasing, persists across restarts, not reset on delete
- Timestamp `time.Now().UnixNano()`

### Persistence with Integrity – Explicit File Format (MUST - Hard)

File at `--data` path must use wrapper with checksum for hard integrity.

**File Format Wrapper with Checksum (required for integrity tests):**
```json
{
  "data": {
    "rooms": {"general": {"users": ["alice"], "messages": [...]}},
    "private_messages": [...],
    "next_id": 3,
    "seen_users": {...}
  },
  "checksum": "hex md5 of canonical JSON of data without HTML escaping"
}
```
- Schema: top-level must have `rooms` map where `rooms[roomID]` has `users` array and `messages` array, plus `private_messages`, `next_id`, `seen_users` inside `data`.
- Canonical data JSON: `json.dumps(data, sort_keys=True, separators=(',', ':'))` with no HTML escaping – Go must use `json.Encoder.SetEscapeHTML(false)` for checksum and file write. Tests include `<>&` in message content for both room and private to ensure no escaping (raw file must contain "<").
- On write: atomic via `os.CreateTemp` in same dir + `os.Rename` plus file lock `<data>.lock` with `O_CREATE|O_EXCL` retry loop (5ms sleep, 2000 tries) and cleanup after each command.
- Behavioral hard checks (extra hard, reference gets 10/10):
  - Same room: 10 concurrent `send` processes, file must never become invalid JSON, must preserve **all 10 messages** after 10 concurrent, IDs unique, lock cleaned.
  - Different rooms: 10 parallel sends to 10 different rooms must preserve at least **9 total** messages, no corruption, IDs unique.
- Spaces via Join: CLI receives message as remaining args. Must use `strings.Join(remainingArgs, " ")` to support `send general alice Hello World with spaces` and `send-private alice bob secret with spaces` where message contains spaces without quoting. Tests invoke binary with multiple separate args.
- Global ID uniqueness: IDs globally incrementing across room and private, monotonic, persists across restarts and interleaved.
- Large history: must handle **500 messages** efficiently, pagination via limit latest N semantics (`get-messages general 10` → last 10) and performance <2s.
- On read: validation before any command:
  - Missing file → empty store `rooms={}, private_messages=[], next_id=1, seen_users={}`
  - Empty file → empty store
  - If file has `data` field (wrapped):
    - If `checksum` missing or empty → corruption: backup to `<original_path>.corrupt.<nanosec>` where `<nanosec>` is `time.Now().UnixNano()` integer, stderr warning containing "corrupt" or "checksum" (case-insensitive), recreate empty valid file with correct checksum
    - If `checksum` present: compute expected MD5 of canonical data JSON, mismatch → corruption handling as above
  - If file has no `data` field (flat): try to parse as Data directly. If valid with `rooms` key, accept. If invalid JSON → corruption handling: backup to `<path>.corrupt.<nanosec>` integer, stderr warning containing "corrupt", recreate empty valid.

### Business Rules
- `create-room` idempotent exit 0
- `delete-room` idempotent exit 0 prints true/false, does not clear seen_users nor reset next_id
- `join` fails exit 2 if room not exist, idempotent else, tracks seen_users
- `leave` idempotent exit 0 even if room/user not exist
- `send`: user must be member else exit 2, message via Join
- `get-messages`: sorted id asc, if room not exist return `[]` exit 0, limit returns latest N
- `list-rooms`, `list-users`, `list-all-users` sorted
- `send-private` always allowed, tracks users, message via Join
- `get-private` both directions sorted asc, limit latest N
- `list-all-users` union ever seen (rooms users current + private participants + seen_users persisted) even after room deletion

### Integrity Coverage (Turn1 tests – 39 tests hard but solvable)
- `test_checksum_integrity_strict`: wrapper data+checksum, checksum matches canonical, SetEscapeHTML(false) for room and private
- `test_checksum_mismatch_handling`, `test_missing_checksum_handling`, `test_invalid_json_backup_naming`, `test_stderr_warnings`: corruption handling backup naming integer nanosec, warning, recreate empty valid
- `test_atomic_behavior_concurrent`: 10 concurrent same room, file never invalid JSON, at least 9 msgs, IDs unique, lock cleaned
- `test_concurrent_different_rooms`: 10 parallel different rooms, at least 8 total msgs, IDs unique, no corruption
- `test_stdlib_only`, `test_atomic_write_advisory` (advisory)
- `test_send_with_spaces_via_join`, `test_send_private_with_spaces_via_join`: multiple args must be joined
- `test_global_id_uniqueness_interleaved`: interleaved room+private globally monotonic
- `test_large_history_and_pagination_performance`: 400 msgs, limit latest N (bulk390-399), <2s
- `test_seen_users_persists_after_delete`, `test_file_lock_cleanup`, `test_private_special_chars_no_escape`

### Exit Codes
0 success, 1 I/O error, 2 invalid input. For `leave` nonexistent, exit 0 idempotent.

### Constraints
- Go stdlib only, build via `go build -o <binary> .`
- Must handle `<>&` without escaping for both room and private
- Atomic writes via `CreateTemp` and `Rename` + file lock recommended, verified behaviorally (≥9 msgs)
- No hardcoded `/tmp/chat`, use `/tmp/codimango` if temp needed
- Respect `--data` flag default `/app/data/chat.json`

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

Implement at `/app` – Turn1.

### Success Criteria – Hard (39 tests)
- Binary builds, help contains keywords, bare help works
- Rooms, joins, leaves idempotent, messages ordered globally monotonic across room+private, private isolation, persistence via CLI, sorted lists
- Integrity hard: strict wrapper checksum canonical no HTML escape for room+private, corruption backup naming integer nanosec, stderr warnings, stdlib-only, atomic CreateTemp+Rename + file lock preserving all 10 same room and at least 9/10 diff rooms (extra hard), lock cleanup, spaces via Join for both room and private, private special chars preserved
- Large history 500 msgs performance <2s, latest N semantics, seen_users persists after delete, next_id not reset
- 39 tests, hard: naive WriteFile or per-room counter or args[2] fails, but proper implementation with locking and Join passes
