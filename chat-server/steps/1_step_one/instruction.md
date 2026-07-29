# Turn 1: Chat Server Core Communication (Go) – Medium-Hard

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
- Canonical data JSON: `json.dumps(data, sort_keys=True, separators=(',', ':'))` with no HTML escaping – Go must use `json.Encoder.SetEscapeHTML(false)` for checksum calculation and file write. Test includes `<>&` in message content to ensure no escaping.
- On write: atomic via `os.CreateTemp` in same dir + `os.Rename`. Behavioral check: during 10 concurrent `send` processes, file should never become invalid JSON and should have at least 1 message after, IDs unique.
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

### Integrity Coverage (Turn1 will test explicitly)
- `test_checksum_integrity`: if file uses wrapper, verify checksum matches canonical (Python's sort_keys+separators, Go SetEscapeHTML(false)). If flat, at least check file has `rooms` key.
- `test_checksum_mismatch_handling`: create wrapper file with data but wrong checksum, run `list-rooms`, expect backup file named `<original>.corrupt.<nanosec>` where nanosec integer, stderr warning contains "corrupt" or "checksum", and file recreated empty with valid checksum.
- `test_missing_checksum_handling`: wrapper with `data` but missing `checksum` or empty checksum → same corruption handling (backup, warning, recreate empty)
- `test_invalid_json_backup_naming`: file with invalid JSON `{invalid` → backup file named `<path>.corrupt.<nanosec>` with integer nanosec, and recreated file valid empty.
- `test_stderr_warnings`: corruption produces warning to stderr containing "corrupt"
- `test_atomic_behavior_concurrent`: behavioral atomicity – during 10 concurrent `send` processes, continuously reading file should never yield invalid JSON; after at least 1 message succeeds, IDs unique, file valid. This verifies atomic CreateTemp+Rename behaviorally, not via source string.
- `test_stdlib_only`: `go.mod` no external requires and `go list -f '{{join .Imports " "}}' .` has no dotted imports.
- `test_atomic_write_advisory`: previously source-string check `CreateTemp`+`Rename` – now made **advisory** not reward-critical: if not found, log warning but don't fail; behavioral concurrent test is reward-critical.

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

### Success Criteria
- Binary builds, help contains keywords, bare help works
- Rooms, joins, leaves idempotent, messages ordered, private isolation, persistence via CLI (not just raw file), sorted lists
- Integrity: checksum wrapper optional for Turn1 but one test requires wrapper to be implementable, special chars, atomic behavioral, corruption backup naming, stderr warnings, stdlib-only
