# Turn 2: Large-Scale Chat Server Support (Go)

## Background

Turn 1 implemented core chat communication with persistence. Now we need to scale to thousands of concurrent users, many rooms, and multiple nodes. Requirements from production:

- **Sharding**: rooms partitioned across shards via weighted consistent hashing (like DB sharding)
- **Rate limiting**: per-user token bucket to prevent spam (5 msg/sec default, burst 10) – large scale spam protection
- **Presence**: heartbeat with TTL to track online users across cluster
- **Pagination**: efficient history for rooms with 10k+ messages
- **Ops log**: transaction log for crash recovery and replay
- **Snapshot/restore**: for backup and horizontal scaling
- **Concurrency safety**: must handle concurrent senders without corruption or race

Turn 1 code is present via `inherit_prior_session`.

## Task – Extend Go Chat Server at `/app/` (same module), built via `go build -o <binary> .`

Must keep Turn 1 functionality working (create-room, join, leave, list-rooms, list-users, send, get-messages, send-private, get-private, list-all-users).

New global flags (keep backward compat):
- `--data` default `/app/data/chat.json` – used when no config (single file mode, for Turn1 compat)
- `--config` default `/app/config.json` – sharded mode config path (optional). If config exists and valid, sharded mode active. If not provided or missing, fallback to single file mode using `--data`.
- `--rate-limit` default off? Actually config file contains rate limit. But CLI may also accept `--rate-limit` overrides? For simplicity, implement config file mode.

### New Config File Format (for sharding)

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
    "messages_per_second": 5,
    "burst": 10
  },
  "presence_ttl_seconds": 60,
  "ops_log": "/app/data/chat_ops.log"
}
```
- `shard_count` >0, shards unique id, path non-empty, weight>0 if present else default 1 for routing.
- `rate_limit` optional, default 5/s burst 10 if missing.
- `presence_ttl_seconds` optional default 60.
- `ops_log` optional default `/app/data/chat_ops.log`.

Validation: bad config → exit 2, no stdout.

### Commands from Turn1 must still work (backward compat)

In **sharded mode** (config exists), semantics change:
- Rooms are sharded: `create-room <roomID>` creates room in its designated shard determined by weighted hash of roomID.
- `delete-room`, `join`, `leave`, `list-users`, `send`, `get-messages`, `list-rooms`, `list-all-users` must work across shards:
  - `list-rooms` unions all shards, sorted, deduped.
  - `list-users`, `send`, `get-messages` for a room operate on its designated shard only.
  - `join` creates entry in shard file; global seen_users must be tracked across shards? Persist seen_users in each shard plus union? Simpler: maintain separate file `/app/data/global_users.json` or in ops log? For sharded mode, we need global user list. Approach: each shard maintains its `seen_users` and `list-all-users` unions all shards + private global file. Implement additional file `/app/data/users.json` with checksum for global seen users, or replicate to all shards. Simplest: store seen users in each shard's data and union across shards for list-all-users (plus private messages file).
- Private messages: in large scale, private messages partitioned too? Could shard by hash of pair? Simpler: store private messages in a separate global file `/app/data/private.json` (or in ops log default) OR shard by from user hash. For determinism, implement private messages sharded similarly by `hash(min(user1,user2)+":"+max(user1,user2))` weighted? Or store in all shards? Spec for this task: **private messages stored in a dedicated file `/app/data/private.json`** (or config's private path) with same checksum format, not sharded by room hash, to simplify. But also support sharded private file per spec: if config present, private messages file path = `/app/data/private.json` regardless of shard, or if `private_path` field exists. Let's define: private messages stored in file specified by `private_path` in config (default `/app/data/private.json`).

### New Commands (must be implemented)

```
chat-server --config /app/config.json get-shard-id <roomID>           -> int, weighted hash shard id, -1 for global? For chat, no global concept, but keep -1 handling if room prefixed global: (broadcast system room)
chat-server --config /app/config.json get-shard-path <roomID>         -> path(s), single path for normal, comma-separated sorted list of all shard paths if prefixed global:
chat-server --config /app/config.json distribution                    -> JSON map shard_id (string) -> count of rooms in that shard (includes broadcast global: rooms counted in all shards if prefixed global:)
chat-server --config /app/config.json heartbeat <userID>              -> updates presence timestamp for user
chat-server --config /app/config.json get-presence <userID>           -> JSON {"user_id":"...","online":true/false,"last_seen":unix_nano, "last_seen_seconds_ago":float}
chat-server --config /app/config.json list-online                     -> JSON array sorted of online users (last_seen within TTL)
chat-server --config /app/config.json get-messages <roomID> [limit] [offset]  -> extend Turn1: support offset second optional arg, pagination. If limit omitted 0=all, offset 0. Must be efficient for large histories.
chat-server --config /app/config.json get-private <user1> <user2> [limit] [offset] -> similarly pagination
chat-server --config /app/config.json snapshot <backup_path>           -> creates backup dir or file: if backup_path is dir, create files inside; if file, create tar? Simpler: if path is file, copy combined JSON dump; if dir, dump each shard file copy + private + users. Implementation: create directory at backup_path (mkdir -p), copy each shard file + private.json + global users + ops.log to backup dir with .bak? Must succeed exit 0.
chat-server --config /app/config.json restore <backup_path>            -> restores from snapshot: if backup_path is dir, copy files back; if file containing JSON with all data, restore accordingly. Must overwrite current shards.
chat-server --config /app/config.json ops-log                           -> prints ops log as JSON array (per Turn1 extended)
```

Help must now contain additional keywords: `get-shard-id`, `get-shard-path`, `distribution`, `heartbeat`, `get-presence`, `list-online`, `snapshot`, `restore`, `ops-log`, `rate`, `presence`, `shard`, `weight`, `config`, `offset`.

Bare no args still prints help containing all Turn1 + Turn2 keywords and exit 0. Same for --help.

### Weighted Sharding Algorithm (MUST, reused from Turn1 sharding task but for rooms)

- Use MD5 big-endian mod but weighted:
  - Each shard has weight default 1 if missing. If weight present, must be >0 else config invalid exit 2.
  - Total weight sum.
  - Compute hash: MD5 of roomID UTF-8 bytes, interpret 16-byte digest as big-endian unsigned integer (big.Int.SetBytes), e.g., Python `int(md5(key.encode()).hexdigest(),16)`
  - Compute `weighted_index = hashInt % totalWeight`
  - Iterate shards sorted by id asc, subtracting weight: if weighted_index < shard.weight, pick that shard id else subtract and continue.

Example: shards 0:w1,1:w2,2:w1,3:w1 total 5 hash%5=0→0,1→1,2→1,3→2,4→3.

- For roomIDs prefixed with `global:` , `get-shard-id` returns -1 to indicate broadcast (system announcement room replicated to all shards). In chat context, a global: room's messages replicated to all shards on send, and get-messages returns first found? For simplicity, global: rooms are broadcast: create-room global:xxx creates in ALL shards, send to global:xxx writes to all shards, get-messages reads from first shard in id order that has it (or union deduped). For list-rooms, global rooms appear once even if replicated. For distribution, global rooms count in each shard. This mirrors broadcast keys from sharding task.

- `get-shard-path`: for normal room, single path; for global: room, comma-separated sorted list of all shard paths by id.

### Rate Limiting (MUST for large scale)

- Per-user token bucket:
  - Rate: `messages_per_second` (default 5), burst (default 10)
  - Each user has bucket with tokens = burst initially, refill rate = rate per second.
  - On `send` or `send-private`, check if user has >=1 token, if yes consume 1 and allow, if not, fail with stderr containing "rate limit" and exit code 1 (not 2, to distinguish from invalid args). Print nothing to stdout on rate limit failure.
  - Buckets persist in file? Must persist across CLI invocations: store in file `/app/data/rate_limit.json` with checksum format or in each shard's data `rate_limit` map? Simplest: separate file `/app/data/rate_limit.json` with same checksum format, or store in config's `rate_limit_state_path`. We'll define state file default `/app/data/rate_limit.json` (with checksum). It stores per-user last refill timestamp and tokens.
  - Must be implemented with file locking to handle concurrent access.
  - After 1 second without sends, bucket refills.
  - `send` that fails due to rate limit must NOT append to ops log and must NOT increment message IDs.
  - For tests, rate limit config may be set via config file. If no config (single file mode), rate limiting disabled? For large scale tests, we will use config mode with rate limit.
  - Must also apply to private messages.
  - Important: bucket check must be per user, not per room.

### Presence with TTL (MUST)

- `heartbeat <userID>`: updates user's last_seen to now (unix nano). Persists in file `/app/data/presence.json` (checksum format) or in global users file. Store map user_id -> timestamp nano.
- `get-presence <userID>`: returns JSON object with online boolean: online if now - last_seen <= TTL * 1e9 nanos (TTL from config, default 60s). If user never heartbeat, online false, last_seen 0 or null. Should return JSON always exit 0 even if user unknown.
- `list-online`: returns JSON array sorted of userIDs whose last_seen within TTL.
- Presence file must have checksum format, atomic writes, corruption handling same as chat file.
- For tests, we can set TTL small like 2 seconds to test expiry quickly.

### Message Pagination (large scale history)

- `get-messages <roomID> [limit] [offset]`:
  - limit optional, default 0 meaning all. If limit >0, return at most limit messages.
  - offset optional, default 0, number of messages to skip from beginning (oldest). So pagination: first page offset 0 limit 100, next page offset 100 limit 100, etc.
  - Must work efficiently for rooms with 10k+ messages: tests will create room with 5000 messages and fetch with limit 10 offset 100, should complete within 2 seconds and return correct slice.
  - Implementation must not be O(n^2) – storing messages as array and slicing is O(n) which is okay for 10k, but ensure no nested loops scanning all rooms.

- Similarly `get-private <user1> <user2> [limit] [offset]`.

### Ops Log (crash recovery)

- File `/app/data/chat_ops.log` (or config's ops_log path) – append-only JSON lines, one per line:
```json
{"op":"create-room","room":"...","ts":nanosec}
{"op":"join","room":"...","user":"...","ts":...}
{"op":"send","room":"...","user":"...","content":"...","id":1,"ts":...}
{"op":"send-private","from":"...","to":"...","content":"...","id":2,"ts":...}
...
```
- On each successful mutation (create, delete, join, leave, send, send-private, heartbeat?), append to ops log. At least for send operations must be logged.
- If ops log contains invalid JSON line (corruption), skip on read (if you implement ops-log command) and log warning to stderr.
- On `restore`, replay ops log? For simplicity, snapshot already contains full state, ops log backup included.

### Snapshot / Restore

- `snapshot <path>`: creates backup. If path is directory, mkdir -p and copy each shard file, private file, presence file, rate_limit file, users file, ops_log to that dir. If path is file, write single JSON containing combined data of all shards + private + presence + next_id etc.
- `restore <path>`: restores. If path is dir that was created by snapshot (containing shard files), copy them back to their original locations (and other files). If file, parse single JSON and restore.
- Must handle atomic writes and corruption handling on restore.

### Concurrency Safety (large scale)

- File operations must be safe for concurrent CLI invocations (tests spawn 20-50 parallel sends). Use lock file mechanism: `<file>.lock` with O_EXCL creation retry loop.
- In-memory data structures must be thread-safe if you later add HTTP server? But for CLI, file locking suffices. However also implement internal mutexes in Go code for future server mode.
- Test "concurrent sends" will spawn many processes simultaneously; final message count must equal number of successful sends, no corruption, no lost messages beyond rate limit.

### Persistence Integrity (reuse)

All persistence files (chat.json, shard_*.json, private.json, presence.json, rate_limit.json, users.json) must use same checksum format with atomic writes via CreateTemp+Rename and corruption backup `.corrupt.<nanosec>` handling.

Source inspection will check for `CreateTemp` and `Rename`.

### Help and Exit Codes

- Bare no args → help stdout with all keywords, exit 0
- --help, -h, help → same help exit 0
- Unknown command → exit 2
- Invalid args → exit 2
- Rate limit exceeded → exit 1, stderr contains "rate limit"
- I/O error → exit 1

### Constraints

- Go stdlib only, `go.mod` no external requires.
- Build via `go build -o <binary> .`
- Must support both single-file mode (Turn1) and sharded mode (Turn2). When --config missing, use --data file single mode.
- Respect both --data and --config.
- No hardcoded /tmp/chat, use /tmp/codimango if tmp needed.
- Must contain source checks for atomic writes.

### Examples Turn2

```bash
go build -o ./chat-server .

# Sharded mode setup
cat /app/config.json
# {"shard_count":4,"shards":[...],"rate_limit":{"messages_per_second":5,"burst":10},"presence_ttl_seconds":2,"ops_log":"/app/data/chat_ops.log"}

./chat-server --config /app/config.json create-room general
./chat-server --config /app/config.json get-shard-id general
./chat-server --config /app/config.json get-shard-path general
./chat-server --config /app/config.json distribution

./chat-server --config /app/config.json join general alice
./chat-server --config /app/config.json join general bob
./chat-server --config /app/config.json send general alice "Hello large scale"
./chat-server --config /app/config.json get-messages general 10 0

./chat-server --config /app/config.json heartbeat alice
./chat-server --config /app/config.json get-presence alice
./chat-server --config /app/config.json list-online

./chat-server --config /app/config.json send-private alice bob "secret"
./chat-server --config /app/config.json get-private alice bob 10 0

./chat-server --config /app/config.json snapshot /tmp/backup
./chat-server --config /app/config.json restore /tmp/backup

./chat-server --config /app/config.json ops-log
```

Implement at `/app/` – Turn1 code present.

### Success

- Turn1 features still work in sharded mode (with sharded storage)
- Sharding weighted correct, broadcast global: rooms
- Rate limiting per-user token bucket with persistence
- Presence heartbeat TTL with list-online
- Pagination efficient for 10k messages
- Ops log append, snapshot/restore
- Concurrent safety (no corruption, correct counts)
- Help contains all keywords, exit codes correct
- Integrity checksum, atomic writes
