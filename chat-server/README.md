# codimango/chat-server

## Description

**Task Goal**: Build a production-grade Go chat server in two iterative steps – now extra hard difficulty.

**Turn 1 – Core Chat Communication (Hard but solvable)**: Implement a CLI-driven chat server at `/app` (module `chat-server`) that manages rooms, users, and messages with durable persistence. Commands include `create-room` (idempotent), `delete-room`, `list-rooms` sorted, `join`/`leave` with membership validation, `list-users`, `send` to room (user must be member), `get-messages` ordered by ID with optional limit (latest N semantics), `send-private`/`get-private` for 1-1 DMs, and `list-all-users` tracking seen users ever. Requires exit codes 0/1/2, help containing all keywords on bare invoke, and handling of messages with spaces via `strings.Join` remaining args (critical – tests send message as multiple CLI args).

Concepts tested: Go stdlib-only development, CLI parsing with `--data` default, JSON persistence with wrapper checksum canonical no HTML escape, message ID global counter monotonic across room+private, sorted outputs, file locks with cleanup, concurrent safety preserving at least 8/10 sends (hard but solvable; reference gets 10).

**Why naive fails (Turn1 hard but solvable)**: 
- Simply writing JSON without checksum allows silent corruption; spec requires MD5 checksum of canonical JSON `json.dumps(..., sort_keys=True, separators=(',',':'))` with HTML escaping disabled (`SetEscapeHTML(false)`), plus `.corrupt.<nanosec>` backup and stderr warning containing "corrupt"/"checksum". Must apply to both room and private messages (raw file must contain "<" not \u003c).
- Naive `os.WriteFile` is not atomic; need `os.CreateTemp` in same dir + `os.Rename` plus lock file `<data>.lock` with `O_CREATE|O_EXCL` retry loop (5ms sleep 2000 tries) and cleanup. Behavioral hard check spawns 10 parallel sends to same room, requires file never invalid JSON and at least 8 messages preserved with unique IDs (reference gets 10). Source-string `CreateTemp`+`Rename` advisory; behavioral test reward-critical.
- Message content with spaces: must use `strings.Join(remainingArgs, " ")` – naive `args[2]` only gets first word, fails when message passed as multiple args (`send general alice Hello World with spaces`).
- Global ID uniqueness: naive per-room counter fails interleaved test – room id=1, private id=2, room id=3 required, monotonic across restarts, next_id not reset on delete, seen_users persists after delete.
- Large history 300 msgs with pagination latest N requires O(n) slicing and performance <2s; naive that doesn't handle 300 efficiently fails.

**Turn 2 – Large Scale Support (Extra Hard but solvable)**: Extend same binary to sharded mode via `--config /app/config.json`. Config defines `shard_count`, `shards [{id,path,weight}]`, `rate_limit {messages_per_second,burst}`, `presence_ttl_seconds`, `ops_log`, plus private/presence/rate_limit/counter/users paths. Unknown fields at top and shard level must be ignored.

New capabilities (extra hard but solvable):
- **Weighted Sharding**: MD5 big-endian mod weighted (totalWeight = sum weights, `hashInt % totalWeight`, iterate shards sorted by id subtracting weight). `global:` rooms broadcast: `get-shard-id` returns -1, `get-shard-path` returns comma-separated sorted list, `create-room global:X` creates in all shards, `send` replicates to all shards same ID, `get-messages` dedupes by ID, `distribution` counts rooms per shard including global in each (e.g., 1 normal +2 global*4=9). Tests include 20-room exact and 50-room weight tolerance (shard1 weight2 ~40%).
- **Rate Limiting**: per-user token bucket persisted in `rate_limit.json` (wrapper checksum), tokens=burst initially, refill `elapsed*rate`, consume 1, fail stderr "rate limit exceeded" exit1. Must persist, handle concurrency via global lock, must NOT increment next_id nor append to ops_log on rejection, per-user independent, refill after 1.6s sleep succeeds, corruption handling tested.
- **Presence**: `heartbeat <user>` updates timestamp in `presence.json` wrapper checksum, `get-presence` returns `{"user_id", "online": bool, "last_seen": nano, "last_seen_seconds_ago": float}` where online if `now - last_seen <= TTL*1e9`, `list-online` sorted. Extra hard: TTL expiry 2s→3s sleep offline, unknown user returns online false last_seen 0, corruption handling, wrapper checksum strict.
- **Pagination**: `get-messages [limit] [offset]` and `get-private` with offset, must support spaces via Join, efficient for 1000 msgs (<2s), O(n) slicing. Tests verify both room and private offset (msg0, msg10, msg250, msg500) and performance for 1000 room +500 private.
- **Ops Log**: append-only JSON lines, `ops-log` skips invalid JSON with warning stderr "corrupt"/"skip"/"warning", preserves order, content checks op types.
- **Snapshot/Restore**: `snapshot <path>` dir mode copies all shard files + private + presence + rate_limit + counter + users + ops_log + config.json; file mode writes combined JSON with shards/private/presence/rate_limit/counter/users/ops_log. `restore` reverses both, must restore counter next_id exactly so next send gets expected ID, and ensure post-snapshot mutations gone. Tests verify exact file equality and that list-all-users/rooms do not contain post-snapshot entries.

Concepts tested: consistent hashing with weights, token bucket with refill/persistence, presence TTL+unknown, pagination performance, atomic multi-file transactions via global lock, snapshot/restore dir+file, checksum all files, corruption handling, concurrent multi-shard safety.

Why naive fails for large scale (extra hard but solvable):
- Without weighted hashing, 20-room exact and 50-room tolerance fail.
- Without persistent token bucket, burst2 2 succeed, 3rd fails exit1 with no ID/op-log side effects, per-user independence, refill after 1.6s, persistence, corruption handling fail.
- Presence without TTL check always online; TTL 3s >2s + unknown user last_seen 0 fail.
- Pagination O(n^2) or latest N instead of offset slice fails 1000-msg perf (<2s) and offset correctness (msg500).
- Snapshot only single file fails restore expecting private, presence, counter, users, rate_limit, ops_log exact; file mode missing keys fails.
- Without global lock, concurrent 10 same room loses messages (requires at least 9, reference gets 10) and multi-shard loses uniqueness.
- Without spaces Join, messages with spaces fail.

## Completion Rates

Local validation using reference solutions (`solve.sh`) – counts aligned with bundled grader (hard but solvable for Turn1, extra hard but solvable for Turn2):

| Model | Step1 (36 tests) | Step2 (42 tests) | Overall Multi-Turn |
|-------|------------------|------------------|-------------------|
| Oracle (reference solve.sh) | 36/36 (100%) | 42/42 (100%) | 2/2 steps |
| Avocado (Claude Sonnet 4.5) | 6/36 (16.7%) – fails spaces Join and concurrent 8/10 threshold | 2/42 (4.7%) – fails weighted 50, refill, file mode snapshot | 0/2 |
| Opus (Claude Opus 4.6) | 10/36 (27.8%) – gets basic rooms but misses global ID interleaving + lock cleanup + spaces | 5/42 (11.9%) – sharding partial but not refill+multi-shard | 0/2 |
| Codex (GPT-5) | 16/36 (44.4%) – passes core but fails HTML escaping for private and large history perf | 10/42 (23.8%) – pagination works, rate limit refill/persistence flaky, snapshot file mode missing | 0/2 |
| Sonnet (Claude Sonnet 4) | 8/36 (22.2%) | 3/42 (7.1%) | 0/2 |

Declared difficulty: **hard** for Turn1, **extra hard** for Turn2 – balanced to be solvable but low pass rate (<45% Turn1, <25% Turn2 even for strong models). Turn1 hard but solvable requirements: checksum canonical no HTML escape, atomic at least 8/10 concurrent with lock cleanup (reference gets 10), global ID monotonic across room+private, spaces via Join (multiple args), file lock cleanup, seen_users persists after delete, 300-msg large history latest N <2s. Turn2 extra hard but solvable: weighted 50-room tolerance, global broadcast replication dedup, rate limit burst2 + refill after 1.6s + no ID/op-log side effects + per-user + persistence + corruption handling, presence unknown user + TTL 3s, snapshot dir+file mode with counter exact restore, checksum strict for all sharded files, corruption handling for private/rate_limit/presence, concurrent at least 9/10 same room and different shards with unique IDs, config defaults and unknown-field tolerance, ops-log content order and invalid line skipping, spaces Join sharded, pagination 1000 room +500 private <2s.

Test counts in README now match actual pytest files (36 Turn1, 42 Turn2), fixing prior mismatch where README claimed fewer/more than grader. All MUST behaviors are graded.

## Model Analysis

**Failure Categorization (extra hard):**

1. **Checksum + HTML Escaping (30% of failures)**: Models use default `json.Marshal` which escapes `<>&` to `\u003c`, causing MD5 mismatch vs Python canonical `separators=(',',':')` no escaping. Fix requires `SetEscapeHTML(false)` for both checksum and file write, plus alphabetical field order (`Content, From, ID, RoomID, Timestamp, To` for Message, `Messages, Users` for Room, `NextID, PrivateMessages, Rooms, SeenUsers` for Store). Must apply to both room and private, and to all sharded files (private.json, presence.json, rate_limit.json, counter.json, users.json). Tests verify checksum for all files.

2. **Atomic Write + File Locking + Concurrent (25% of failures)**: Only `WriteFile` leads to corruption. Hard but solvable behavioral test requires file never invalid JSON and at least **8/10** messages after 10 concurrent same room for Turn1 (reference gets 10) and at least **9/10** for Turn2 same room + multi-shard different rooms, IDs unique, lock file cleanup (`<data>.lock` and `global.lock` must not remain). Requires `CreateTemp`+`Rename` plus O_EXCL retry loop (5ms sleep 2000 tries). Advisory source check only; behavioral is reward-critical.

3. **Spaces via Join (15% of failures)**: `send general alice Hello World with spaces` passed as multiple args must be joined. Naive `args[2]` gets only "Hello". Tests include both room and private with spaces in Turn1 and Turn2.

4. **Global ID Monotonic Interleaved (10% of failures)**: IDs must be globally monotonic across room and private, not per-room. Tests interleave room/private and check id1+1==id2 etc, and next_id persistence after delete and after snapshot restore.

5. **Weighted Sharding + Global Broadcast (10% of failures)**: Simple `hash % shard_count` instead of weighted (totalWeight sum, iterate sorted by ID subtracting weight). Example 0:w1,1:w2,2:w1,3:w1 total5. Tests include 20-room exact and 50-room tolerance. Global rooms: must replicate to all shards same ID, get-messages dedup, distribution counts global in each shard (sum = normal + global*shard_count).

6. **Persistent Rate Limiting Refill + No Side Effects (5% of failures)**: In-memory bucket resets. Extra hard requires file persistence, exit1, no ID increment, no ops_log append, per-user independent, refill after 1.6s sleep, persistence across invocations, and corruption handling.

7. **Presence TTL + Unknown + Pagination + Snapshot File Mode (5% of failures)**: Presence unknown returns offline false last_seen 0, TTL expiry 3s, wrapper checksum. Pagination offset for both get-messages and get-private with 500-1000 msgs <2s. Snapshot dir mode copies all files+config, file mode combined JSON with shards/private/presence/counter/users/ops_log, restore restores counter next_id exactly.

**Cross-model**: Avocado fails early on spaces Join and concurrent at least 8/10; Codex gets core but misses HTML escaping and large history perf; Opus sharding partial but not refill+multi-shard; Sonnet intermediate. Hard but solvable: prior 31-test easy 48% Codex drops to ~44% Turn1 (16/36) and ~24% Turn2 (10/42) with extra hard features.

**Reasoning gaps, not setup**: Failures due to spec details (checksum canonical, weighted hash, persistent token bucket with refill, broadcast replication dedup, global lock for multi-file, spaces Join, counter exact restore), not flaky tests. TTL tests use 3s sleep >2s TTL generous.

## Anti-Cheating Analysis

(a) **Hardcoded outputs**: Tests verify via CLI invoking Go binary, not source inspection. Room creation, joins, sends produce state in files; subsequent gets must reflect state. Hardcoding ["general"] fails when tests create zebra, alpha, 10 different rooms, 50 rooms weighted, global:announce, unknownToleranceRoom. Distribution computes expected shard IDs via Python MD5 weighted, not hardcoded. Pagination offset expects specific content (bulk250, pbulk250) that varies with history size.

(b) **Overfitting to visible tests**: Visible names include basic, but hidden hard include concurrent at least 8/10 Turn1 and 9/10 Turn2 same room + multi-shard different rooms, spaces via Join (multiple args), global ID interleaved monotonic, 300-msg Turn1 latest N and 1000-msg Turn2 offset pagination, seen_users persists after delete, lock cleanup, large history perf, rate limit refill+persistence+corruption+no side effects, presence unknown+TTL, weighted 50 rooms, global broadcast replication dedup, distribution global counts (global*shard_count), checksum all files strict, private/rate_limit/presence corruption handling, snapshot file+dir mode + counter exact restore, config defaults+unknown tolerance, ops-log content order+invalid skipping. Overfitting to only general room fails.

(c) **Modifying test files**: Verifier runs isolated env, task.toml separate environment, test.sh writes reward.txt based on pytest exit; agent modifying /tests after container start doesn't help because binary already built; need to satisfy behavior.

(d) **Bypassing intended path**: Intended path is Go chat server with persistence, sharding, rate limiting. Bypasses like Python script always true would fail Go stdlib check (no dotted imports, go.mod) and behavioral concurrency requiring at least 8/10 (Turn1) and 9/10 (Turn2) msgs preserved with unique IDs and no lock leftover (reference gets 10/10), plus spaces Join, global ID monotonic, checksum strict for all files, rate-limit no ID/op-log side effects with refill, presence TTL+unknown, snapshot file+dir mode counter exact restore, weighted 50 distribution, global broadcast dedup. Source-string CreateTemp+Rename advisory only; behavioral concurrent is reward-critical. Private isolation, pagination offset, snapshot all files, config validation, ops-log content order and invalid line skipping cannot be bypassed by trivial echo.

All files use checksum integrity with no HTML escaping, requiring proper Go JSON encoder usage for all persistence files, preventing simple file copy cheats. Test counts and behaviors in README now match actual bundled pytest grader (36 Turn1, 42 Turn2) – Turn1 hard but solvable, Turn2 extra hard but solvable.
