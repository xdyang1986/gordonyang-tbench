# Turn 1: Chat Server Core Communication (Go) – Extra Hard (56 tests)

We need a production-grade chat server for team collaboration. Build core chat communication functionality in Go with durable persistence and integrity. This turn is extra hard: 56 tests, 20 concurrent all 20, 1000-msg history, 200 rooms, checksum strict, spaces Join, global ID, edge validation, Unicode.

Data directory `/app/data/` writable, default persistence `/app/data/chat.json`.

## Task – Implement Go Chat Server at `/app/` (module `chat-server`), built via `go build -o <binary> .`

Stdlib only: `go list -f '{{join .Imports " "}}' .` must contain no dotted imports (only stdlib). `go.mod` must have no external require.

### CLI (MUST - Extra Hard)

Global: `--data` default `/app/data/chat.json`

Help: bare binary no args must print help containing keywords `create-room`, `delete-room`, `list-rooms`, `join`, `leave`, `list-users`, `send`, `get-messages`, `send-private`, `get-private`, `data`, `checksum` exit0. `--help`, `-h`, `help` also help exit0. Unknown command → exit2, missing required args → exit2, empty roomID or userID → exit2, invalid limit → exit2, missing message → exit2.

Commands:
```
create-room <roomID>              -> idempotent exit0, fail exit2 if empty, handles 200 rooms sorted
delete-room <roomID>              -> prints true/false, removes room and its messages, exit0 even if not exist, join after delete fails exit2, does not clear seen_users nor reset next_id except after corruption reset to 1
list-rooms                        -> JSON array sorted
join <roomID> <userID>            -> idempotent exit0, fail exit2 if room not exist or empty args, 20 concurrent joins different users must preserve all 20 sorted, tracks seen_users
leave <roomID> <userID>           -> idempotent exit0 even if room/user not exist, after leaving all list-users [] and send after leave fails exit2
list-users <roomID>               -> JSON array sorted, exit2 if room not exist
send <roomID> <userID> <message>  -> sends message, user must be member else exit2, prints JSON, message via strings.Join remaining args (requires message else exit2), special chars <>& no HTML escape (raw file contains "<"), Unicode emoji 🌍🚀😀 preserved, newlines/tabs preserved, large message 10KB handled
get-messages <roomID> [limit]     -> JSON array oldest first sorted by id asc, limit optional integer ≥0, 0/omit=all, if limit given returns latest N, if room not exist returns [] exit0, invalid limit exit2, limit zero returns all
send-private <from> <to> <msg>    -> sends private via Join, requires message else exit2, tracks users, special chars and Unicode
get-private <u1> <u2> [limit]     -> JSON array private both directions sorted asc, limit latest N, invalid limit exit2, limit zero returns all
list-all-users                    -> JSON array sorted unique ever seen even after delete
```

Message JSON room: `{"id":1,"room_id":"general","from":"alice","content":"hi","timestamp":...}`
Private: `{"id":2,"from":"alice","to":"bob","content":"hi","timestamp":...}`
- IDs globally incrementing int64 starting at 1 unique across room+private monotonic interleaved 1,2,3,4, persists across restarts (20 room+private → next_id 41) and after many ops, not reset on delete except after corruption reset to 1
- Timestamp `time.Now().UnixNano()`

### Persistence with Integrity – Explicit File Format (MUST - Extra Hard)

File at `--data` must use wrapper `{"data":{rooms, private_messages, next_id, seen_users}, "checksum": md5 canonical}` canonical = `json.dumps(data, sort_keys=True, separators=(',',':'))` no HTML escaping – Go must use `SetEscapeHTML(false)` for checksum and file write. Raw file must contain "<" for special chars and emoji.

- On write: atomic via `os.CreateTemp` same dir + `os.Rename` plus file lock `<data>.lock` `O_CREATE|O_EXCL` retry loop 5ms 2000 tries + cleanup after each command (lock file must not remain)
- Behavioral extra hard (reference gets 20/20):
  - Same room: 20 concurrent sends, file never invalid JSON during concurrent, must preserve all 20 messages, IDs unique
  - Different rooms: 20 parallel sends to 20 different rooms must preserve all 20, IDs unique
  - Concurrent joins: 20 concurrent joins different users same room must preserve all 20 sorted
  - Concurrent mixed: persistence across restarts with many ops, room IDs with dash/underscore/dot/colon
- Spaces via Join: must use `strings.Join(remainingArgs, " ")` for send and send-private – tests pass message as multiple separate args (Hello World with spaces)
- Global ID uniqueness interleaved + large message 10KB + Unicode emoji + newlines/tabs
- Large history **1000 messages** latest N (`get-messages general 10` → bulk990-999) performance <2s, all 1000 retrievable, plus 200 rooms sorted
- Edge validation: empty room/user ID exit2, missing message exit2, invalid limit (`-1, abc, -100`) exit2 for both get-messages and get-private, limit zero returns all, nonexist room get-messages [] not error, list-users after leaving all → [], join after delete fails exit2, send after leave fails exit2, large number of rooms 200 sorted, concurrent joins 20, next_id after corruption reset to 1

On read: missing file → empty store, empty file → empty store, wrapper missing/empty checksum or mismatch or invalid JSON → corruption: backup `<original>.corrupt.<nanosec>` integer, stderr warning "corrupt" or "checksum", recreate empty valid wrapper.

### Business Rules
- create-room idempotent, empty ID exit2, handles 200 rooms sorted
- delete-room true/false, does not clear seen_users nor reset next_id except after corruption, join after delete fails
- join idempotent, fail exit2 if room not exist or empty args, 20 concurrent joins preserve all 20 sorted, tracks seen_users
- leave idempotent exit0 even if room/user not exist, leave all → list-users [] and send after leave fails
- send: member else exit2, message via Join, requires message else exit2, special chars no escape, Unicode, newlines/tabs, 10KB
- get-messages: sorted asc, nonexist → [], limit latest N, limit zero → all, invalid limit exit2
- list-rooms, list-users, list-all-users sorted, 200 rooms
- send-private always allowed, tracks users, Join, special chars, Unicode, 10KB
- get-private both directions sorted, limit latest N, limit zero all, invalid limit exit2
- list-all-users union ever seen even after delete

### Integrity Coverage (56 tests extra hard)
- checksum strict, mismatch/missing/invalid JSON backup integer nanosec, stderr warnings
- atomic all 20 same room, diff rooms all 20, concurrent joins 20, file lock cleanup
- stdlib only, advisory CreateTemp/Rename
- special chars room+private no escape, Unicode emoji, newlines/tabs, large message 10KB
- global ID uniqueness interleaved, next_id persists, next_id after corruption reset, persistence across many ops 20 room+private → next_id 41
- large history 1000 perf latest N, 200 rooms sorted, limit zero all, nonexist [], leave all [], join after delete fails, send after leave fails, room ID with dash/underscore/dot/colon
- edge: empty room/user ID exit2, missing message exit2, invalid limit exit2 (negative, non-int) for room and private
- 56 tests extra hard: naive WriteFile, per-room counter, args[2] fails

### Exit Codes
0 success, 1 I/O error, 2 invalid input. Leave nonexist exit0.

### Constraints
- Go stdlib only, build via `go build -o <binary> .`
- Handle `<>&` without escaping for both room and private, plus Unicode
- Atomic CreateTemp+Rename + file lock + cleanup, all 20 concurrent
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

### Success Criteria – Extra Hard (56 tests)
- Binary builds, help contains keywords, bare help, unknown exit2, empty IDs exit2, invalid limit exit2, missing message exit2, nonexist [], leave all [], join after delete fails, send after leave fails
- Rooms sorted, idempotent, delete true/false, 200 rooms, isolation, room ID special chars dash/underscore/dot/colon
- Joins idempotent, fail exit2 if nonexist, 20 concurrent joins preserve all 20 sorted, leaves idempotent, list-users sorted
- Messages ordered globally monotonic interleaved, limit latest N, limit zero all, private isolation, list-all-users sorted persisting after delete, 1000 history perf <2s, 400/500 previously 1000 now, Unicode emoji, newlines/tabs, large message 10KB
- Integrity: strict wrapper checksum canonical no HTML escape room+private, backup integer nanosec, stderr warnings, stdlib-only, atomic all 20 same room + all 20 diff rooms + 20 concurrent joins, lock cleanup, spaces Join both, private special chars
- 56 tests extra hard, naive fails but proper locking+Join+global counter passes
