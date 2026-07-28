# Turn 1: Chat Server Core Communication (Go)

## Background

We need a chat server for team collaboration. Build core chat communication functionality in Go.

Data directory `/app/data/` is writable, default persistence `/app/data/chat.json`.

## Task – Implement Go Chat Server at `/app/` (module `chat-server`), built via `go build -o <binary> .`

Stdlib only.

### CLI (MUST)

Global: `--data` default `/app/data/chat.json`

Help:
- Bare binary no args must print help to stdout containing `create-room`, `delete-room`, `list-rooms`, `join`, `leave`, `list-users`, `send`, `get-messages`, `send-private`, `get-private` and exit 0
- `--help`, `-h`, `help` also help exit 0
- Unknown command → exit 2, missing args → exit 2

Commands:
```
create-room <roomID>              -> idempotent, exit 0
delete-room <roomID>              -> prints true/false
list-rooms                        -> JSON array sorted
join <roomID> <userID>            -> exit 0 idempotent, fail if room not exist exit 2
leave <roomID> <userID>           -> idempotent exit 0 even if room/user not exist
list-users <roomID>               -> JSON array sorted, exit 2 if room not exist
send <roomID> <userID> <message>  -> user must be member else exit 2, prints JSON message
get-messages <roomID> [limit]     -> JSON array oldest first, limit optional latest N
send-private <from> <to> <msg>    -> prints JSON private message
get-private <u1> <u2> [limit]     -> JSON array between two users either direction
list-all-users                    -> JSON array sorted unique users seen
```

### Data Model
Message:
```json
{"id":1,"room_id":"general","from":"alice","content":"hi","timestamp":1234567890}
```
Private: `{"id":2,"from":"alice","to":"bob","content":"hi","timestamp":...}`

- IDs globally incrementing int64 starting at 1, unique across room+private, persists
- Timestamp `time.Now().UnixNano()`

### Persistence (simple for Turn1)
Store JSON file (any format, e.g. `{"rooms":{"general":{"users":["alice"],"messages":[...] }}, "private_messages":[...], "next_id":2}`) at `--data` path. Must survive restarts. Atomic writes recommended via `os.CreateTemp`+`os.Rename` but not strictly enforced in Turn1 (enforced in Turn2).

### Business Rules
- `create-room` idempotent
- `join` fails exit 2 if room not exist, idempotent otherwise
- `leave` idempotent exit 0 even if room/user not exist
- `send`: user must be member else exit 2
- `get-messages`: sorted by id asc, if room not exist return `[]` exit 0, if limit given return latest N
- `list-rooms`, `list-users`, `list-all-users` sorted
- `send-private` always allowed, tracks users
- `get-private` both directions sorted asc
- `list-all-users` union of room users + private participants (seen ever)

### Exit Codes
0 success, 1 I/O, 2 invalid input

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

Implement at `/app`.

### Success
Rooms, joins, leaves idempotent, messages ordered, private isolation, persistence across runs, sorted lists.
