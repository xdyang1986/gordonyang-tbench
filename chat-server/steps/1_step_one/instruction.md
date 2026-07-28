# Turn 1: Chat Server Core Communication (Go)

## Background

We need a chat server for team collaboration. Build core chat communication functionality in Go.

Data directory `/app/data/` writable, default persistence `/app/data/chat.json`.

## Task – Implement Go Chat Server at `/app/` (module `chat-server`), built via `go build -o <binary> .`

Stdlib only: `go list -f '{{join .Imports " "}}' .` must contain no dotted imports.

### CLI (MUST)

Global: `--data` default `/app/data/chat.json`

Help:
- Bare binary no args must print help containing `create-room`, `delete-room`, `list-rooms`, `join`, `leave`, `list-users`, `send`, `get-messages`, `send-private`, `get-private`, `data` and exit 0
- `--help`, `-h`, `help` also help exit 0
- Unknown command → exit 2, missing args → exit 2

Commands:
```
create-room <roomID>              -> idempotent exit 0
delete-room <roomID>              -> prints true/false
list-rooms                        -> JSON array sorted
join <roomID> <userID>            -> idempotent, fail if room not exist exit 2
leave <roomID> <userID>           -> idempotent exit 0 even if room/user not exist
list-users <roomID>               -> sorted, exit 2 if room not exist
send <roomID> <userID> <message>  -> user must be member else exit 2, prints JSON message
get-messages <roomID> [limit]     -> oldest first, limit optional latest N, 0 or omitted = all
send-private <from> <to> <msg>    -> prints JSON
get-private <u1> <u2> [limit]     -> both directions sorted asc
list-all-users                    -> sorted unique seen ever
```

Message: `{"id":1,"room_id":"general","from":"alice","content":"hi","timestamp":...}` Private: `{"id":2,"from":"alice","to":"bob","content":"hi","timestamp":...}`

- IDs globally incrementing int64 starting at 1, unique across room+private, persists
- Timestamp `time.Now().UnixNano()`

### Persistence with Basic Integrity (for Turn1 medium)
File at `--data` path. Format may be simple `{"rooms": {...}, "private_messages": [...], "next_id":1}` or wrapper `{"data":{...},"checksum":...}`. For Turn1, we require:
- File survives restarts
- Atomic writes via `os.CreateTemp`+`os.Rename` (source inspection will check for `CreateTemp` and `Rename`)
- Handle special chars `<>&` without HTML escaping corruption – use `SetEscapeHTML(false)` when encoding JSON (test includes `<>&`)
- For Turn1, checksum optional but recommended; Turn2 will enforce checksum.

### Business Rules
- `create-room` idempotent, `delete-room` prints true/false exit 0
- `join` fails exit 2 if room not exist, idempotent
- `leave` idempotent exit 0 even if room/user not exist (ease)
- `send` must be member else exit 2
- `get-messages` sorted by id asc, if room not exist return `[]` exit 0, limit returns latest N
- `list-rooms`, `list-users`, `list-all-users` sorted
- `send-private` always allowed, tracks users
- `get-private` both directions
- `list-all-users` union of room users + private participants + seen set

### Examples
```bash
go build -o ./chat-server .
./chat-server --data /app/data/chat.json create-room general
./chat-server --data /app/data/chat.json join general alice
./chat-server --data /app/data/chat.json send general alice "Hello"
./chat-server --data /app/data/chat.json get-messages general
./chat-server --data /app/data/chat.json send-private alice bob "secret"
./chat-server --data /app/data/chat.json get-private alice bob
./chat-server --help
```

Implement at `/app`.
