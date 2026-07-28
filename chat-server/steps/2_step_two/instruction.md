# Turn 2: Large-Scale Chat Server Support (Go)

## Background

Turn 1 implemented core chat communication with simple persistence. Now we need to scale to many rooms and users.

Turn 1 code is present via `inherit_prior_session`.

## Task – Extend Go Chat Server at `/app/` (same module), built via `go build -o <binary> .`

Must keep Turn 1 functionality working (create-room, delete-room, list-rooms, join, leave idempotent, list-users, send, get-messages, send-private, get-private, list-all-users).

### New Flags
- `--data` default `/app/data/chat.json` – single file mode (Turn1 compat)
- `--config` default `/app/config.json` – sharded mode config path (optional). If config file exists and valid, sharded mode active. Otherwise fallback to single file mode.

### Config File Format (for sharding)

`/app/config.json`:
```json
{
  "shard_count": 4,
  "shards": [
    {"id": 0, "path": "/app/data/shard_0.json", "weight": 1},
    {"id": 1, "path": "/app/data/shard_1.json", "weight": 2},
    {"id": 2, "path": "/app/data/shard_2.json", "weight": 1},
    {"id": 3, "path": "/app/data/shard_3.json", "weight": 1}
  ],
  "presence_ttl_seconds": 60,
  "ops_log": "/app/data/chat_ops.log"
}
```
- `shard_count` >0, shards unique id, path non-empty, weight>0 if present else default 1
- Validation: bad config → exit 2

### Sharded Mode Semantics
- Rooms sharded via weighted hash of roomID (see algorithm)
- `create-room <roomID>`: creates room in designated shard, idempotent; if `global:` prefix, creates in ALL shards (broadcast)
- `delete-room`, `join`, `leave`, `list-users`, `send`, `get-messages` work across shards:
  - `list-rooms` unions all shards, sorted
  - `list-users`, `send`, `get-messages` for a room operate on its designated shard (or all shards if global)
  - `join`/`leave` idempotent even if room/user not exist in that shard? For global, join/leave in all shards
- Private messages: stored in dedicated file `private_path` default `/app/data/private.json` (flat JSON or checksum wrapper both accepted)

### New Commands (MUST)

```
get-shard-id <roomID>        -> int, weighted hash shard id, -1 for global: rooms
get-shard-path <roomID>      -> path, single path for normal, comma-separated sorted list for global:
distribution                 -> JSON map shard_id (string) -> count of rooms (global counts in each shard)
get-messages <roomID> [limit] [offset] -> pagination: limit optional, offset optional default 0
get-private <u1> <u2> [limit] [offset] -> pagination
heartbeat <userID>           -> updates presence timestamp (simple, no TTL expiry required for test, just make online)
get-presence <userID>        -> JSON {"user_id":...,"online":bool,"last_seen":...}
list-online                  -> JSON array sorted of online users
snapshot <backup_path>       -> dir mode: mkdir -p and copy shard files + private + users to dir
restore <backup_path>        -> restores from dir
ops-log                      -> optional: prints ops log as array (if file exists)
```

Help must contain: `create-room`, `delete-room`, `list-rooms`, `join`, `leave`, `list-users`, `send`, `get-messages`, `send-private`, `get-private`, `get-shard-id`, `get-shard-path`, `distribution`, `heartbeat`, `get-presence`, `list-online`, `snapshot`, `restore`

Bare no args → help exit 0.

### Weighted Sharding Algorithm (MUST)

- Each shard weight default 1 if missing, must be >0 else invalid config exit 2
- Total weight sum
- Hash: MD5 of roomID UTF-8 bytes, interpret as big-endian unsigned int: Python `int(md5(key.encode()).hexdigest(),16)`
- `weighted_index = hashInt % totalWeight`
- Iterate shards sorted by id asc subtracting weight: if weighted_index < shard.weight → pick that shard id else subtract

Example: shards 0:w1,1:w2,2:w1,3:w1 total5 hash%5 0→0,1→1,2→1,3→2,4→3

- For roomIDs prefixed `global:`, `get-shard-id` returns -1 (broadcast)
- `get-shard-path`: normal single path, global comma-separated sorted list of all shard paths

### Pagination (large scale history)

- `get-messages <roomID> [limit] [offset]`:
  - limit 0 or omitted = all, offset 0 default
  - Returns slice from offset: `sorted[offset:offset+limit]` if limit>0 else `sorted[offset:]`
  - Must work for 100 messages (test) quickly

- Similarly `get-private`

### Presence (simplified for large scale)

- `heartbeat <userID>`: updates last_seen to now (unix nano), persists in file `/app/data/presence.json` (any format, flat JSON accepted)
- `get-presence <userID>`: returns JSON with `online` bool – online if ever heartbeat (simple) or if within TTL if you implement TTL
- `list-online`: sorted list of users who ever heartbeat (simple) – no need to test expiry

### Snapshot/Restore (horizontal scaling)

- `snapshot <backup_path>`: dir mode – mkdir -p backup_path, copy each shard file, private file, presence file, users file into backup dir
- `restore <backup_path>`: restores – if backup_path is dir, copy files back to original locations (overwrite)
- Must succeed exit 0

### Concurrency & Integrity (Turn2, but lenient)

- File writes should be atomic via `os.CreateTemp`+`os.Rename` (source inspection optional in Turn2, but recommended)
- Handle concurrent CLI invocations? Tests will not spawn 20 parallel for Turn2 simplified, only 10 for Turn1 which already passed. For Turn2 we keep concurrent optional.
- Persistence files may use simple flat JSON or checksum wrapper `{"data":..., "checksum":...}` – tests accept both (permissive)

### Exit Codes
0 success, 1 I/O, 2 invalid input (bad config, room not exist for join, etc). `leave` is idempotent exit 0 even if room not exist.

### Examples
```bash
go build -o ./chat-server .
./chat-server --config /app/config.json create-room general
./chat-server --config /app/config.json get-shard-id general
./chat-server --config /app/config.json get-shard-path general
./chat-server --config /app/config.json distribution
./chat-server --config /app/config.json join general alice
./chat-server --config /app/config.json send general alice "Hello large scale"
./chat-server --config /app/config.json get-messages general 10 0
./chat-server --config /app/config.json heartbeat alice
./chat-server --config /app/config.json get-presence alice
./chat-server --config /app/config.json list-online
./chat-server --config /app/config.json snapshot /tmp/backup
./chat-server --config /app/config.json restore /tmp/backup
```

Implement at `/app`.

### Success
- Turn1 features still work in sharded mode
- Weighted sharding correct, global broadcast
- Pagination works for 100 messages
- Snapshot/restore dir mode
- Help contains keywords
