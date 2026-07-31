# Turn 2: Large-Scale Chat Server Support (Go) – Extra Hard (60 tests)

Turn1 implemented core chat communication. Now we need to scale to many rooms and users with production-grade distributed systems requirements. This turn is extra hard: 60 tests, 20 concurrent all 20, weighted sharding 20/50/100, global broadcast replication dedup, rate limiting with refill multiple cycles and no side effects, presence TTL multi-user, pagination 1000/2000 offset, snapshot dir+file all files with counter exact restore, checksum all files strict, corruption handling all files.

Turn1 code is present via `inherit_prior_session`.

## Task – Extend Go Chat Server at `/app/` (same module), built via `go build -o <binary> .`

Must keep Turn1 functionality working (create-room, delete-room, list-rooms sorted, join, leave idempotent, list-users sorted, send via Join, get-messages latest N + limit/offset, send-private via Join, get-private, list-all-users sorted, checksum strict, atomic all 10/20, lock cleanup, spaces, global ID, edge validation, 100/200 rooms, large history).

### Flags
- `--data` default `/app/data/chat.json` – single-file mode (Turn1 compat)
- `--config` default `/app/config.json` – sharded mode config path. If config file exists and valid, sharded mode active, else fallback to single-file.

### Config File Format (for sharding, MUST - Extra Hard)

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
  "rate_limit": {"messages_per_second": 1000, "burst": 10000},
  "presence_ttl_seconds": 60,
  "ops_log": "/app/data/chat_ops.log",
  "private_path": "/app/data/private.json",
  "presence_path": "/app/data/presence.json",
  "rate_limit_path": "/app/data/rate_limit.json",
  "counter_path": "/app/data/counter.json",
  "users_path": "/app/data/users.json"
}
```
- `shard_count` >0 else exit2, shards unique id, path non-empty, weight>0 else exit2, negative id exit2, duplicate id exit2, empty path exit2. shard_count mismatch with len(shards) lenient not crash (may allow).
- `rate_limit` optional default `{"messages_per_second":5,"burst":10}`
- `presence_ttl_seconds` optional default 60
- `ops_log`, `private_path`, `presence_path`, `rate_limit_path`, `counter_path`, `users_path` optional defaults as above
- **Unknown fields must be ignored** – tolerant: extra fields top-level and inside shards must be ignored, still allow `create-room` succeed. Tests verify `future_field`, `unknown_top_level`, `future_shard_field` ignored.

Validation: bad config (invalid JSON, shard_count≤0, duplicate id, empty path, weight≤0, negative id) → exit2.

### Sharded Mode Semantics
- Rooms sharded via weighted hash of roomID
- `create-room <roomID>`: idempotent, creates in designated shard, if `global:` prefix creates in ALL shards (broadcast), must handle empty ID exit2
- `delete-room`, `join`, `leave`, `list-users`, `send`, `get-messages` work across shards: `list-rooms` unions all shards sorted deduped (must handle 200 rooms), `join`/`leave` idempotent even if room not exist in that shard? Actually for normal rooms fail exit2 if room not exist; for global rooms join/leave in all shards. Must handle empty IDs exit2, 20 concurrent joins preserve all 20 sorted, leave all → [] and send after leave fails. `send` requires member else exit2, message via Join, missing message exit2, large message 10KB, special chars `<>&` no escape, Unicode emoji preserved. `get-messages` sorted id asc, nonexist → [] not error, limit latest N? For sharded, pagination is offset: `get-messages <room> [limit] [offset]` limit 0/omit=all, offset 0 default, returns `sorted[offset:offset+limit]` if limit>0 else `[offset:]`, invalid limit/offset exit2, limit zero returns all, performance 1000 and 2000 msgs <2s.

- Private messages stored in file at `private_path` wrapper checksum, atomic, global lock, corruption handling, must handle spaces via Join, special chars `<>&` and Unicode, large message 10KB, isolation, both directions, limit/offset pagination, invalid limit exit2.

### Rate Limiting (extra hard)

- Token-bucket per-user: tokens=burst initially, last_refill=now nano
- Refill: elapsed=(now - last_refill)/1e9, tokens=min(burst, tokens+elapsed*rate), last_refill=now
- Consume: if tokens≥1, tokens-=1 allow, persist; else fail rate limited, persist refilled tokens
- Per-user independent
- Persistence path `rate_limit_path` wrapper checksum, atomic via CreateTemp+Rename, corruption handling same as other files, global lock `/app/data/global.lock` with cleanup
- Exit code / stderr: if rate-limited exit1 stderr contains "rate limit" case-insensitive no stdout, must NOT increment message IDs and must NOT append to ops log
- Tests extra hard: burst2 rate1, 2 succeed, 3rd fails exit1 no side effects, per-user independent (bob succeeds when alice limited), **refill after 1.6s** succeeds, **multiple cycles** 2 succeed fail sleep 1.2s succeed fail sleep 1.2s succeed, persistence across invocations (file contains bucket), corruption handling for rate_limit.json (invalid JSON → bucket reset → send succeeds)

### Presence (extra hard)

- `heartbeat <userID>`: updates last_seen nano in `presence.json` wrapper checksum atomic global lock
- `get-presence <userID>`: `{"user_id","online":bool,"last_seen":nano,"last_seen_seconds_ago":float}` where `online = now - last_seen <= TTL*1e9`, TTL from config default 60, if never heartbeat online false last_seen0 last_seen_seconds_ago0
- `list-online`: sorted online users within TTL
- Tests extra hard: heartbeat→online, **TTL expiry 2s→3s sleep** offline and list-online excludes, **unknown user** returns online false last_seen0, **multiple users TTL** 3 users online, 3s sleep → [] empty, heartbeat bob → [bob], corruption handling for presence.json (checksum mismatch → offline), wrapper checksum strict

### New Commands (MUST)

```
get-shard-id <roomID>        -> int, weighted hash, -1 for global:
get-shard-path <roomID>      -> path single for normal, comma-separated sorted list for global:
distribution                 -> JSON map shard_id (string) -> count rooms including global in each shard (1 normal+2 global*4=9 for 3 rooms), handles 200 rooms
get-messages <roomID> [limit] [offset] -> offset pagination, limit0=all, offset0 default, performance 1000 and 2000 <2s
get-private <u1> <u2> [limit] [offset] -> offset pagination, isolation, both directions, limit0=all, performance 500 privates + large 500 offset250
heartbeat <userID>           -> updates presence
get-presence <userID>        -> JSON with online per TTL, unknown returns false last_seen0
list-online                  -> sorted online within TTL, handles multi-user TTL expiry
snapshot <backup_path>       -> dir mode: mkdir -p and copy shard files+private+presence+counter+users+rate_limit+ops_log+config; file mode: combined JSON file with shards map, private, presence, rate_limit, counter, users, ops_log
restore <backup_path>        -> dir and file modes restore all files via atomic writes, must restore counter next_id exactly so next send gets expected ID, post-snapshot mutations gone
ops-log                      -> prints ops.log as JSON array, skips invalid JSON lines with warning stderr "corrupt"/"skip"/"warning", preserves order, content checks op types (create-room, join, send, send-private) order, large 100 ops
```

Help must contain: `create-room`, `delete-room`, `list-rooms`, `join`, `leave`, `list-users`, `send`, `get-messages`, `send-private`, `get-private`, `get-shard-id`, `get-shard-path`, `distribution`, `heartbeat`, `get-presence`, `list-online`, `snapshot`, `restore`, `ops-log`
Bare no args → help exit0.

### Weighted Sharding Algorithm (MUST)

- Weight default 1 if missing, must be >0 else invalid config exit2
- Total weight = sum weights
- Hash: MD5 bytes, big-endian int: Python `int(md5(key.encode()).hexdigest(),16)`
- `weighted_index = hashInt % totalWeight`
- Iterate shards sorted by id asc subtracting weight: if weighted_index < shard.weight → pick that shard id else subtract
- Example: 0:w1,1:w2,2:w1,3:w1 total5 → 0→0,1→1,2→1,3→2,4→3
- `global:` prefix → -1 broadcast, `get-shard-path` returns comma-separated sorted list of all shard paths

### Pagination – Extra Hard

- `get-messages <roomID> [limit] [offset]`: limit0=all, offset0 default, `sorted[offset:offset+limit]` if limit>0 else `[offset:]`
- Similarly `get-private`
- Must handle spaces via Join
- Must work for 50, 500, 1000, 2000 messages quickly (<2s) – O(n) slicing not O(n^2), large history tests 1000 bulk500 and 2000 bulk1000
- Global ID monotonic: pagination preserves global ID order

### Snapshot/Restore – Extra Hard

- `snapshot <backup_path>`: dir mode (no .json suffix or existing dir): mkdir -p, copy each shard file (if exists), private_path, presence_path, counter_path, users_path, rate_limit_path, ops_log into backup dir basename preserved plus config; file mode (path ends with .json): writes combined JSON file with keys shards map (shard_id->ShardFileData), private, presence, rate_limit, counter, users, ops_log
- `restore <backup_path>`: dir mode copy files back overwrite, file mode reads combined JSON and restores each component via atomic writes; after restore private, presence, counter next_id, users, rate_limit, ops_log must be exactly as snapshot time (tested via exact file content equality and that post-snapshot mutated data gone, list-all-users no dave, list-rooms no newroom, and next send gets expected ID counter_before)
- Exit0, must handle global lock

### Integrity & Concurrency – Extra Hard (60 tests)

- Persistence files must use wrapper `{"data":..., "checksum":...}` checksum MD5 canonical `json.dumps(data, sort_keys=True, separators=(',',':'))` `SetEscapeHTML(false)`, atomic via CreateTemp+Rename, tests verify checksum for all sharded files strict
- Corruption handling for all files: invalid JSON → backup `<path>.corrupt.<nanosec>` integer, stderr warning "corrupt" or "checksum", recreate empty valid file. Tests for shard files, private.json, rate_limit.json, presence.json, etc.
- Missing checksum and mismatch → corruption handling
- Atomic behavior extra hard:
  - Same room: 20 concurrent sends, file must remain valid JSON, IDs unique, no partial writes, preserve **all 20 messages** (extra hard), global.lock cleaned
  - Different rooms: 20 concurrent sends to 20 different rooms hashing to different shards must preserve **all 20** with unique global IDs (multi-shard atomic via global lock)
- Stdlib-only imports, advisory CreateTemp+Rename
- Ops-log invalid line skipping with warning, order preserved, content order and large 100 ops
- Config validation and unknown-field tolerance: malformed configs exit2, unknown fields ignored, defaults for missing optional, shard_count mismatch lenient not crash
- Weighted distribution 20 exact, 50 tolerance, 100 tolerance (40% weight)
- Global broadcast: create in all shards, send replicates same ID to all shards, get-messages dedupes by ID (1 msg for global, 5 for multiple), distribution counts global in each shard
- Spaces handling: message content with spaces via Join remaining args (tested for both room and private sharded, 10KB large message)
- Edge: empty room/user ID exit2, missing message exit2, invalid limit/offset exit2, nonexist returns [], leave all empty, private special chars `<>&` and Unicode

### Exit Codes
0 success, 1 I/O or rate-limited (rate limit → exit1 stderr "rate limit"), 2 invalid input (bad config, room not exist for join, empty IDs, invalid limit, missing message). Leave idempotent exit0 even if room not exist.

### Examples
```bash
go build -o ./chat-server .
./chat-server --config /app/config.json create-room general
./chat-server --config /app/config.json get-shard-id general
./chat-server --config /app/config.json get-shard-path general
./chat-server --config /app/config.json distribution
./chat-server --config /app/config.json join general alice
./chat-server --config /app/config.json send general alice Hello large scale with spaces
./chat-server --config /app/config.json get-messages general 10 0
./chat-server --config /app/config.json heartbeat alice
./chat-server --config /app/config.json get-presence alice
./chat-server --config /app/config.json list-online
./chat-server --config /app/config.json snapshot /tmp/backup
./chat-server --config /app/config.json restore /tmp/backup
```

Implement at `/app`.

### Success – Extra Hard (60 tests)
- Turn1 features still work in sharded mode, including spaces Join, global ID monotonic, checksum strict all files, atomic all 20 concurrent same+diff rooms+20 joins, lock cleanup, 200 rooms, 1000 history, Unicode, special chars
- Weighted sharding correct, global broadcast replication dedup, distribution counts global*shard_count, 20 exact, 50 and 100 tolerance
- Pagination extra hard: 50, 500, 1000, 2000 msgs offset for both get-messages and get-private, performance <2s
- Snapshot/restore dir and file modes all files+counter exact, post-mutation gone
- Presence: heartbeat online, TTL expiry 3s offline, unknown user false last_seen0, multi-user TTL, wrapper checksum+corruption
- Rate limiting: burst2 rate1, 2 succeed 3rd fails exit1 no side effects per-user independent, refill 1.6s, multiple cycles 1.2s, persistence, corruption handling
- Config: invalid JSON, shard_count≤0, duplicate, empty path, weight≤0, negative id → exit2, unknown fields tolerated, defaults, mismatch lenient
- Ops-log: invalid skipping warning, content order, large 100 ops
- Help contains all keywords, bare no args → help exit0
- Concurrent: 20 same room all 20, 20 diff rooms all 20, 20 joins all 20 with unique IDs – extra hard
- 60 tests extra hard, oracle 100% but low model pass rate
