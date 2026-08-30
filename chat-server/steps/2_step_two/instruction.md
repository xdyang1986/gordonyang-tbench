# Large-Scale Chat Server Support

You've built the core chat server with durable split-file persistence (chat.json with rooms+deleted_rooms+seen_users, private.json, counter.json) that handles rooms, users, messages, and integrity. Now we need to scale it to many rooms and users with production-grade distributed systems requirements.

The existing code is present in `/app` and should continue to work. Keep the core behaviors working in both Turn1 split-file mode and sharded modes: create-room, delete-room, purge, tombstone, list-rooms sorted, join, leave, list-users sorted, send via Join, get-messages with limit/offset handling, send-private via Join, get-private, list-all-users sorted, checksum integrity, atomic file operations, handling of spaces, global ID monotonicity, and edge validation.

## Task – Extend Go Chat Server at `/app/` (same module), built via `go build -o <binary> .`

Stdlib only.

### Flags
- `--data` default `/app/data/chat.json` – Turn1 compatibility mode, used when --config is not supplied. Persistence keeps the Turn1 split-file layout: chat.json (rooms + deleted_rooms + seen_users), private.json, counter.json.
- `--config` default `/app/config.json` – sharded mode. If --config is supplied, the file must exist and contain valid JSON that passes the validation rules below; a missing file, malformed JSON, or a validation failure exits 2. There is no silent fallback when --config is given.

### Config File Format

`/app/config.json` example:
```json
{
  "shard_count": 4,
  "shards": [
    {"id": 0, "path": "/app/data/shard_0.json", "weight": 1},
    {"id": 1, "path": "/app/data/shard_1.json", "weight": 2},
    {"id": 2, "path": "/app/data/shard_2.json", "weight": 1},
    {"id": 3, "path": "/app/data/shard_3.json", "weight": 1}
  ],
  "rate_limit": {"messages_per_second": 5, "burst": 10},
  "presence_ttl_seconds": 60,
  "ops_log": "/app/data/chat_ops.log",
  "private_path": "/app/data/private.json",
  "presence_path": "/app/data/presence.json",
  "rate_limit_path": "/app/data/rate_limit.json",
  "counter_path": "/app/data/counter.json",
  "users_path": "/app/data/users.json"
}
```

Validation rules (must exit 2 on violation):
- `shard_count` must be >0
- `shards` must be non-empty array
- Each shard: `id` must be >=0 and < shard_count, unique; `path` must be non-empty string; `weight` if present must be >0, default 1 if missing
- `rate_limit` optional default `{"messages_per_second":5,"burst":10}` where messages_per_second may be float or int, positive; burst positive
- `presence_ttl_seconds` optional, default 60; if present it must be a non-negative number — negative or non-numeric values are invalid (exit 2)
- All path fields optional with defaults as shown above
- Unknown fields at top-level or inside shard objects must be ignored (tolerant parsing): extra fields must not cause failure
- `shard_count` mismatch with `len(shards)` is lenient (must not crash, may allow)
- Invalid JSON in config file → exit 2

### Sharded Mode Data Files

All JSON files use wrapper `{"data": <Data>, "checksum": md5 canonical}` where canonical = `json.dumps(data, sort_keys=True, separators=(',',':'))` with no HTML escaping, using `SetEscapeHTML(false)` in Go.

- Each shard file (e.g., `/app/data/shard_0.json`): `Data = {"rooms": {roomID: {users: []string, messages: []Message}}, "deleted_rooms": {roomID: {users: []string, messages: []Message, deleted_at: int64}}, "seen_users": {userID: bool}}`
  - Room object identical to Turn1: must have keys `users` (sorted array) and `messages` (array sorted by id asc)
- `private_path`: `{"private_messages": []Message}`
- `presence_path`: `{"<userID>": last_seen_nano}` where map value is int64 nanoseconds from `UnixNano()`, or empty object `{}`
- `rate_limit_path`: per-user token bucket state. Wrapper checksum and atomic writes required like every other file.
- `counter_path`: `{"next_id": int64}` global counter shared across shards and private messages
- `users_path`: `{"alice": true, ...}` global seen users
- `ops_log`: append-only JSON-lines file (not wrapper), each line is a JSON object

All files: atomic via `os.CreateTemp` same dir + `os.Rename`, file locking for correctness, wrapper checksum strict, corruption handling: invalid JSON, missing/empty checksum, or checksum mismatch → backup `<path>.corrupt.<nanosec>` (nanosec digits integer from `UnixNano()`), stderr warning containing "corrupt" or "checksum", recreate empty valid file. Global lock file `/app/data/global.lock` must be used for multi-shard operations and cleaned after each command. The lock is acquired by creating it with O_CREATE|O_EXCL; if it already exists the command retries and ultimately fails rather than proceeding.

### Sharded Semantics

- Rooms are assigned to a shard via weighted consistent hashing of roomID. Global rooms with prefix `global:` are broadcast rooms with special handling.
- `create-room <roomID>`: idempotent exit0, fail exit2 if empty after TrimSpace. For normal rooms, creates entry in designated shard only. For `global:` prefix, creates room in ALL shards (replicated). Re-creating same ID after deletion starts empty and does NOT clear existing tombstone. Must handle empty ID exit2.
- `delete-room <roomID>`: tombstone – retains history, prints true/false, exit0 even if room not exist or global (applies to all shards if global) . After deletion room behaves as deleted.
- `purge <roomID>`: removes tombstone, prints true/false, exits 2 if no tombstone exists.
- `join <roomID> <userID>`: For normal rooms, fails exit2 if room does not exist in its shard. For global rooms, operates on all shards (creates membership in each). Idempotent exit0 otherwise. Empty IDs exit2. Tracks global seen_users.
- `leave <roomID> <userID>`: Idempotent exit0 even if room or user does not exist. For normal rooms, removes from its shard; for global, removes from all shards. After leaving all users, `list-users` must return `[]`. `send` after leaving all must fail exit2.
- `list-rooms`: unions all shards, deduplicated, sorted lexicographically. Must handle many rooms sorted.
- `list-users <roomID>`: for normal room, returns users from its shard; for global, unions across shards (since room replicated in all shards) sorted. Exit2 if room does not exist.
- `send <roomID> <userID> <message>`: Requires membership (user in room) else exit2. Message obtained via `strings.Join(remainingArgs, " ")`, requires message else exit2. For normal room, appends to that shard's room with global unique ID from counter; for global, replicates same message (same ID) to all shards. Prints message JSON. Must handle large messages (10KB), special chars `<>&` without HTML escaping, Unicode emoji preservation.
- `get-messages <roomID> [limit] [offset]`: Returns messages sorted by id asc. Pagination: `offset` default 0, `limit` default 0 meaning all. If `limit>0`, returns messages starting from offset limited to limit count; else returns from offset onward. Invalid limit/offset (non-int, negative) → exit2. Nonexistent room → `[]` exit0 (not error). For global rooms, must dedupe by ID (since replicated) and return sorted unique list. Must be efficient for large histories.
- Private messages stored in `private_path` file, with global lock, atomic, checksum, corruption handling. `send-private <from> <to> <msg>` always allowed (no membership requirement), tracks seen users, message via Join, requires message else exit2, handles spaces, special chars, Unicode, large 10KB.
- `get-private <u1> <u2> [limit] [offset]`: Both directions, sorted asc, same pagination semantics as `get-messages`, limit/offset validation exit2, limit zero all, isolation between conversation pairs.

### Tombstone deletion

delete-room retains history: the room's members and messages move to deleted_rooms under the room ID with a deleted_at nanosecond timestamp. A tombstoned room behaves as though it does not exist for every command except list-all-users, which still reports everyone who was ever a member. Re-creating a room with same ID after deletion starts empty and does NOT clear existing tombstone. purge <roomID> is the only way to remove a tombstone. deleted_rooms participates in wrapper checksum and corruption recovery like any other field.

### Rate Limiting

Token bucket per user, single bucket shared across all message sends (`send` and `send-private` share the same quota per user).

- State: per user `tokens = burst` initially, `last_refill = now nano`
- Refill: `elapsed = (now - last_refill)/1e9` seconds, `tokens = min(burst, tokens + elapsed*rate)`, update `last_refill=now`
- Consume: if `tokens >=1`, `tokens -=1`, allow and persist; else fail rate-limited, persist refilled tokens
- Per-user independent (bob succeeds when alice limited)
- Persistence in `rate_limit_path` wrapper checksum, atomic via CreateTemp+Rename, corruption handling: invalid JSON or checksum mismatch → reset bucket (allow next send to succeed)
- Exit semantics: if rate-limited, exit code 1, stderr must contain case-insensitive "rate limit", no stdout, must NOT increment global `next_id` (counter) and must NOT append to ops log
- Config: `rate_limit.messages_per_second` may be fractional (e.g., 0.05 means 1 token per 20s), so Go struct should use float64 for this field.

### Presence

- `heartbeat <userID>`: updates `last_seen` to `UnixNano()` in `presence.json` wrapper checksum, atomic with global lock
- `get-presence <userID>`: returns JSON `{"user_id": string, "online": bool, "last_seen": int64, "last_seen_seconds_ago": float}` where `online = (now - last_seen) <= TTL*1e9`. TTL from config `presence_ttl_seconds` default 60. If never seen, `online=false`, `last_seen=0`, `last_seen_seconds_ago=0`.
- `list-online`: sorted array of user_ids where online within TTL
- Corruption handling: checksum mismatch or invalid JSON → treat as empty (offline), backup and warning similar to other files
- TTL expiry must be testable with short TTL (2s) and sleep.

### Ops Log

Append-only file at `ops_log` path.

Each line is a JSON object (JSON-lines, not JSON array), with at least:
- `op`: string operation type, e.g., `create-room`, `delete-room`, `join`, `leave`, `send`, `send-private`, `heartbeat`
- `ts`: int64 nanoseconds timestamp
- Additional fields (room, user, etc.) may be present

`ops-log` command:
- Reads the log file, skips invalid JSON lines, prints warning to stderr containing case-insensitive "corrupt" or "skip" or "warning" for each skipped line
- Prints remaining valid entries as JSON array (preserving original file order) to stdout, exit0
- Must handle large logs
- Content must preserve order, and must include entries for `create-room`, `join`, `send`, `send-private` in order of execution

### Snapshot / Restore

- `snapshot <backup_path>`:
  - Dir mode: if path does not end with `.json` or is an existing directory, `mkdir -p` and copy all shard files (if exist), private, presence, counter, users, rate_limit, ops_log, and config.json into backup dir preserving basename
  - File mode: if path ends with `.json`, writes combined JSON file with keys `shards` (map shard_id string → ShardFileData), `private`, `presence`, `rate_limit`, `counter`, `users`, `ops_log` where `ops_log` is a JSON array of the parsed valid log entries. Must include counter exact value so that restore can set `next_id` precisely.
- `restore <backup_path>`:
  - Dir mode: copy files back, overwriting, via atomic writes
  - File mode: reads combined JSON and restores each component via atomic writes
  - After restore, counter `next_id` must be exactly snapshot value so next send gets expected ID, and post-snapshot mutations (new rooms, users, private msgs) must be gone

### New Commands

```
get-shard-id <roomID>        -> prints int shard id, -1 for global: rooms, weighted hash defined below
get-shard-path <roomID>      -> for normal room, single path; for global: returns comma-separated sorted list of all shard paths (sorted lexicographically)
distribution                 -> JSON map shard_id (as string) -> count rooms including global rooms counted in each shard (e.g., 1 normal + 2 global * 4 shards = 9 total count across shards)
get-messages <roomID> [limit] [offset] -> offset pagination as defined
get-private <u1> <u2> [limit] [offset] -> offset pagination
heartbeat <userID>           -> updates presence
get-presence <userID>        -> JSON with online per TTL, unknown returns false last_seen 0
list-online                  -> sorted online users within TTL
snapshot <backup_path>       -> creates backup dir or file
restore <backup_path>        -> restores from backup
ops-log                      -> prints ops.log as JSON array, skips invalid lines with warning
```

Help must contain keywords: `create-room`, `delete-room`, `purge`, `list-rooms`, `join`, `leave`, `list-users`, `send`, `get-messages`, `send-private`, `get-private`, `get-shard-id`, `get-shard-path`, `distribution`, `heartbeat`, `get-presence`, `list-online`, `snapshot`, `restore`, `ops-log`. Bare no args → help exit0.

### Weighted Sharding Algorithm

- Weight default 1 if missing, must be >0 else invalid config exit2
- Total weight = sum weights
- Hash: MD5 of roomID bytes, interpreted as big-endian integer: Python `int(hashlib.md5(key.encode()).hexdigest(),16)`, Go equivalent using `math/big` `SetBytes(md5.Sum([]byte(key))[:])`
- `weighted_index = hashInt % totalWeight`
- Iterate shards sorted by id asc subtracting weight: if weighted_index < shard.weight → pick that shard id else subtract weight and continue
- `global:` prefix → shard id -1 broadcast, get-shard-path returns comma-separated sorted list of all shard paths

### Shard Assignment Stability (Sticky)

A room's shard is decided once, at creation, and recorded in the store. `get-shard-id`, `get-shard-path`, `distribution`, and all read/write paths must use the recorded assignment for existing rooms. If the config's shard weights or shard set change afterwards, existing rooms MUST stay on their original shard — they are never rehashed or migrated. Only rooms created after the change use the new weights. `global:` rooms remain in all shards. The assignment must survive restarts and be included in snapshots.

### Pagination

- `get-messages <roomID> [limit] [offset]`: 
  - With 1 arg (roomID): returns all messages sorted by id asc
  - With 2 args (roomID, limit): if limit==0 returns all, if limit>0 returns latest N – preserves Turn-1 behavior for backward compatibility
  - With 3 args (roomID, limit, offset): offset defaults to 0, returns messages starting from offset, limited to limit when positive, otherwise from offset onward – extended form for sharded mode
  - Same rules apply to `get-private`
- Must handle spaces via Join
- Must be efficient and avoid quadratic behavior for large histories

### Integrity & Concurrency

- All persistence files must use wrapper checksum MD5 canonical `json.dumps(data, sort_keys=True, separators=(',',':'))` `SetEscapeHTML(false)`
- Atomic via CreateTemp+Rename, file locking with retry, lock cleanup
- Corruption handling for all files as described
- Concurrent behavior:
  - Same room concurrent sends must preserve all messages with unique global IDs, file must remain valid JSON during concurrent execution, no lock files left
  - Different rooms concurrent sends hashing to different shards must preserve all with unique IDs using global lock to avoid counter races
  - Concurrent joins must preserve all users sorted

### Edge handling

Identifier validation happens before any existence check or idempotent handling: if a roomID or userID argument is empty after strings.TrimSpace, the command exits 2 without reading or writing state, including for otherwise-idempotent commands (delete-room, leave, purge) and read commands (list-users, get-messages, get-private, get-presence, get-shard-id, get-shard-path, heartbeat).

### Exit Codes
0 success, 1 I/O or rate-limited (rate limit → exit1 stderr "rate limit"), 2 invalid input (bad config, room not exist for join, empty IDs, invalid limit/offset, missing message). Leave idempotent exit0 even if room not exist. Empty ID precedence: blank after TrimSpace is exit2, not the idempotent []/false path.

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
./chat-server --config /app/config.json ops-log
```
