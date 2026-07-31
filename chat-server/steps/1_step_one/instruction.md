# Turn 1: Chat Server Core Communication (Go) – Hard (48 tests)

We need a production-grade chat server for team collaboration. Build core chat communication functionality in Go with durable persistence and integrity. This turn is hard: checksum canonicalization, atomic multi-process safety with lock cleanup, global ID monotonicity, spaces via Join, and edge-case validation.

Data directory `/app/data/` writable, default persistence `/app/data/chat.json`.

## Task – Implement Go Chat Server at `/app/` (module `chat-server`), built via `go build -o <binary> .`

Stdlib only: `go list -f '{{join .Imports " "}}' .` must contain no dotted imports (only stdlib). `go.mod` must have no external require.

### CLI (MUST - Hard)

Global: `--data` default `/app/data/chat.json`

Help:
- Bare binary no args must print help to stdout containing keywords `create-room`, `delete-room`, `list-rooms`, `join`, `leave`, `list-users`, `send`, `get-messages`, `send-private`, `get-private`, `data`, `checksum` and exit 0
- `--help`, `-h`, `help` also help exit 0
- Unknown command → exit 2, missing required args → exit 2, empty roomID or userID → exit 2, invalid limit (negative or non-integer) → exit 2, missing message → exit 2

Commands:
```
create-room <roomID>              -> idempotent exit 0, fails exit2 if roomID empty
delete-room <roomID>              -> prints true/false, removes room and its messages, exit 0 even if not exist
list-rooms                        -> JSON array sorted of room IDs, must handle 100 rooms sorted
join <roomID> <userID>            -> idempotent exit 0, fail exit2 if room not exist or empty args, 10 concurrent joins must preserve all 10 sorted
leave <roomID> <userID>           -> idempotent exit 0 even if room/user not exist, leave all → list-users [] 
list-users <roomID>               -> JSON array sorted, exit2 if room not exist, after leave all returns []
send <roomID> <userID> <message>  -> sends message, user must be member else exit2, prints JSON, message via strings.Join remaining args (requires message else exit2), special chars <>& no HTML escape
get-messages <roomID> [limit]     -> JSON array oldest first sorted by id asc, limit optional integer ≥0, 0/omit=all, if limit given returns latest N, if room not exist returns [] exit0, invalid limit exit2
send-private <from> <to> <msg>    -> sends private via Join, requires message else exit2, prints JSON, tracks users, special chars no escape
get-private <u1> <u2> [limit]     -> JSON array private both directions sorted asc, limit latest N, invalid limit exit2, nonexist → []
list-all-users                    -> JSON array sorted unique ever seen (rooms users + private participants + seen_users) even after delete, sorted
```

Message JSON (room): `{"id":1,"room_id":"general","from":"alice","content":"hi","timestamp":123...}`
Private: `{"id":2,"from":"alice","to":"bob","content":"hi","timestamp":...}`

- IDs globally incrementing int64 starting at 1, unique across room+private monotonic interleaved (room1, priv1, room2, priv2 must be 1,2,3,4), persists across restarts, not reset on delete except after corruption handling reset to 1
- Timestamp `time.Now().UnixNano()`

### Persistence with Integrity – Explicit File Format (MUST - Hard)

File at `--data` must use wrapper with checksum strict:

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
- Canonical: `json.dumps(data, sort_keys=True, separators=(',',':'))` no HTML escaping – Go must use `SetEscapeHTML(false)` for checksum and file write. Tests include `<>&` for both room and private (raw file must contain "<").
- On write: atomic via `os.CreateTemp` same dir + `os.Rename` plus file lock `<data>.lock` with `O_CREATE|O_EXCL` retry loop 5ms 2000 tries + cleanup after each command.
- Behavioral hard (extra hard, reference gets 10/10):
  - Same room: 10 concurrent sends, file never invalid JSON, must preserve **all 10 messages**, IDs unique, lock cleaned
  - Different rooms: 10 parallel sends to 10 different rooms must preserve **all 10** messages, IDs unique, no corruption
  - Concurrent joins: 10 concurrent joins different users to same room must preserve all 10 sorted
- Spaces via Join: CLI receives message as remaining args. Must use `strings.Join(remainingArgs, " ")` for `send` and `send-private`
- Global ID uniqueness interleaved + after corruption reset: after invalid JSON file, load recreates empty with next_id 1, next send gets id 1
- Large history: 500 msgs, limit latest N (bulk490-499), performance <2s, all 500 retrievable, plus 100 rooms sorted
- Edge validation: empty room/user ID → exit2, missing message → exit2, invalid limit → exit2, nonexistent room get-messages → [] not error, list-users after leave all → [], next_id after corruption resets
- On read: missing file → empty store `rooms={}, private_messages=[], next_id=1, seen_users={}`, empty file → empty store, wrapper missing/empty checksum or mismatch or invalid JSON → corruption: backup `<original>.corrupt.<nanosec>` integer, stderr warning "corrupt" or "checksum", recreate empty valid wrapper

### Business Rules
- create-room idempotent, empty ID exit2
- delete-room true/false, does not clear seen_users nor reset next_id except after corruption
- join idempotent, fail exit2 if room not exist or empty args, concurrent joins preserve all 10 sorted, tracks seen_users
- leave idempotent exit0, leaves empty list
- send: member else exit2, message via Join, requires message else exit2, special chars no escape
- get-messages: sorted asc, nonexist → [], limit latest N, invalid limit exit2
- list-rooms sorted, 100 rooms
- send-private always allowed, tracks users, Join, special chars
- get-private both directions sorted, limit latest N, invalid limit exit2
- list-all-users union ever seen even after delete, sorted
- next_id after corruption → 1

### Integrity Coverage (48 tests hard)
- checksum strict, mismatch/missing/invalid JSON backup integer nanosec, stderr warnings
- atomic all 10 same room, diff rooms all 10, concurrent joins 10
- stdlib only, advisory CreateTemp/Rename
- spaces via Join room and private
- global ID uniqueness interleaved, next_id persists, next_id after corruption reset
- large history 500 perf latest N, 100 rooms sorted
- special chars room and private no escape
- seen_users persists after delete, file lock cleanup
- edge: empty room/user ID exit2, missing message exit2, invalid limit exit2, nonexist returns [], leave all empty

### Exit Codes
0 success, 1 I/O error, 2 invalid input. Leave nonexist exit0.

### Constraints
- Go stdlib only, `go build -o <binary> .`
- Handle `<>&` without escaping for both room and private
- Atomic CreateTemp+Rename + file lock + cleanup, all 10 concurrent
- Use /tmp/codimango for temp
- Respect --data flag default

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

### Success Criteria – Hard (48 tests)
- Binary builds, help contains keywords, bare help works, unknown command exit2, empty IDs exit2, invalid limit exit2, missing message exit2
- Rooms sorted, idempotent, delete true/false, 100 rooms, isolation
- Joins idempotent, fail exit2 if nonexist, 10 concurrent joins preserve all 10 sorted, leaves idempotent leave all empty
- Messages ordered globally monotonic interleaved, limit latest N, nonexist [], private isolation, list-all-users sorted persisting after delete, 500 history perf <2s
- Integrity: strict wrapper checksum canonical no HTML escape room+private, backup integer nanosec, stderr warnings, stdlib-only, atomic all 10 same room + all 10 diff rooms + 10 concurrent joins, lock cleanup, spaces Join, private special chars, next_id after corruption reset
- Hard: naive WriteFile, per-room counter, args[2] fails, but proper locking+Join+global counter passes 48/48
