# codimango/chat-server

## Description

**Task Goal**: Production-grade Go chat server in two hard-balanced steps – oracle 100% but moderate model pass rate (Step1 ~80% with fix, Step2 ~30-40% after balancing).

**Turn 1 – Core (Hard, 56 tests)**: CLI at `/app` module `chat-server` manages rooms, users, messages with durable persistence. Commands: `create-room` idempotent (empty ID exit2), `delete-room` true/false, `list-rooms` sorted must handle **200 rooms** sorted, `join`/`leave` idempotent (join fail exit2 if room not exist or empty args, **20 concurrent joins** diff users same room must preserve all 20 sorted, leave all → [] and `send` after leave fails), `list-users` sorted exit2 if nonexist, `send` to room (member else exit2, message via `strings.Join` remaining args, missing message exit2, special chars `<>&` no HTML escape, raw file contains "<", Unicode emoji 🌍🚀😀 preserved, newlines/tabs preserved, large message **10KB** handled), `get-messages` oldest first sorted by id asc, limit latest N (0/omit=all, latest N when limit), invalid limit exit2, limit zero returns all, nonexist room → [] not error, `send-private`/`get-private` 1-1 DMs (spaces via Join, isolation, both directions, limit latest N, invalid limit exit2, limit zero all, private special chars `<>&` no escape, Unicode, 10KB), `list-all-users` sorted unique ever seen even after delete. IDs globally incrementing int64 starting at 1 unique across room+private monotonic interleaved (room1, priv1, room2, priv2 → 1,2,3,4), persists across restarts (20 room+private ops → next_id 41) and many ops, not reset on delete except after corruption reset to 1. Help bare no args contains keywords `create-room`, `delete-room`, `list-rooms`, `join`, `leave`, `list-users`, `send`, `get-messages`, `send-private`, `get-private`, `data`, `checksum` exit0. Unknown command exit2.

Persistence MUST use wrapper `{"data":{rooms, private_messages, next_id, seen_users}, "checksum": md5 canonical}` canonical = `json.dumps(data, sort_keys=True, separators=(',',':'))` no HTML escaping – Go must use `SetEscapeHTML(false)` for checksum and file write. On write atomic via `os.CreateTemp` same dir + `os.Rename` plus file lock `<data>.lock` `O_CREATE|O_EXCL` retry loop 5ms 2000 tries + cleanup (lock must not remain). On read: missing/empty → empty store, wrapper missing/empty checksum or mismatch or invalid JSON → corruption: backup `<path>.corrupt.<nanosec>` integer `UnixNano()`, stderr warning "corrupt"/"checksum", recreate empty valid wrapper. Behavioral hard (reference gets 20/20): **20 concurrent same room preserves all 20** (file never invalid JSON during concurrent, IDs unique), **20 parallel diff rooms preserves all 20**, **20 concurrent joins preserves all 20 sorted**, plus persistence across restarts, room IDs with dash/underscore/dot/colon. Large history **1000 msgs** latest N (`get-messages general 10` → `bulk990-999` for 1000) performance <2s, all 1000 retrievable, plus **200 rooms sorted**, 100 rooms sorted. Edge: empty room/user ID exit2, missing message exit2, invalid limit `-1, abc, -100` exit2 for both room and private, limit zero returns all, nonexist [], leave all [], join after delete fails exit2, send after leave fails exit2, next_id after corruption reset to 1, Unicode emoji, newlines/tabs, large message 10KB, persistence across many ops.

**Turn 2 – Large Scale (Balanced Hard, 59 tests)**: Extends binary to sharded mode via `--config /app/config.json` (inherits Turn1). Config defines `shard_count`, `shards [{id,path,weight}]` (validation: shard_count≤0, empty shards, negative id, duplicate id, empty path, weight≤0 → exit2, shard_count mismatch lenient not crash), `rate_limit {messages_per_second,burst}` default 5/s burst10, `presence_ttl_seconds` default60, `ops_log`, private/presence/rate_limit/counter/users paths. Unknown fields at top and inside shards must be ignored. **This version is balanced after fixing too-easy feedback (8/10 metacode)**: previously 49 tests had Metacode 1/10 due to strict flat format blocker, after fix 56 tests went to 8/10 too easy (step2 100% for those reaching it). Now 59 tests with strict concurrent all 10 for same-room & multi-shard (was ≥9 lenient), plus 20 concurrent multi-shard all 20, 20 concurrent joins all 20 sorted, plus edge: 200 rooms sharded, 2000 pagination, large message 10KB sharded+private 10KB, nonexist empty and leave-all empty sharded, unicode emoji sharded, private special chars sharded. Rate_limit persistence format-agnostic (flat and buckets).

New capabilities balanced hard (all strict concurrent all-10/20):
- Weighted Sharding: MD5 big-endian weighted, totalWeight sum, idx=hashInt%totalWeight, iterate sorted by id subtracting weight; `global:` → -1, get-shard-path comma-separated sorted list. `create-room global:X` creates in ALL shards, `send` replicates to all shards same ID, `get-messages` dedupes by ID, `distribution` counts rooms per shard including global in each (1 normal+2 global*4=9). Tests 20-room exact, 50-room tolerance, 100-room tolerance (40% weight) plus 200-room sorted.
- Rate Limiting: per-user token bucket persisted `rate_limit.json` wrapper checksum, tokens=burst, last_refill=now nano, refill elapsedSec*rate cap burst, consume1 else fail exit1 stderr "rate limit" no stdout, must NOT increment next_id nor ops_log, per-user independent (bob succeeds when alice limited), refill after 1.6s succeeds, **multiple cycles** (2 succeed fail sleep 1.2s succeed fail sleep 1.2s succeed), persistence across invocations format-agnostic (flat and nested buckets both accepted), corruption handling for rate_limit.json.
- Presence: `heartbeat <user>` updates last_seen nano in `presence.json` wrapper checksum atomic global lock; `get-presence` returns `{"user_id","online":bool,"last_seen":nano,"last_seen_seconds_ago":float}` where online = now - last_seen ≤TTL*1e9, if never seen online false last_seen0; `list-online` sorted within TTL. Tests: heartbeat→online, TTL 2s→3s sleep offline and list-online excludes, unknown user returns online false last_seen0, multi-user TTL 3 users online, 3s sleep → [], heartbeat bob → [bob], corruption handling, wrapper checksum all files.
- Pagination: `get-messages [limit] [offset]` and `get-private [limit] [offset]` offset pagination sorted[offset:offset+limit] if limit>0 else [offset:], both directions private, spaces via Join, global ID order, performance **1000 room offset500** and **500 private offset250** and **2000 room offset1000** all <2s, plus large history 500 private perf.
- Ops Log: append-only JSON lines, `ops-log` prints JSON array, must skip invalid JSON lines with warning stderr "corrupt"/"skip"/"warning", preserve order, content checks op types (create-room, join, send, send-private) order, **large 100 ops**.
- Snapshot/Restore: `snapshot <path>` dir mode copies all shard files+private+presence+rate_limit+counter+users+ops_log+config.json basename preserved; file mode (path.json) writes combined JSON with keys shards map, private, presence, rate_limit, counter, users, ops_log. `restore` reverses both modes via atomic writes global lock, must restore counter next_id exactly so next send gets expected ID, post-snapshot mutations (new rooms, users, private msgs) gone – verified via exact file content equality and list-all-users/rooms, plus counter persistence.

Why naive fails both hard 56/59:
- Flat JSON no wrapper → checksum strict tests fail (all files)
- WriteFile no lock → corruption under 20 parallel same room, diff rooms (20), concurrent joins 20 → loses (<20) and invalid JSON and lock files remain
- `args[2]` not Join → spaces tests room+private (both Turn1 and sharded Turn2) and large message 10KB fail
- Per-room counter → global ID interleaving 1,2,3,4 and next_id persistence and after delete and after corruption reset fail
- No SetEscapeHTML(false) → special chars <>& fail room+private both modes, private special chars sharded, Unicode
- No invalid limit/empty ID checks → exit2 tests fail (empty room/user, missing message, invalid limit negative/abc, invalid limit private)
- Nonexist room get-messages returns error not [] and leave all not empty → fail
- 100/200 rooms not sorted → fail
- Large history 500/800/1000 O(n^2) or not perf → <2s fail (2000 pagination also)
- Simple hash%shard_count not weighted → 20/50/100 distribution fail
- In-memory rate limit → burst2+refill+multiple cycles+no side effects+per-user+persistence+corruption fail
- Presence always online or no unknown or no multi-user TTL → TTL expiry 3s, unknown last_seen0, multi-user fail
- Pagination latest N only → offset 500/1000/250/1000 fail
- Snapshot only single file → all files + file mode + counter exact restore fail
- No spaces Join sharded, no large message 10KB sharded, no Unicode emoji sharded → fail
- No config validation exit2 / unknown tolerance → fail
- No ops-log invalid skipping + content order + large 100 → fail

## Completion Rates

Local validation oracle – aligned with grader (56 Turn1 hard, 59 Turn2 balanced-hard) – hard but oracle 100%:

| Model | Step1 (56 tests) | Step2 (59 tests) | Overall |
|-------|------------------|------------------|---------|
| Oracle | 56/56 (100%) | 59/59 (100%) | 2/2 |
| Avocado online 49 tests strict (83fbfaa) | 56/56 | 1/10 (10%) metacode – 9/10 fail only `test_rate_limit_persistence` flat vs buckets – artificial | 1/10 |
| Avocado online 56 tests format-agnostic lenient >=9 (7f35008) | 56/56 | 8/10 (80%) metacode – 2 fail step1 members vs users, step2 8/8 100% for those reaching it – too easy | 8/10 |
| Avocado new 59 tests strict all 10/20 (balanced) | 56/56 | Expected 4-5/10 (~40-50%) after hardening: strict 10 all 10 same-room & 10 multi-shard & 20 multi-shard & 20 joins + 200 rooms + 2000 pagination + 10KB sharded+private + nonexist/leave-all sharded + unicode + private special | ~40% target |
| Codex online 49 strict | 5 fail step1, 5 fail step2 get_shard_path quoting | 1/10 only `test_rate_limit_persistence` | 0/10 |
| Codex online 56 lenient | 2 fail step1 members vs users, 1 fail get_shard_path quoted | 7/10 (70%) – too easy | 7/10 |
| Opus | 3/56 | 2/56 before | 0/2 |

Declared difficulty: **hard-balanced** – fixing online oscillation:
- 49 tests strict flat persistence → Metacode 1/10 (artificial blocker, 48/49 pass)
- 56 tests format-agnostic lenient >=9 → Metacode 8/10, Codex 7/10 (too easy, step2 100% for those reaching it)
- New 59 tests: format-agnostic but **strict all 10/20** for concurrent (same-room 10 all 10, multi-shard 10 all 10, multi-shard 20 all 20, joins 20 all 20) + added private 10KB, nonexist empty, leave-all empty → targets 40-50% Metacode, not 80% nor 10%
- 60 tests (4f5854e) → Metacode 0/10 too hard

Test counts match actual pytest files (56 Turn1, 59 Turn2). All MUST behaviors graded including rate-limit exit1 no side effects+refill+multiple cycles+persistence (format-agnostic)+per-user+corruption, TTL expiry+unknown+multi-user, malformed config validation+unknown tolerance+defaults, private pagination offset+500 perf+2000 perf, ops-log invalid skipping+content order+large, snapshot/restore all files+file mode+counter exact+ops_log, 200 rooms sharded, 20 concurrent multi-shard all 20, 20 joins sharded all 20, 10KB sharded+private, unicode, nonexist/leave-all sharded.

Oracle 59 tests 100% proved (3:02-3:04).

## Model Analysis

**Failure Categorization (hard 56+56):**

1. **Checksum + HTML Escaping (25%)**: Default Marshal escapes `<>&` → MD5 mismatch vs Python canonical. Fix `SetEscapeHTML(false)` + alphabetical field order + wrapper checksum for all files. Private special chars and private special chars sharded + Unicode emoji + large message 10KB test.

2. **Atomic + Locking + Concurrent (30%)**: WriteFile no lock → corruption under 20 parallel same room (all 20 required) and diff rooms all 20, concurrent joins 20 sorted, Turn2 lenient 10≥9 same-room plus strict 20 multi-shard all 20 + 20 joins all 20 sorted with unique global IDs, lock files cleaned (`.lock`, `global.lock`). Needs `CreateTemp`+`Rename` + `O_CREATE|O_EXCL` retry.

3. **Spaces via Join + Large Message + Unicode (20%)**: Multiple args must be joined. Naive `args[2]` fails. Tests for both room and private Turn1 and sharded Turn2, plus 10KB large message, Unicode emoji, newlines/tabs, 500 private pagination and 2000 pagination.

4. **Global ID + Edge Validation (15%)**: Globally monotonic across room+private, next_id after corruption reset to 1, empty room/user ID exit2, missing message exit2, invalid limit exit2 (negative, non-int), limit zero returns all, nonexist [] and leave all [], 100/200 rooms sorted, concurrent joins 20, persistence across restarts 20 ops → next_id 41, room ID with dash/underscore/dot/colon, join after delete fails, send after leave fails, plus sharded nonexist [] and leave-all [].

5. **Weighted Sharding + Global Broadcast (5% Turn2)**: Simple hash%shard_count not weighted. 20 exact +50 tolerance +100 tolerance (40% weight) +200 rooms sorted. Global rooms replicate same ID, dedup, distribution global*shard_count, multiple broadcast 5 msgs.

6. **Rate Limiting Refill Multiple Cycles + No Side Effects + Persistence (2.5% Turn2)**: In-memory resets, must persist, exit1 no ID/op-log, per-user independent, refill 1.6s and multiple cycles 1.2s, persistence format-agnostic (flat and buckets both accepted), corruption handling.

7. **Presence TTL + Unknown + Multi-User + Pagination + Snapshot File Mode (2.5% Turn2)**: Unknown false last_seen0, TTL expiry 3s, multi-user TTL, offset pagination 50/500/1000/2000 room+private <2s, snapshot dir all files+config + file mode combined JSON with counter exact restore, ops-log invalid skipping + content order + large 100, 200 rooms sharded, large message 10KB sharded, unicode.

**Cross-model**: Turn1 hard 56 tests, Turn2 56 tests balanced-hard after fixing artificial strict format check that made online Metacode 1/10 despite 48/49 passing. Now targets 30-40% Metacode, 20-30% Codex, with oracle 100%.

**Reasoning gaps**: Spec details (checksum canonical, weighted hash, persistent token bucket with refill multiple cycles and no side effects, broadcast dedup, global lock, spaces Join, counter exact restore, empty ID and invalid limit validation, 200 rooms, concurrent joins 20, Unicode, 10KB, 1000/2000 history), not flaky.

## Anti-Cheating Analysis

(a) **Hardcoded**: CLI invoking Go binary, file persistence checks via `json.load` + checksum, not source. Room names include zebra, alpha, middle, room0-19, room-000..199 (200 rooms), room-0000 (100), global:announce, global:multi (5 msgs), unknownToleranceRoom, defaultTest, room-0..49 (50), room-0..99 (100), room-000..199 sharded (200 rooms), nonexist. Distribution computes expected via Python MD5 weighted exact for 20/50/100/200 rooms. Pagination expects bulk490-499 for 500, bulk990-999 for 1000 (Turn1), bulk500 for 1000, bulk1000 for 2000, pbulk250 for 500 varying, bulk1000 for 2000 sharded. Rate-limit tests check counter and ops_log side effects and per-user and refill multiple cycles – now format-agnostic to avoid artificial failure.

(b) **Overfitting**: Hidden hard includes concurrent all 20 same+diff rooms+20 joins + concurrent mixed, spaces Join multiple args room+private both modes +10KB, global ID interleaving + next_id after corruption reset + persistence across many ops 20 room+private →41, 500/1000-msg Turn1 latest N +100/200-room sorted +1000/2000-msg Turn2 offset +500 private offset perf <2s plus 2000 perf, seen_users persists after delete, lock cleanup both, private special chars <>& + Unicode emoji + newlines/tabs + large message 10KB + large message 10KB sharded + 200 rooms sharded + 20 multi-shard all 20 + 20 joins sharded + unicode emoji sharded + private special chars sharded, invalid limit exit2, empty IDs exit2, missing message exit2, nonexist [] and leave all [] and join after delete fails and send after leave fails and limit zero all (both single and sharded), rate-limit refill 1.6s + multiple cycles 1.2s + persistence format-agnostic + per-user independence + corruption handling private/rate_limit/presence, presence unknown + multi-user TTL expiry, weighted 50/100/200 rooms, global broadcast replication dedup same ID + multiple 5 msgs, distribution global*shard_count, checksum all files strict wrapper + after many ops, snapshot file mode combined JSON with counter exact restore + all files exact + ops_log, config validation exit2 for shard_count≤0 etc + unknown-field tolerance top and shard level + defaults, ops-log invalid skipping warning + content order + large 100, 200 rooms sharded, 20 multi-shard all 20, 20 joins sharded all 20. Overfitting to only general room fails.

(c) **Modifying test files**: Verifier isolated Docker, /tests separate, test.sh writes reward.txt based on pytest; agent modifying /tests doesn't help; binary must satisfy file persistence and checksum and locking.

(d) **Bypassing intended path**: Go chat server with persistence, sharding, rate limiting, presence. Bypasses like Python script true would fail Go stdlib check (no dotted imports, go.mod no external) and behavioral concurrency all 20/20 same+diff+joins+multi-shard 20/20 or 10≥9 lenient +20 strict multi-shard/join preserved with unique IDs and no lock leftover, spaces Join +10KB+Unicode both modes +10KB sharded +2000 pagination +200 rooms, global ID monotonic + after corruption reset, checksum strict for all files, rate-limit exit1 no ID/op-log + refill + multiple cycles + persistence format-agnostic + per-user + corruption, presence TTL+unknown+multi-user, snapshot dir+file mode counter exact restore, weighted 50/100/200, global broadcast dedup multiple, config validation exit2, unknown tolerance, invalid args exit2. Source-string CreateTemp+Rename advisory only; behavioral atomicity reward-critical. Private isolation, pagination offset, snapshot all files cannot be bypassed.

All files use checksum integrity with no HTML escaping, requiring proper Go JSON encoder usage for all files. Test counts now 56 Turn1 hard, 56 Turn2 balanced-hard (49 → 56 after adding back 200 rooms/2000 pagination/20 multi-shard/20 joins/10KB/unicode/private special + fixing format-agnostic persistence) – fixing online too-hard feedback (Metacode 1/10 due to strict flat format vs nested buckets artificial blocker, while 48/49 other tests passed) while keeping oracle 100% (3:01) and targeting 30-40% Metacode not 0% nor 70%.
