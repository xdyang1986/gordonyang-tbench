# Turn 2: Large-Scale Chat Server Support (Go) – Extra Hard

## Background

Turn 1 implemented core chat communication. Now we need to scale to many rooms and users with production-grade distributed systems requirements. This turn is extra hard: weighted sharding, persistent token bucket with refill, presence TTL, pagination performance, atomic multi-file transactions, full snapshot/restore, ops-log corruption tolerance, and strict concurrency preserving all 10 sends.

Turn 1 code is present via `inherit_prior_session`.

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
- **Unknown fields must be ignored** – implementation must be tolerant: if config contains extra fields not listed above (e.g., future extensions), ignore them and use defaults for missing optional fields. Do not fail on unknown fields. Tests verify that config with extra field like `future_field` or `unknown_top_level` still allows `create-room` to succeed.

Validation: bad config (invalid JSON, shard_count <=0, duplicate id, empty path, weight <=0) → exit 2. Tests verify all these invalid cases return exit 2.

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
- **Tests for this turn – extra hard**: verify both within-burst success and rejection, **refill after sleep**, and **persistence across restarts**. Burst enforcement: with burst 2 rate 1, 2 sends succeed, 3rd must fail exit 1 with "rate limit" stderr and produce no ID increment and no ops_log side effects. After 1.5s sleep, token refills and next send must succeed. Per-user buckets independent, and rate_limit file must persist across CLI invocations (corruption handling tested).

### Presence (large-scale)

- `heartbeat <userID>`: updates user's last_seen to now (UnixNano) persisted in file at `presence_path` default `/app/data/presence.json`, wrapper with checksum, atomic writes, corruption handling, global lock
- `get-presence <userID>`: returns JSON `{"user_id": "alice", "online": true/false, "last_seen": 123, "last_seen_seconds_ago": 0.5}` where `online = now - last_seen <= presence_ttl_seconds * 1e9` (TTL from config default 60). If user never heartbeat, online false, last_seen 0
- `list-online`: returns JSON array sorted of userIDs whose last_seen within TTL, i.e., `now - last_seen <= TTL*1e9`
- **Tests for this turn – extra hard**: verify heartbeat makes online, `list-online` contains user, **TTL expiry**, and **unknown user handling**. With TTL 2s, after heartbeat and sleep 3s, `get-presence` must return `online=false` and `list-online` must not contain user. For user never seen, `get-presence` must return `{"user_id":..., "online":false, "last_seen":0, "last_seen_seconds_ago":0}`. Presence file must use wrapper checksum and handle corruption.

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

### Pagination – Extra Hard

- `get-messages <roomID> [limit] [offset]`: limit 0 = all, offset 0 default, returns `sorted[offset:offset+limit]` if limit>0 else `sorted[offset:]`
- Similarly `get-private <u1> <u2> [limit] [offset]` must support offset pagination, both directions.
- Must handle spaces via `strings.Join` for message content as in Turn1.
- Must work for 500 messages quickly, and for large history 1000 performance test <2s. Tests verify both `get-messages` and `get-private` pagination with offset: e.g., limit 10 offset 0 → msg0-9, offset 10 → msg10-19, offset 500, and reversely. O(n) slicing required, not O(n^2).
- Global ID monotonic: pagination must preserve global ID order.

### Snapshot/Restore – Extra Hard

- `snapshot <backup_path>`: two modes:
  - **Dir mode** (no .json suffix or existing dir): mkdir -p backup_path, copy each shard file (if exists), private_path, presence_path, counter_path, users_path, rate_limit_path, ops_log into backup dir (basename preserved), plus config.json. Must preserve private messages, presence, counter (next_id), users, rate_limit, and ops_log exactly.
  - **File mode** (path ends with .json): writes combined JSON file with keys `shards` (map shard_id->ShardFileData), `private`, `presence`, `rate_limit`, `counter`, `users`, `ops_log`. Reference solution implements both; tests verify both modes.
- `restore <backup_path>`: dir mode copies files back from backup dir to original paths (overwrite). File mode reads combined JSON and restores each component via atomic writes. After restore, private messages, presence, counter (next_id must be restored, so next send gets expected ID), users, rate_limit, and ops_log must be exactly as snapshot time. Tests verify that after restore, mutated data (new rooms, new users, new private msgs) are gone and old state restored, including that `list-all-users` does not contain post-snapshot users and `list-rooms` does not contain post-snapshot rooms.
- Exit 0, must handle global lock.

### Integrity & Concurrency (Turn2, extra hard)

- Persistence files (chat.json, shard_*.json, private.json, presence.json, rate_limit.json, counter.json, users.json) **must** use wrapper `{"data":..., "checksum":...}` where checksum = MD5 of canonical data JSON `json.dumps(data, sort_keys=True, separators=(',',':'))` with `SetEscapeHTML(false)`, atomic via `CreateTemp`+`Rename`. Tests verify checksum for all sharded files (strict), not just chat.json.
- Corruption handling **for all files**: if any persistence file has invalid JSON, backup to `<path>.corrupt.<nanosec>` integer, stderr warning containing "corrupt" or "checksum", recreate empty valid file with correct checksum. Tests verify for shard files, private.json, rate_limit.json, presence.json, etc.
- Missing checksum handling for wrapped files: if file has `data` field but `checksum` missing or empty → corruption handling (backup, recreate empty)
- Checksum mismatch: if file has `data` and `checksum` but checksum mismatch → corruption handling
- **Atomic behavior under concurrent sends – extra hard but solvable**: Must not corrupt any shard file. Behavioral hard check: during 10 concurrent sends to same room, file must remain valid JSON, IDs unique, no partial writes, and preserve at least 9 messages (reference gets 10, hard but solvable). Additionally, 10 concurrent sends to 10 different rooms hashing to different shards must preserve at least 9 with unique global IDs (tests multi-shard atomic via global lock). Requires file locking via `<path>.lock` or global lock `/app/data/global.lock` with `O_CREATE|O_EXCL` retry loop and cleanup.
- **Stdlib-only imports**: `go.mod` must have no external requires, `go list -f '{{join .Imports " "}}' .` should contain no dotted imports (only stdlib). Tested in Turn1.
- **Source-string checks for `CreateTemp` and `Rename`**: advisory – logs warning if missing but behavioral tests are reward-critical. Reference uses both.
- **Ops-log invalid line handling**: `ops-log` must skip invalid JSON lines with warning to stderr containing "corrupt"/"skip"/"warning", and return valid entries as JSON array. Must preserve order.
- **Config validation & unknown-field tolerance**: malformed configs (invalid JSON, shard_count <=0, duplicate id, empty path, weight <=0, negative id) must exit 2; unknown fields at top-level and inside shards must be ignored (tolerant). Tests verify unknown fields still allow room creation.
- **Weighted distribution**: 50 rooms test with tolerance checking shard 1 weight2 gets ~40% of rooms.
- **Global broadcast**: `global:` rooms must be created in all shards, `send` replicates to all, `get-messages` dedupes by ID, `distribution` counts global in each shard.
- **Spaces handling**: message content with spaces must use `strings.Join` remaining args (tested for both room and private in sharded mode).

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

### Success – Extra Hard but Solvable
- Turn1 features still work in sharded mode, including spaces via Join, global ID monotonic, checksum strict, atomic at least 9/10 concurrent, lock cleanup
- Weighted sharding correct (MD5 big-endian mod weighted), global broadcast: id -1, comma-separated paths, replication, dedup, distribution counts global in each shard, 20-room exact and 50-room weight tolerance
- Pagination extra hard but solvable: 500+ messages for both get-messages and get-private with limit+offset, O(n) slicing, performance <2s for 1000 room +500 private
- Snapshot/restore: dir mode copies all files+config, file mode combined JSON, restore verifies private, presence, counter next_id exact, users, rate_limit, ops_log, post-snapshot mutations gone
- Presence: heartbeat online, TTL expiry 3s offline, unknown user returns online false last_seen 0, wrapper checksum + corruption
- Rate limiting: burst2 rate1, 2 succeed, 3rd fails exit1 no ID/op-log side effects, per-user independent, refill after 1.5s succeeds, persistence, corruption handling
- Config: invalid JSON, shard_count≤0, duplicate, empty path, weight≤0, negative id → exit2; unknown fields tolerated
- Ops-log skips invalid lines with warning, preserves order
- Integrity: all sharded files use wrapper checksum strict, atomic via CreateTemp+Rename, global lock cleanup
- Help contains all keywords
- Concurrent: 10 same room preserves at least 9, 10 different shards at least 9 with unique IDs – hard but solvable with proper locking
