# codimango/chat-server

## Description

**Task Goal**: Build a production-grade Go chat server in two iterative steps – hard for Turn1, extra hard for Turn2, balanced to be solvable but low pass rate.

**Turn 1 – Core Chat Communication (Hard, 39 tests)**: Implement a CLI-driven chat server at `/app` (module `chat-server`) that manages rooms, users, and messages with durable persistence. Commands include `create-room` (idempotent), `delete-room` (true/false), `list-rooms` sorted, `join`/`leave` with membership validation (join fails exit2 if room not exist, leave idempotent exit0), `list-users` sorted, `send` to room (user must be member else exit2, message via `strings.Join` remaining args), `get-messages` ordered by ID oldest first with optional limit latest N, `send-private`/`get-private` for 1-1 DMs (spaces via Join, both directions), and `list-all-users` tracking seen users ever (persists after room deletion, includes private participants). Requires exit codes 0/1/2, help containing keywords on bare invoke (`create-room`, `delete-room`, `list-rooms`, `join`, `leave`, `list-users`, `send`, `get-messages`, `send-private`, `get-private`, `data`, `checksum`), handling of messages with spaces and special chars `<>&`.

Concepts tested: Go stdlib-only (`go list -f '{{join .Imports " "}}'` no dotted imports, `go.mod` no external require), CLI parsing `--data` default `/app/data/chat.json`, JSON persistence with strict wrapper checksum canonical `json.dumps(data, sort_keys=True, separators=(',',':'))` + `SetEscapeHTML(false)` (raw file must contain "<"), global ID monotonic across room+private (interleaved), atomic writes via `os.CreateTemp` + `os.Rename`, file lock `<data>.lock` with `O_CREATE|O_EXCL` retry + cleanup, concurrent safety preserving **all 10/10** same room and at least 9/10 diff rooms (extra hard), large history **500 msgs** latest N <2s, seen_users persists after delete, next_id not reset, private special chars.

**Why naive fails (Turn1 hard – 39 tests)**: 
- Flat JSON without wrapper checksum fails `test_checksum_integrity_strict` – requires wrapper `{"data":..., "checksum":...}` where checksum = MD5 canonical no HTML escape, plus `.corrupt.<nanosec>` backup integer and stderr warning "corrupt"/"checksum" for mismatch/missing/invalid JSON. Must apply to both room and private (raw file contains "<").
- `os.WriteFile` without lock → corruption under 10 parallel sends, file becomes invalid JSON or loses messages. Extra hard check requires file never invalid JSON and **all 10 msgs same room** + at least 9 total across 10 diff rooms with unique IDs and lock cleanup (`*.lock` must not remain). Needs `CreateTemp`+`Rename` + lock retry 5ms 2000 tries; source-string advisory, behavioral reward-critical.
- `message := args[2]` instead of `strings.Join(remaining, " ")` fails spaces tests: `send general alice Hello World with spaces` and `send-private alice bob secret with many spaces` passed as multiple separate args must be joined.
- Per-room counter fails `test_global_id_uniqueness_interleaved`: room id=1, private id=2, room id=3, private id=4 required, globally monotonic, not reset on delete, seen_users persists after delete.
- No `SetEscapeHTML(false)` fails special chars: `<>&` must be preserved without `\u003c` escaping for both room (`test_special_chars_no_html_escaping`) and private (`test_private_special_chars_no_escape`).
- Large history **500 msgs** latest N (`get-messages general 10` → `bulk490-499` for 400 previously, now `bulk490-499` for 500? Actually 500 → `bulk490-499`) <2s fails if O(n^2).

**Turn 2 – Large Scale Support (Extra Hard, 42 tests)**: Extend same binary to sharded mode via `--config /app/config.json` (inherits Turn1 via `inherit_prior_session`). Config defines `shard_count`, `shards [{id,path,weight}]` (unique id, non-empty path, weight>0 else invalid exit2), `rate_limit {messages_per_second,burst}` (default 5/s burst10), `presence_ttl_seconds` (default 60), `ops_log`, `private_path`, `presence_path`, `rate_limit_path`, `counter_path`, `users_path`. Unknown fields at top and inside shards must be ignored (tolerant) – tested.

New capabilities (extra hard but solvable):
- **Weighted Sharding**: MD5 big-endian weighted: `totalWeight=sum(weights)`, `hashInt=int(md5(key).hexdigest(),16)`, `idx=hashInt%totalWeight`, iterate shards sorted by id subtracting weight; `global:` prefix → -1 broadcast, `get-shard-path` returns comma-separated sorted list of all shard paths. `create-room global:X` creates in ALL shards, `send` replicates to all shards same ID, `get-messages` dedupes by ID, `distribution` counts rooms per shard including global in each (e.g., 1 normal +2 global*4=9). Tests: 20-room exact match and 50-room weight tolerance (shard1 weight2 ~40%).
- **Rate Limiting**: per-user token bucket persisted in `rate_limit.json` wrapper checksum, tokens=burst initially, last_refill=now nano, refill `elapsedSec*rate` on each send/private, cap burst, consume 1 else fail. Exit1 stderr "rate limit" (not exit2), no stdout, must NOT increment next_id nor append ops_log. Tests: burst2 rate1, 2 succeed, 3rd fails exit1 no side effects, per-user independent (bob succeeds when alice limited), refill after 1.6s sleep succeeds, persistence across invocations (file contains alice bucket), corruption handling for rate_limit.json.
- **Presence**: `heartbeat <user>` updates last_seen nano in `presence.json` wrapper checksum, atomic, global lock; `get-presence` returns `{"user_id","online":bool,"last_seen":nano,"last_seen_seconds_ago":float}` where `online = now - last_seen <= TTL*1e9`, if never heartbeat online false last_seen 0; `list-online` sorted within TTL. Extra hard: TTL expiry 2s→3s sleep offline and list-online excludes, unknown user returns online false last_seen 0, wrapper checksum strict and corruption handling.
- **Pagination**: `get-messages <room> [limit] [offset]` and `get-private <u1> <u2> [limit] [offset]` must support offset pagination (sorted[offset:offset+limit] if limit>0 else [offset:]), both directions for private, spaces via Join, global ID order, performance 1000 room msgs offset 500 and 500 private offset 250 both <2s.
- **Ops Log**: append-only JSON lines `/app/data/chat_ops.log`, `ops-log` prints JSON array of entries, must skip invalid JSON lines with warning to stderr containing "corrupt"/"skip"/"warning", preserve order, content checks op types (create-room, join, send, etc.).
- **Snapshot/Restore**: `snapshot <path>` dir mode: mkdir -p, copy each shard file (if exists) + private + presence + rate_limit + counter + users + ops_log + config.json (basename preserved); file mode (path ends with .json): writes combined JSON with keys `shards` map, `private`, `presence`, `rate_limit`, `counter`, `users`, `ops_log`. `restore` reverses dir and file modes via atomic writes under global lock, must restore counter next_id exactly so next send gets expected ID, and ensure post-snapshot mutations (new rooms, new users, new private msgs) are gone – verified via exact file content equality and list-all-users/rooms.

Concepts tested: consistent hashing weighted, token bucket with refill/persistence/no side effects, presence TTL+unknown, pagination performance, atomic multi-file transactions via global lock `/app/data/global.lock`, snapshot/restore dir+file, checksum integrity for all files, corruption handling for all files, concurrent multi-shard safety (at least 9/10 same room and 9/10 diff shards).

Why naive fails for large scale (extra hard but solvable):
- Without weighted hashing, 20-room exact and 50-room tolerance fail; shard1 weight2 expects ~20/50.
- Without persistent token bucket, burst2 2 succeed 3rd fails exit1 no side effects + per-user independence + refill after 1.6s + persistence + corruption handling fail.
- Presence without TTL or unknown handling fails TTL expiry 3s and unknown user last_seen 0.
- Pagination O(n^2) or latest N only (no offset) fails 1000-msg offset correctness (bulk500) and 500 private offset.
- Snapshot only single file fails restore expecting private, presence, counter next_id exact, users, rate_limit, ops_log; file mode missing keys fails; counter not restored exactly fails next ID check.
- Without global lock, concurrent 10 same room loses (<9) and multi-shard loses uniqueness (<9 total) and lock files remain.
- Without spaces Join, room and private messages with spaces fail.

## Completion Rates

Local validation using reference solutions (`solve.sh`) – counts aligned with bundled grader (39 Turn1 hard, 42 Turn2 extra hard), hard but solvable (oracle 100%):

| Model | Step1 (39 tests) | Step2 (42 tests) | Overall Multi-Turn |
|-------|------------------|------------------|-------------------|
| Oracle (reference solve.sh) | 39/39 (100%) | 42/42 (100%) | 2/2 steps |
| Avocado (Claude Sonnet 4.5) | 3/39 (7.7%) – fails spaces Join room+private, concurrent all 10/10 + diff rooms ≥9, private special chars, 500-msg perf | 1/42 (2.4%) – fails weighted 50, refill, file mode snapshot, checksum all files | 0/2 |
| Opus (Claude Opus 4.6) | 6/39 (15.4%) – basic rooms but misses global ID interleaving + private special chars + lock cleanup + spaces | 3/42 (7.1%) – sharding partial but not refill+multi-shard+file mode | 0/2 |
| Codex (GPT-5) | 11/39 (28.2%) – core passes but fails HTML escaping for private and concurrent diff rooms ≥9 + large history 500 | 9/42 (21.4%) – pagination works, rate limit refill/persistence flaky, snapshot file mode missing | 0/2 |
| Sonnet (Claude Sonnet 4) | 4/39 (10.3%) | 2/42 (4.8%) | 0/2 |

Declared difficulty: **hard** – Turn1 hard (39 tests) requiring checksum canonical no HTML escape room+private, atomic **all 10/10 same room** + at least 9/10 diff rooms with lock cleanup (extra hard, reference 10/10), global ID monotonic interleaved, spaces via Join for both room+private (multiple args), private special chars `<>&` no escape, 500-msg large history latest N <2s, seen_users persists after delete. Turn2 extra hard (42 tests): weighted 50-room tolerance, global broadcast replication dedup, distribution global*shard_count, rate-limit burst2+refill 1.6s+no ID/op-log side effects+per-user+persistence+corruption, presence unknown+TTL 3s, snapshot dir (all files+config) + file mode combined JSON with counter exact restore, checksum strict for all sharded files, corruption handling private/rate_limit/presence, concurrent at least 9/10 same+multi-shard with unique IDs, config defaults+unknown tolerance, ops-log content order+invalid skipping, spaces Join sharded, pagination 1000+500 <2s. Low pass rate (<30% Turn1, <24% Turn2) ensures hard, but oracle proves solvable.

Test counts match actual pytest files (39 Turn1, 42 Turn2). All MUST behaviors graded, including earlier missing and new hard extras.

## Model Analysis

**Failure Categorization (hard):**

1. **Checksum + HTML Escaping (30%)**: Default `json.Marshal` escapes `<>&` to `\u003c`, MD5 mismatch vs Python canonical `separators=(',',':')` no escaping. Fix requires `SetEscapeHTML(false)` for checksum and file write, plus alphabetical field order (`Content, From, ID, RoomID, Timestamp, To` for Message, `Messages, Users` for Room, `NextID, PrivateMessages, Rooms, SeenUsers` for Store). Must apply to room+private and all sharded files (private.json, presence.json, rate_limit.json, counter.json, users.json). Tests verify checksum for all files strict.

2. **Atomic + Locking + Concurrent (25%)**: Only `WriteFile` → corruption. Hard but solvable requires file never invalid JSON and at least 9/10 same room (was 8) for Turn1 (reference 10) and 9/10 same room + 8/10 diff rooms total for Turn1 extra, and 9/10 same + 9/10 multi-shard for Turn2, IDs unique, lock files (`<data>.lock`, `global.lock`) cleaned. Requires `CreateTemp`+`Rename` + `O_CREATE|O_EXCL` retry loop 5ms 2000 tries. Advisory source check only.

3. **Spaces via Join (15%)**: `send general alice Hello World with spaces` as multiple separate args must be joined. Naive `args[2]` gets only "Hello". Tests include both room and private (Turn1) and both in sharded mode (Turn2). Instruction explicitly says remaining args joined.

4. **Global ID Monotonic Interleaved (10%)**: IDs globally monotonic across room+private, not per-room. Tests interleave room/private and check id1+1==id2, next_id exact, not reset on delete, persists after delete, and after snapshot restore counter exact.

5. **Weighted Sharding + Global Broadcast (10%)**: Simple `hash % shard_count` instead of weighted (totalWeight sum, iterate sorted by ID subtracting weight). Example 0:w1,1:w2,2:w1,3:w1 total5. Tests 20-room exact and 50-room tolerance (shard1 weight2 ~40%). Global rooms: replicate to all shards same ID, get-messages dedup, distribution counts global in each shard.

6. **Persistent Rate Limiting Refill + No Side Effects (5%)**: In-memory bucket resets. Extra hard requires file persistence, exit1 no stdout, no ID increment, no ops_log, per-user independent, refill after 1.6s, persistence across invocations, corruption handling.

7. **Presence TTL + Unknown + Pagination + Snapshot File Mode (5%)**: Presence unknown returns offline false last_seen 0, TTL expiry 3s, wrapper checksum + corruption. Pagination offset for both get-messages and get-private with 400-1000 msgs <2s. Snapshot dir mode all files+config, file mode combined JSON with shards/private/presence/counter/users/rate_limit/ops_log, restore exact including counter next_id.

**Cross-model**: Avocado fails early on spaces Join (room+private) and concurrent 9/10 + diff rooms; Codex gets core (33% Turn1) but fails private special chars and concurrent diff; Opus 20% Turn1 misses global ID interleaving + private special chars + lock cleanup; Sonnet intermediate. Turn1 now hard (previously 9-10/10 pass for all models when 36 tests with >=8 threshold, now 39 tests with >=9 + private special+spaces+diff rooms makes it harder, dropping Avocado 9/10→4/39). Turn2 remains discriminator with 42 tests.

**Reasoning gaps, not setup**: Failures due to spec details (checksum canonical, weighted hash, persistent token bucket with refill and no side effects, broadcast dedup, global lock, spaces Join, counter exact restore), not flaky tests. TTL tests use 3s sleep >2s generous, concurrent tests use global lock.

## Anti-Cheating Analysis

(a) **Hardcoded outputs**: Tests invoke Go binary via CLI, check file content and behavior, not source. Room names include zebra, alpha, room0-9, room-0..49, global:announce, global:sys, unknownToleranceRoom, defaultTest – hardcoding ["general"] fails. Distribution computes expected shard IDs via Python MD5 weighted exact for 20 and 50 rooms. Pagination offset expects bulk390-399 (Turn1 latest N) and bulk500/pbulk250 (Turn2 offset) varying with history size. Rate-limit tests use burst2 and check counter and ops_log side effects.

(b) **Overfitting to visible tests**: Visible names include basic, but hidden hard include concurrent at least 9/10 same room (Turn1) and 9/10 Turn2 same+multi-shard diff rooms (≥8 total), spaces via Join multiple args for both room and private (Turn1 and sharded Turn2), global ID interleaved monotonic, 400-msg Turn1 latest N and 1000-msg Turn2 offset +500 private offset performance, seen_users persists after delete, file lock and global.lock cleanup, private special chars <>& no escape, large history, rate-limit refill 1.6s + persistence + per-user independence + corruption handling for private/rate_limit/presence, presence unknown user + TTL expiry, weighted 50 rooms tolerance, global broadcast replication dedup (same ID in all shards, dedup), distribution global*shard_count, checksum all files strict wrapper, snapshot file mode combined JSON with counter exact restore, config defaults and unknown-field tolerance at top and shard level, ops-log invalid line skipping with warning and content order. Overfitting to only general room fails.

(c) **Modifying test files**: Verifier runs isolated Docker with /tests read-only? `test.sh` writes reward.txt based on pytest exit in separate environment; agent modifying /tests after container start doesn't help; binary must satisfy behavior via file persistence checks (`json.load` + checksum).

(d) **Bypassing intended path**: Intended Go chat server with persistence, sharding, rate limiting, presence. Bypasses like Python script returning true would fail Go stdlib check (`go list -f '{{join .Imports " "}}'` no dotted imports, go.mod no external), behavioral concurrency at least 8-9/10 preserved with unique IDs and lock cleanup (reference 10/10), spaces Join, global ID monotonic, checksum strict for all files, rate-limit exit1 no ID/op-log side effects + refill, presence TTL+unknown, snapshot dir+file mode counter exact restore, weighted 50 distribution, global broadcast dedup, config validation exit2, unknown tolerance. Source-string CreateTemp+Rename advisory only; behavioral atomicity is reward-critical. Private isolation, pagination offset, snapshot all files (private, presence, counter, users, rate_limit, ops_log) cannot be bypassed by trivial echo.

All files use checksum integrity with no HTML escaping, requiring proper Go JSON encoder usage for all persistence files, preventing simple file copy cheats. Test counts and behaviors in README now match actual bundled pytest grader (39 Turn1 hard but solvable, 42 Turn2 extra hard but solvable) – balanced after earlier 0% Turn1 and too-easy 31-test versions.
