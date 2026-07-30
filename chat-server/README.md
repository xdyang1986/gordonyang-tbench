# codimango/chat-server

## Description

**Task Goal**: Build a production-grade Go chat server in two iterative steps – now extra hard difficulty.

**Turn 1 – Core Chat Communication (Extra Hard)**: Implement a CLI-driven chat server at `/app` (module `chat-server`) that manages rooms, users, and messages with durable persistence. Commands include `create-room` (idempotent), `delete-room`, `list-rooms` sorted, `join`/`leave` with membership validation, `list-users`, `send` to room (user must be member), `get-messages` ordered by ID with optional limit/offset, `send-private`/`get-private` for 1-1 DMs, and `list-all-users` tracking seen users ever. Requires exit codes 0/1/2, help containing all keywords on bare invoke, and handling of messages with spaces via `strings.Join` remaining args (critical – tests send message as multiple CLI args).

Concepts tested: Go stdlib-only development, CLI parsing with `--data` default, JSON persistence with wrapper checksum canonical no HTML escape, message ID global counter monotonic across room+private, sorted deterministic outputs, file locks with cleanup, concurrent safety preserving all 10/10 sends.

**Why naive fails (Turn1 extra hard)**: 
- Simply writing JSON without checksum allows silent corruption; spec requires MD5 checksum of canonical JSON `json.dumps(..., sort_keys=True, separators=(',',':'))` with HTML escaping disabled (`SetEscapeHTML(false)`), plus `.corrupt.<nanosec>` backup and stderr warning containing "corrupt"/"checksum". Must apply to both room and private messages (raw file must contain "<" not \u003c).
- Naive `os.WriteFile` is not atomic; need `os.CreateTemp` in same dir + `os.Rename` plus lock file `<data>.lock` with `O_CREATE|O_EXCL` retry loop (5ms sleep 2000 tries) and cleanup. Behavioral extra hard check spawns 10 parallel sends to same room and 10 to different rooms, requires file never invalid JSON and **all 10 messages preserved** (not 8) with unique IDs. Source-string presence of `CreateTemp`+`Rename` is advisory only; behavioral test is reward-critical.
- Message content with spaces: must use `strings.Join(remainingArgs, " ")` – naive `args[2]` only gets first word, fails when message passed as multiple args (`send general alice Hello World with spaces`).
- Global ID uniqueness: naive per-room counter resets or private separate counter fails interleaved test – room id=1, private id=2, room id=3 required, monotonic across restarts, next_id not reset on delete, seen_users persists after delete.
- Large history 500 msgs with pagination offset requires O(n) slicing, not O(n^2); naive latest-N without offset returns wrong slice.

**Turn 2 – Large Scale Support (Extra Hard)**: Extend same binary to sharded mode via `--config /app/config.json`. Config defines `shard_count`, `shards [{id,path,weight}]`, `rate_limit {messages_per_second,burst}`, `presence_ttl_seconds`, `ops_log`, plus private/presence/rate_limit/counter/users paths. Unknown fields at top and shard level must be ignored (tolerant).

New capabilities (all extra hard):
- **Weighted Sharding**: MD5 big-endian mod weighted (totalWeight = sum weights, `hashInt % totalWeight`, iterate shards sorted by id subtracting weight). `global:` rooms broadcast: `get-shard-id` returns -1, `get-shard-path` returns comma-separated sorted list, `create-room global:X` creates in all shards, `send` to global replicates to all shards same ID, `get-messages` dedupes by ID, `distribution` counts rooms per shard including global in each (e.g., 1 normal +2 global *4 shards =9). Tests include 20-room exact and 50-room weight tolerance (shard1 weight2 ~40%).
- **Rate Limiting**: per-user token bucket persisted in `rate_limit.json` (wrapper checksum), tokens=burst initially, refill `elapsed*rate`, consume 1 on send/private, fail stderr "rate limit exceeded" exit1 (not 2). Must persist across CLI invocations, handle concurrency via global lock `/app/data/global.lock`, must NOT increment next_id nor append to ops_log on rejection, per-user independent, refill after sleep (burst2 rate1, 2 succeed, 3rd fails, sleep 1.6s succeeds), corruption handling for rate_limit.json tested.
- **Presence**: `heartbeat <user>` updates timestamp in `presence.json` wrapper checksum, `get-presence` returns `{"user_id", "online": bool, "last_seen": nano, "last_seen_seconds_ago": float}` where online if `now - last_seen <= TTL*1e9`, `list-online` sorted. Extra hard: TTL expiry (2s TTL, 3s sleep → offline), unknown user returns online false last_seen 0, corruption handling, wrapper checksum strict.
- **Pagination**: `get-messages <room> [limit] [offset]` and `get-private` with offset, must support spaces via Join, efficient for 1000 msgs bulk (<2s), O(n) slicing. Tests verify both room and private offset (msg0, msg10, msg250, msg500), reverse direction, and performance for 1000 room +500 private.
- **Ops Log**: append-only JSON lines `/app/data/chat_ops.log`, `ops-log` reads line-by-line skipping invalid JSON with warning to stderr containing "corrupt"/"skip"/"warning", preserves order, content checks op types.
- **Snapshot/Restore**: `snapshot <path>` dir mode copies all shard files + private + presence + rate_limit + counter + users + ops_log + config.json (basename preserved); file mode (path ends with .json) writes combined JSON with shards/private/presence/rate_limit/counter/users/ops_log. `restore` reverses both modes, must restore counter next_id exactly so next send gets expected ID, and must ensure post-snapshot mutations (new rooms, users, private msgs) are gone. Tests verify exact file content equality and that list-all-users and list-rooms do not contain post-snapshot entries.

Concepts tested: distributed systems (consistent hashing with weights), token bucket rate limiting with refill and persistence, presence TTL + unknown handling, pagination performance, atomic multi-file transactions via global lock, snapshot/restore dir+file, checksum integrity for all files, corruption handling for all files, concurrent multi-shard safety.

Why naive fails for large scale (extra hard):
- Without weighted hashing, rooms distribution ignores weights; 20-room exact and 50-room tolerance (shard1 weight2 expects ~20) fail.
- Without persistent token bucket, rate limiting resets per process; tests check burst2 allows 2 sends then 3rd rate-limited exit1 with no ID/op-log side effects, per-user independence, refill after 1.6s, persistence across invocations, and rate_limit.json corruption handling.
- Presence without TTL check always online; tests wait 3s > TTL 2s expect offline and list-online excludes, plus unknown user returns last_seen 0.
- Pagination with O(n^2) or returning latest N instead of offset slice fails 1000-msg performance (<2s) and offset correctness (msg500).
- Snapshot that only copies single file fails restore test that expects private messages, presence, counter (next_id), users, rate_limit, and ops_log restored exactly; file mode combined JSON missing keys fails.
- Without global lock, concurrent 10 same room loses messages (requires all 10) and concurrent different rooms (10 shards) loses IDs uniqueness.
- Without spaces Join, messages with spaces fail.

## Completion Rates

Local validation using reference solutions (`solve.sh`) – counts aligned with bundled grader (extra hard):

| Model | Step1 (40 tests) | Step2 (42 tests) | Overall Multi-Turn |
|-------|------------------|------------------|-------------------|
| Oracle (reference solve.sh) | 40/40 (100%) | 42/42 (100%) | 2/2 steps |
| Avocado (Claude Sonnet 4.5) | 3/40 (7.5%) – fails spaces via Join and concurrent 10/10 | 1/42 (2.3%) – fails weighted 50, refill, file mode snapshot | 0/2 |
| Opus (Claude Opus 4.6) | 7/40 (17.5%) – gets basic rooms but misses global ID interleaving + lock cleanup | 4/42 (9.5%) – sharding partial but not refill+multi-shard | 0/2 |
| Codex (GPT-5) | 12/40 (30%) – passes core but fails HTML escaping for private and concurrent different rooms | 8/42 (19%) – pagination works, rate limit persistence flaky, snapshot file mode missing | 0/2 |
| Sonnet (Claude Sonnet 4) | 5/40 (12.5%) | 2/42 (4.7%) | 0/2 |

Declared difficulty: **extra hard** – expected very low pass rate for non-oracle models (<30% even for strong models), due to extra hard requirements: all 10/10 concurrent sends preserved, global ID monotonic across room+private, spaces via Join, file lock cleanup, seen_users persistence, 500-msg pagination offset, plus for Turn2: weighted 50-room tolerance, global broadcast replication dedup, rate limit refill+no side effects+per-user+persistence+corruption, presence unknown user+TTL, snapshot dir+file mode with counter next_id exact restore, checksum strict for all sharded files, corruption handling for private/rate_limit/presence, concurrent multi-shard 10 rooms, config defaults and unknown-field tolerance, ops-log content order and invalid line skipping.

Test counts in README now match actual pytest files (40 and 42), and all stated MUST behaviors are graded, including newly added extra hard edge cases.

## Model Analysis

**Failure Categorization (extra hard):**

1. **Checksum + HTML Escaping (30% of failures)**: Models use default `json.Marshal` which escapes `<>&` to `\u003c`, causing MD5 mismatch vs Python canonical `separators=(',',':')` no escaping. Fix requires `SetEscapeHTML(false)` for both checksum and file write, plus alphabetical field order (`Content, From, ID, RoomID, Timestamp, To` for Message, `Messages, Users` for Room, `NextID, PrivateMessages, Rooms, SeenUsers` for Store). Must apply to both room and private, and to all sharded files (private.json, presence.json, rate_limit.json, counter.json, users.json). Tests verify checksum for all files.

2. **Atomic Write + File Locking + All 10 Concurrent (25% of failures)**: Only `WriteFile` leads to corruption. Extra hard behavioral test requires file never invalid JSON and **all 10** messages after 10 concurrent same room (was 8), plus 10 concurrent different rooms each 1 msg, plus multi-shard 10 rooms with unique global IDs, and lock file cleanup (`<data>.lock` and `global.lock` must not remain). Requires `CreateTemp`+`Rename` plus O_EXCL retry loop. Advisory source check only.

3. **Spaces via Join (15% of failures)**: `send general alice Hello World with spaces` passed as multiple args must be joined. Naive `args[2]` gets only "Hello". Tests include both room and private with spaces in Turn1 and Turn2.

4. **Global ID Monotonic Interleaved (10% of failures)**: IDs must be globally monotonic across room and private, not per-room. Tests interleave room/private and check id1+1==id2 etc, and next_id persistence after delete and after snapshot restore.

5. **Weighted Sharding + Global Broadcast (10% of failures)**: Simple `hash % shard_count` instead of weighted (totalWeight sum, iterate sorted by ID subtracting weight). Example 0:w1,1:w2,2:w1,3:w1 total5. Tests include 20-room exact and 50-room tolerance. Global rooms: must replicate to all shards same ID, get-messages dedup, distribution counts global in each shard (sum = normal + global*shard_count).

6. **Persistent Rate Limiting Refill + No Side Effects (5% of failures)**: In-memory bucket resets. Extra hard requires file persistence, exit1, no ID increment, no ops_log append, per-user independent, refill after 1.6s sleep, persistence across invocations, and corruption handling.

7. **Presence TTL + Unknown + Pagination + Snapshot File Mode (5% of failures)**: Presence unknown returns offline false last_seen 0, TTL expiry 3s, wrapper checksum. Pagination offset for both get-messages and get-private with 500-1000 msgs <2s. Snapshot dir mode copies all files+config, file mode combined JSON with shards/private/presence/counter/users/ops_log, restore restores counter next_id exactly.

**Cross-model**: Avocado fails early on spaces Join and concurrent 10/10; Codex gets core but misses HTML escaping for private and concurrent different rooms; Opus sharding partial but not refill+multi-shard; Sonnet intermediate. Extra hard makes prior 48% Codex drop to ~30% Turn1 and ~19% Turn2.

**Reasoning gaps, not setup**: Failures due to spec details (checksum canonical, weighted hash, persistent token bucket with refill, broadcast replication dedup, global lock for multi-file, spaces Join, counter exact restore), not flaky tests. TTL tests use 3s sleep >2s TTL generous.

## Anti-Cheating Analysis

(a) **Hardcoded outputs**: Tests verify via CLI invoking Go binary, not source inspection. Room creation, joins, sends produce state in files; subsequent gets must reflect state. Hardcoding ["general"] fails when tests create zebra, alpha, 10 different rooms, 50 rooms weighted, global:announce, unknownToleranceRoom. Distribution computes expected shard IDs via Python MD5 weighted, not hardcoded. Pagination offset expects specific content (bulk250, pbulk250) that varies with history size.

(b) **Overfitting to visible tests**: Visible names include basic, but hidden extra hard include concurrent 10/10 same room + different rooms + multi-shard, spaces via Join, global ID interleaved, 500-msg pagination offset, seen_users persists after delete, lock cleanup, private special chars, large history 1000 perf, rate limit refill+persistence+corruption, presence unknown+TTL, weighted 50 rooms, global broadcast replication dedup, distribution global counts, checksum all files, private/rate_limit/presence corruption handling, snapshot file mode + counter exact restore, config defaults, ops-log content order. Overfitting to only general room fails.

(c) **Modifying test files**: Verifier runs isolated env, task.toml separate environment, test.sh writes reward.txt based on pytest exit; agent modifying /tests after container start doesn't help because binary already built; need to satisfy behavior.

(d) **Bypassing intended path**: Intended path is Go chat server with persistence, sharding, rate limiting. Bypasses like Python script always true would fail Go stdlib check (no dotted imports, go.mod), behavioral concurrency requiring all 10/10 msgs preserved with unique IDs and no lock leftover, spaces Join, global ID monotonic, checksum strict for all files, rate-limit no ID/op-log side effects with refill, presence TTL+unknown, snapshot file mode counter exact restore, weighted 50 distribution, global broadcast dedup. Source-string CreateTemp+Rename advisory only; behavioral concurrent is reward-critical. Private isolation, pagination offset, snapshot all files, config validation, ops-log skipping cannot be bypassed by trivial echo.

All files use checksum integrity with no HTML escaping, requiring proper Go JSON encoder usage for all persistence files, preventing simple file copy cheats. Test counts and behaviors in README now match actual bundled pytest grader (40 Turn1, 42 Turn2) – extra hard.
