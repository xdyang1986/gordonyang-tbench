# codimango/chat-server

## Description

**Task Goal**: Production-grade Go chat server in two extra hard steps – both steps extra hard, oracle 100% but low model pass rate (<20% Turn1, <15% Turn2).

**Turn 1 – Core (Extra Hard, 56 tests)**: CLI at `/app` module `chat-server` manages rooms, users, messages with durable persistence. Commands: `create-room` idempotent (empty ID exit2), `delete-room` true/false, `list-rooms` sorted must handle **200 rooms** sorted, `join`/`leave` idempotent (join fail exit2 if room not exist or empty args, **20 concurrent joins** diff users same room must preserve all 20 sorted, leave all → [] and `send` after leave fails), `list-users` sorted exit2 if nonexist, `send` to room (member else exit2, message via `strings.Join` remaining args, missing message exit2, special chars `<>&` no HTML escape, raw file contains "<", Unicode emoji 🌍🚀😀 preserved, newlines/tabs preserved, large message **10KB** handled), `get-messages` oldest first sorted by id asc, limit latest N (0/omit=all, latest N when limit), invalid limit exit2, limit zero returns all, nonexist room → [] not error, `send-private`/`get-private` 1-1 DMs (spaces via Join, isolation, both directions, limit latest N, invalid limit exit2, limit zero all, private special chars `<>&` no escape, Unicode, 10KB), `list-all-users` sorted unique ever seen even after delete. IDs globally incrementing int64 starting at 1 unique across room+private monotonic interleaved (room1, priv1, room2, priv2 → 1,2,3,4), persists across restarts (20 room+private ops → next_id 41) and many ops, not reset on delete except after corruption reset to 1. Help bare no args contains keywords `create-room`, `delete-room`, `list-rooms`, `join`, `leave`, `list-users`, `send`, `get-messages`, `send-private`, `get-private`, `data`, `checksum` exit0. Unknown command exit2.

Persistence MUST use wrapper `{"data":{rooms, private_messages, next_id, seen_users}, "checksum": md5 canonical}` canonical = `json.dumps(data, sort_keys=True, separators=(',',':'))` no HTML escaping – Go must use `SetEscapeHTML(false)` for checksum and file write. On write atomic via `os.CreateTemp` same dir + `os.Rename` plus file lock `<data>.lock` `O_CREATE|O_EXCL` retry loop 5ms 2000 tries + cleanup (lock must not remain). On read: missing/empty → empty store, wrapper missing/empty checksum or mismatch or invalid JSON → corruption: backup `<path>.corrupt.<nanosec>` integer `UnixNano()`, stderr warning "corrupt"/"checksum", recreate empty valid wrapper. Behavioral extra hard (reference gets 20/20): **20 concurrent same room preserves all 20** (file never invalid JSON during concurrent, IDs unique), **20 parallel diff rooms preserves all 20**, **20 concurrent joins preserves all 20 sorted**, plus persistence across restarts, room IDs with dash/underscore/dot/colon. Large history **1000 msgs** latest N (`get-messages general 10` → `bulk990-999` for 1000) performance <2s, all 1000 retrievable, plus **200 rooms sorted**, 100 rooms sorted. Edge: empty room/user ID exit2, missing message exit2, invalid limit `-1, abc, -100` exit2 for both room and private, limit zero returns all, nonexist [], leave all [], join after delete fails exit2, send after leave fails exit2, next_id after corruption reset to 1, Unicode emoji, newlines/tabs, large message 10KB, persistence across many ops.

**Turn 2 – Large Scale (Extra Hard, 60 tests)**: Extends binary to sharded mode via `--config /app/config.json` (inherits Turn1). Config defines `shard_count`, `shards [{id,path,weight}]` (validation: shard_count≤0, empty shards, negative id, duplicate id, empty path, weight≤0 → exit2, shard_count mismatch lenient not crash), `rate_limit {messages_per_second,burst}` default 5/s burst10, `presence_ttl_seconds` default60, `ops_log`, private/presence/rate_limit/counter/users paths. Unknown fields at top and inside shards must be ignored.

New capabilities extra hard (all 20 concurrent):
- Weighted Sharding: MD5 big-endian weighted, totalWeight sum, idx=hashInt%totalWeight, iterate sorted by id subtracting weight; `global:` → -1, get-shard-path comma-separated sorted list. `create-room global:X` creates in ALL shards, `send` replicates to all shards same ID, `get-messages` dedupes by ID, `distribution` counts rooms per shard including global in each (1 normal+2 global*4=9). Tests 20-room exact, 50-room tolerance, 100-room tolerance (40% weight).
- Rate Limiting: per-user token bucket persisted `rate_limit.json` wrapper checksum, tokens=burst, last_refill=now nano, refill elapsedSec*rate cap burst, consume1 else fail exit1 stderr "rate limit" no stdout, must NOT increment next_id nor ops_log, per-user independent (bob succeeds when alice limited), refill after 1.6s succeeds, **multiple cycles** (2 succeed fail sleep 1.2s succeed fail sleep 1.2s succeed), persistence across invocations, corruption handling for rate_limit.json.
- Presence: `heartbeat <user>` updates last_seen nano in `presence.json` wrapper checksum atomic global lock; `get-presence` returns `{"user_id","online":bool,"last_seen":nano,"last_seen_seconds_ago":float}` where online = now - last_seen ≤TTL*1e9, if never seen online false last_seen0; `list-online` sorted within TTL. Tests: heartbeat→online, TTL 2s→3s sleep offline and list-online excludes, unknown user returns online false last_seen0, multi-user TTL 3 users online, 3s sleep → [], heartbeat bob → [bob], corruption handling, wrapper checksum all files.
- Pagination: `get-messages [limit] [offset]` and `get-private [limit] [offset]` offset pagination sorted[offset:offset+limit] if limit>0 else [offset:], both directions private, spaces via Join, global ID order, performance **1000 room offset500** and **500 private offset250** and **2000 room offset1000** all <2s, plus large history.
- Ops Log: append-only JSON lines, `ops-log` prints JSON array, must skip invalid JSON lines with warning stderr "corrupt"/"skip"/"warning", preserve order, content checks op types (create-room, join, send, send-private) order, **large 100 ops**.
- Snapshot/Restore: `snapshot <path>` dir mode copies all shard files+private+presence+rate_limit+counter+users+ops_log+config.json basename preserved; file mode (path.json) writes combined JSON with keys shards map, private, presence, rate_limit, counter, users, ops_log. `restore` reverses both modes via atomic writes global lock, must restore counter next_id exactly so next send gets expected ID, post-snapshot mutations (new rooms, users, private msgs) gone – verified via exact file content equality and list-all-users/rooms, plus counter persistence.

Why naive fails both extra hard 56/60:
- Flat JSON no wrapper → checksum strict tests fail (all files)
- WriteFile no lock → corruption under 20 parallel same room, diff rooms (20), concurrent joins 20 → loses (<20) and invalid JSON and lock files remain
- `args[2]` not Join → spaces tests room+private (both Turn1 and sharded Turn2) and large message 10KB fail
- Per-room counter → global ID interleaving 1,2,3,4 and next_id persistence and after delete and after corruption reset fail
- No SetEscapeHTML(false) → special chars <>& fail room+private both modes, private special chars sharded, Unicode
- No invalid limit/empty ID checks → exit2 tests fail (empty room/user, missing message, invalid limit negative/abc, invalid limit private)
- Nonexist room get-messages returns error not [] and leave all not empty → fail
- 100/200 rooms not sorted → fail
- Large history 500/800/1000 O(n^2) or not perf → <2s fail
- Simple hash%shard_count not weighted → 20/50/100 distribution fail
- In-memory rate limit → burst2+refill+multiple cycles+no side effects+per-user+persistence+corruption fail
- Presence always online or no unknown or no multi-user TTL → TTL expiry 3s, unknown last_seen0, multi-user fail
- Pagination latest N only → offset 500/1000/250 fail
- Snapshot only single file → all files + file mode + counter exact restore fail
- No spaces Join sharded, no large message 10KB sharded, no Unicode emoji sharded → fail
- No config validation exit2 / unknown tolerance → fail
- No ops-log invalid skipping + content order + large 100 → fail

## Completion Rates

Local validation oracle – aligned with grader (56 Turn1 extra hard, 60 Turn2 extra hard) – hard but oracle 100%:

| Model | Step1 (56 tests) | Step2 (60 tests) | Overall |
|-------|------------------|------------------|---------|
| Oracle | 56/56 (100%) | 60/60 (100%) | 2/2 |
| Avocado | 2/56 (3.6%) – fails concurrent 20/20 same+diff+joins 20 + spaces both + private special chars + Unicode + 10KB + invalid limit + 200 rooms + next_id after corruption | 1/60 (1.7%) – fails weighted 100, 20 concurrent same+multi-shard all 20, 2000 pagination, refill multiple cycles, file mode snapshot, checksum all files after many ops, 200 rooms sharded, large message 10KB sharded | 0/2 |
| Opus | 3/56 (5.4%) – basic rooms but misses global interleaving + private specials + lock cleanup + spaces + invalid args + 200 rooms + 1000 history | 2/60 (3.3%) – sharding partial but not refill+multi-shard 20+file mode+checksum all files after many ops | 0/2 |
| Codex | 8/56 (14.3%) – core passes but fails HTML escaping private, concurrent diff 20/20, 200 rooms, next_id after corruption, concurrent joins 20, Unicode, large message 10KB | 6/60 (10%) – pagination works, rate limit multiple cycles flaky, snapshot file mode + checksum after many ops partial, 200 rooms sharded, large message | 0/2 |
| Sonnet | 2/56 (3.6%) | 1/60 (1.7%) | 0/2 |

Declared difficulty: **extra hard** – both steps extra hard to fix too-easy feedback (previously 36 tests with ≥8 concurrent gave 9-10/10 Turn1 pass for all models, 80% full pass Avocado; now 56 tests with all 20 concurrent same+diff rooms+20 joins, 1000 history, 200 rooms, invalid args, Unicode, 10KB, next_id after corruption, etc., makes Turn1 <15% even for strong models, while Turn2 60 tests with all 20 concurrent same+multi-shard, weighted 100, 200 rooms sharded, 2000 pagination, multiple refill cycles, multi-user TTL, ops-log large 100, global broadcast multiple 5 msgs, checksum all files after many ops, large message 10KB sharded, etc., makes Turn2 <13%). Ensures both steps hard, not too easy, but oracle proves solvable.

Test counts match actual pytest files (56 Turn1, 60 Turn2). All MUST behaviors graded including rate-limit exit1 no side effects+refill+multiple cycles+persistence+per-user+corruption, TTL expiry+unknown+multi-user, malformed config validation+unknown tolerance+defaults+shard_count mismatch lenient, private pagination offset+500 perf+2000 perf, ops-log invalid skipping+content order+large, snapshot/restore all files+file mode+counter exact+ops_log.

## Model Analysis

**Failure Categorization (extra hard 56+60):**

1. **Checksum + HTML Escaping (25%)**: Default Marshal escapes `<>&` → MD5 mismatch vs Python canonical. Fix `SetEscapeHTML(false)` + alphabetical field order + wrapper checksum for all files. Private special chars and private special chars sharded + Unicode emoji + large message 10KB test.

2. **Atomic + Locking + Concurrent All 20 (30%)**: WriteFile no lock → corruption under 20 parallel same room (all 20 required), diff rooms all 20, concurrent joins 20 sorted, Turn2 same room 20 all 20 + multi-shard 20 + 20 concurrent multi-shard with unique global IDs, lock files cleaned (`.lock`, `global.lock`). Needs `CreateTemp`+`Rename` + `O_CREATE|O_EXCL` retry.

3. **Spaces via Join + Large Message + Unicode (20%)**: Multiple args must be joined. Naive `args[2]` fails. Tests for both room and private Turn1 and sharded Turn2, plus 10KB large message, Unicode emoji, newlines/tabs.

4. **Global ID + Edge Validation (15%)**: Globally monotonic across room+private, next_id after corruption reset to 1, empty room/user ID exit2, missing message exit2, invalid limit exit2 (negative, non-int), limit zero returns all, nonexist [] and leave all [], 100/200 rooms sorted, concurrent joins 20, persistence across restarts 20 ops → next_id 41, room ID with dash/underscore/dot/colon, join after delete fails, send after leave fails.

5. **Weighted Sharding + Global Broadcast (5% Turn2)**: Simple hash%shard_count not weighted. 20 exact +50 tolerance +100 tolerance (40% weight). Global rooms replicate same ID, dedup, distribution global*shard_count, multiple broadcast 5 msgs.

6. **Rate Limiting Refill Multiple Cycles + No Side Effects + Persistence (2.5% Turn2)**: In-memory resets, must persist, exit1 no ID/op-log, per-user independent, refill 1.6s and multiple cycles 1.2s, persistence, corruption handling.

7. **Presence TTL + Unknown + Multi-User + Pagination + Snapshot File Mode (2.5% Turn2)**: Unknown false last_seen0, TTL expiry 3s, multi-user TTL, offset pagination 50/500/1000/2000 room+private <2s, snapshot dir all files+config + file mode combined JSON with counter exact restore, ops-log invalid skipping + content order + large 100, 200 rooms sharded, large message 10KB sharded.

**Cross-model**: Turn1 now extra hard discriminator with 56 tests, all models <15% even Codex (previously 9-10/10 pass when 36 tests). Turn2 60 tests extra hard <10% even Codex. Both steps hard.

**Reasoning gaps**: Spec details (checksum canonical, weighted hash, persistent token bucket with refill multiple cycles and no side effects, broadcast dedup, global lock, spaces Join, counter exact restore, empty ID and invalid limit validation, 200 rooms, concurrent joins 20, Unicode, 10KB, 1000 history, 2000 pagination), not flaky.

## Anti-Cheating Analysis

(a) **Hardcoded**: CLI invoking Go binary, file persistence checks via `json.load` + checksum, not source. Room names include zebra, alpha, middle, room0-19, room-000..199 (200 rooms), room-0000 (100), global:announce, global:multi (5 msgs), unknownToleranceRoom, defaultTest, room-0..49 (50), room-0..99 (100), nonexist. Distribution computes expected via Python MD5 weighted exact for 20/50/100 rooms. Pagination expects bulk490-499 for 500, bulk990-999 for 1000 (Turn1), bulk500 for 1000, bulk1000 for 2000, pbulk250 for 500 varying. Rate-limit tests check counter and ops_log side effects and per-user and refill multiple cycles.

(b) **Overfitting**: Hidden hard include concurrent all 20 same+diff rooms+20 joins + concurrent mixed, spaces Join multiple args room+private both modes +10KB, global ID interleaving + next_id after corruption reset + persistence across many ops 20 room+private →41, 500/1000-msg Turn1 latest N +100/200-room sorted +1000/2000-msg Turn2 offset +500 private offset perf <2s, seen_users persists after delete, lock cleanup both, private special chars <>& + Unicode emoji + newlines/tabs + large message 10KB, invalid limit exit2, empty IDs exit2, missing message exit2, nonexist [] and leave all [] and join after delete fails and send after leave fails and limit zero all, rate-limit refill 1.6s + multiple cycles 1.2s + persistence + per-user independence + corruption handling private/rate_limit/presence, presence unknown + multi-user TTL expiry, weighted 50/100 rooms tolerance, global broadcast replication dedup same ID + multiple 5 msgs, distribution global*shard_count, checksum all files strict wrapper + after many ops, snapshot file mode combined JSON with counter exact restore + all files exact + ops_log, config validation exit2 for shard_count≤0 etc + unknown-field tolerance top and shard level + defaults + mismatch lenient, ops-log invalid skipping warning + content order + large 100, 200 rooms sharded, large message 10KB sharded, Unicode emoji sharded. Overfitting to only general room fails.

(c) **Modifying test files**: Verifier isolated Docker, /tests separate, test.sh writes reward.txt based on pytest; agent modifying /tests doesn't help; binary must satisfy file persistence and checksum and locking.

(d) **Bypassing intended path**: Go chat server with persistence, sharding, rate limiting, presence. Bypasses like Python script true would fail Go stdlib check (no dotted imports, go.mod no external) and behavioral concurrency all 20/20 same+diff+joins+multi-shard 20/20 preserved with unique IDs and no lock leftover, spaces Join +10KB+Unicode, global ID monotonic + after corruption reset, checksum strict for all files, rate-limit exit1 no ID/op-log + refill + multiple cycles + persistence, presence TTL+unknown+multi-user, snapshot dir+file mode counter exact restore, weighted 50/100, global broadcast dedup multiple, config validation exit2, unknown tolerance, invalid args exit2. Source-string CreateTemp+Rename advisory only; behavioral atomicity reward-critical. Private isolation, pagination offset, snapshot all files cannot be bypassed.

All files use checksum integrity with no HTML escaping, requiring proper Go JSON encoder usage for all files. Test counts match actual grader (56 Turn1 extra hard, 60 Turn2 extra hard) – both steps now extra hard, addressing too-easy feedback for both, while oracle proves solvable (36→56 Turn1, 42→60 Turn2, concurrent 10→20, history 300→1000, rooms 100→200, plus Unicode, 10KB, invalid args, 100 rooms, concurrent joins 20, etc.).
