# Turn 2: Large-Scale Chat Server Support (Go)

## Background

Turn 1 implemented core chat communication. Now we need to scale to many rooms and users.

Turn 1 code is present via `inherit_prior_session`.

## Task – Extend Go Chat Server at `/app/` (same module), built via `go build -o <binary> .`

Must keep Turn 1 functionality working (create-room, delete-room, list-rooms, join, leave idempotent, list-users, send, get-messages, send-private, get-private, list-all-users).

### Flags
- `--data` default `/app/data/chat.json` – single-file mode (Turn1 compat)
- `--config` default `/app/config.json` – sharded mode config path (optional). If config file exists and valid, sharded mode active. Otherwise fallback to single-file mode.

### Config File Format (for sharding, MUST)

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
  "rate_limit": {
    "messages_per_second": 1000,
    "burst": 10000
  },
  "presence_ttl_seconds": 60,
  "ops_log": "/app/data/chat_ops.log",
  "private_path": "/app/data/private.json",
  "presence_path": "/app/data/presence.json",
  "rate_limit_path": "/app/data/rate_limit.json",
  "counter_path": "/app/data/counter.json",
  "users_path": "/app/data/users.json"
}
```
- `shard_count` >0, shards unique id, path non-empty, weight>0 if present else default 1
- `shards` array sorted by id for routing
- `rate_limit` optional, default `{"messages_per_second":5,"burst":10}` if missing. See **Rate Limiting** section below for token-bucket semantics, persistence, per-user, refill math, exit codes.
- `presence_ttl_seconds` optional default 60. See **Presence** section for `online == now - last_seen <= TTL` semantics and `list-online` handling.
- `ops_log` optional default `/app/data/chat_ops.log`
- `private_path` optional default `/app/data/private.json`
- `presence_path` optional default `/app/data/presence.json`
- `rate_limit_path` optional default `/app/data/rate_limit.json`
- `counter_path` optional default `/app/data/counter.json`
- `users_path` optional default `/app/data/users.json`
- **Unknown fields must be ignored** – implementation must be tolerant: if config contains extra fields not listed above (e.g., future extensions), ignore them and use defaults for missing optional fields. Do not fail on unknown fields.

Validation: bad config (invalid JSON, shard_count <=0, duplicate id, empty path, weight <=0) → exit 2.

### Sharded Mode Semantics
- Rooms sharded via weighted hash of roomID
- `create-room <roomID>`: creates room in designated shard, idempotent; if `global:` prefix, creates in ALL shards (broadcast)
- `delete-room`, `join`, `leave`, `list-users`, `send`, `get-messages` work across shards:
  - `list-rooms` unions all shards, sorted deduped
  - `list-users`, `send`, `get-messages` for a room operate on its designated shard (or all shards if global)
  - `join`/`leave` idempotent exit 0 even if room/user not exist in that shard (for global, join/leave in all shards)
- Private messages: stored in file at `private_path` (default `/app/data/private.json`), may use flat `{"private_messages": [...]}` or wrapper `{"data": {...}, "checksum": "..."}` – tests accept both (permissive) for large-scale ease.

### Rate Limiting (large-scale spam protection, explicit)

- Config `rate_limit` optional with `messages_per_second` (rate) and `burst`
- Default 5/s burst 10 if missing
- **Token-bucket per-user**: each user has bucket with tokens = burst initially, last_refill = now (UnixNano)
- **Refill math**: on each `send` or `send-private`, compute elapsed seconds = (now - last_refill)/1e9, refill = elapsed * rate, tokens = min(burst, tokens + refill), last_refill = now
- **Consume**: if tokens >=1, tokens -=1, allow; else fail
- **Per-user behavior**: each user independent bucket
- **Persistence path**: `rate_limit_path` default `/app/data/rate_limit.json`, wrapper format with checksum `{"data": {"alice": {"tokens":..., "last_refill":...}}, "checksum": "..."}`, atomic via `CreateTemp`+`Rename`, corruption handling same as other files, file locking via global lock `/app/data/global.lock`
- **Exit code / stderr**: if rate-limited, exit code 1 (not 2), stderr must contain "rate limit" (case-insensitive), no stdout, must NOT increment message IDs and must NOT append to ops log
- **Tests for this turn**: will only verify **within-burst success** (lenient) – e.g., burst 10000 allows 50 sends to succeed, not requiring failure enforcement. This eases Turn2 while still requiring token-bucket logic and persistence. Strict failure tests may be added in future but not required for reward in this simplified Turn2.

### Presence (large-scale)

- `heartbeat <userID>`: updates user's last_seen to now (UnixNano) persisted in file at `presence_path` default `/app/data/presence.json`, wrapper with checksum, atomic writes, corruption handling, global lock
- `get-presence <userID>`: returns JSON `{"user_id": "alice", "online": true/false, "last_seen": 123, "last_seen_seconds_ago": 0.5}` where `online = now - last_seen <= presence_ttl_seconds * 1e9` (TTL from config default 60). If user never heartbeat, online false, last_seen 0
- `list-online`: returns JSON array sorted of userIDs whose last_seen within TTL, i.e., `now - last_seen <= TTL*1e9`
- **Tests for this turn**: only verify **heartbeat makes online** and `list-online` contains user (simple, no TTL expiry sleep required). TTL expiry handling is optional for this simplified Turn2 but recommended per spec.

### New Commands (MUST)

```
get-shard-id <roomID>        -> int, weighted hash shard id, -1 for global:
get-shard-path <roomID>      -> path, single for normal, comma-separated sorted list for global:
distribution                 -> JSON map shard_id (string) -> count rooms (global counts in each shard)
get-messages <roomID> [limit] [offset] -> pagination: limit 0 or omitted = all, offset 0 default, returns sorted[offset:offset+limit] if limit>0
get-private <u1> <u2> [limit] [offset] -> pagination
heartbeat <userID>           -> updates presence
get-presence <userID>        -> JSON with online bool per above TTL formula
list-online                  -> sorted online users
snapshot <backup_path>       -> dir mode: mkdir -p and copy shard files + private + presence + users + counter + ops_log
restore <backup_path>        -> restores from dir (copy back)
ops-log                      -> optional: prints ops log as JSON array (skip invalid lines with warning to stderr)
```

Help must contain: `create-room`, `delete-room`, `list-rooms`, `join`, `leave`, `list-users`, `send`, `get-messages`, `send-private`, `get-private`, `get-shard-id`, `get-shard-path`, `distribution`, `heartbeat`, `get-presence`, `list-online`, `snapshot`, `restore`, `ops-log`

Bare no args → help exit 0.

### Weighted Sharding Algorithm (MUST)

- Weight default 1 if missing, must be >0 else invalid config exit 2
- Total weight = sum weights
- Hash: MD5 of roomID bytes, big-endian int: Python `int(md5(key.encode()).hexdigest(),16)`
- `weighted_index = hashInt % totalWeight`
- Iterate shards sorted by id asc subtracting weight: if weighted_index < shard.weight → pick that shard id else subtract

Example: 0:w1,1:w2,2:w1,3:w1 total5 → 0→0,1→1,2→1,3→2,4→3

- `global:` prefix → -1 broadcast, `get-shard-path` returns comma-separated sorted list of all shard paths

### Pagination

- `get-messages <roomID> [limit] [offset]`: limit 0 = all, offset 0 default, returns `sorted[offset:offset+limit]` if limit>0 else `sorted[offset:]`
- Similarly `get-private`
- Must work for 50 messages quickly, and for large history 200 performance test <2s

### Snapshot/Restore

- `snapshot <backup_path>`: dir mode – mkdir -p backup_path, copy each shard file (if exists), private_path, presence_path, counter_path, users_path, ops_log into backup dir (basename preserved)
- `restore <backup_path>`: dir – copy files back from backup dir to original paths (overwrite)
- Exit 0

### Integrity & Concurrency (Turn2, lenient for ease)

- Persistence files (chat.json, shard_*.json, private.json, presence.json, rate_limit.json, counter.json, users.json) may use simple flat JSON OR wrapper `{"data":..., "checksum":...}` – tests accept both for ease, but reference solution uses wrapper with checksum where checksum = MD5 of canonical data JSON `json.dumps(data, sort_keys=True, separators=(',',':'))` with `SetEscapeHTML(false)`, atomic via `CreateTemp`+`Rename`
- Corruption handling: if file has invalid JSON, backup to `<path>.corrupt.<nanosec>` where nanosec = `time.Now().UnixNano()` integer, stderr warning containing "corrupt" or "checksum", recreate empty valid file
- Missing checksum handling for wrapped files: if file has `data` field but `checksum` missing or empty → corruption handling (backup, recreate empty)
- Checksum mismatch: if file has `data` and `checksum` but checksum mismatch → corruption handling
- **Atomic behavior under concurrent sends**: Must not corrupt file – behavioral check: during 10 concurrent sends, file should remain valid JSON, IDs unique, no partial writes. Tests will spawn 10 parallel `send` processes and verify file stays valid JSON and has at least 2 messages after (lenient). Advisory: implement file locking via `<path>.lock` with `O_CREATE|O_EXCL` retry to prevent races.
- **Stdlib-only imports**: `go.mod` must have no external requires, `go list -f '{{join .Imports " "}}' .` should contain no dotted imports (only stdlib). Tested in Turn1.
- **Source-string checks for `CreateTemp` and `Rename`**: These are made **advisory** not reward-critical in Turn2 simplified – reference solution contains them, but tests will not fail if missing; instead behavioral atomic check above ensures no corruption. Turn1 still checks source as advisory? Actually Turn1 will have behavioral check, not strict source string.

### Exit Codes
0 success, 1 I/O or rate-limited (rate limit → exit1 stderr "rate limit"), 2 invalid input (bad config, room not exist for join, etc). `leave` idempotent exit 0 even if room not exist.

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
- Pagination works for 50 messages
- Snapshot/restore dir mode
- Presence simple heartbeat online
- Rate limiting lenient within-burst (optional)
- Help contains keywords
