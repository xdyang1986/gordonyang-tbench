# codimango/chat-server

## Description

**Task Goal**: Production-grade Go chat server in two extra hard steps – balanced hard, oracle 100% but low model pass rate.

**Turn 1 – Core (Extra Hard, 52 tests)**: CLI at `/app` module `chat-server` manages rooms, users, messages with durable persistence. Commands: `create-room` idempotent (empty ID exit2), `delete-room` true/false, `list-rooms` sorted must handle 200 rooms, `join`/`leave` idempotent (join fail exit2 if room not exist or empty args, **20 concurrent joins** different users same room must preserve all 20 sorted, leave all → `[]`), `list-users` sorted exit2 if nonexist, `send` to room (member else exit2, message via `strings.Join` remaining args, missing message exit2, special chars `<>&` no HTML escape, Unicode emojis 🌍🚀 and newlines/tabs preserved, raw file contains "<"), `get-messages` oldest first sorted by id asc, limit latest N, invalid limit exit2, nonexist room → `[]`, `send-private`/`get-private` 1-1 DMs (spaces via Join, isolation, both directions, limit latest N, invalid limit exit2, private special chars `<>&` no escape, Unicode), `list-all-users` sorted unique ever seen even after delete. IDs globally incrementing int64 starting at 1 unique across room+private monotonic interleaved (1,2,3,4), persists across restarts (20 room+private ops → next_id 41), not reset on delete except after corruption reset to 1. Help on bare no args contains keywords `create-room`, `delete-room`, `list-rooms`, `join`, `leave`, `list-users`, `send`, `get-messages`, `send-private`, `get-private`, `data`, `checksum` exit0. Unknown command exit2.

Persistence MUST use wrapper `{"data":{rooms, private_messages, next_id, seen_users}, "checksum": md5 canonical}` canonical = `json.dumps(data, sort_keys=True, separators=(',',':'))` no HTML escaping – Go must use `SetEscapeHTML(false)` for checksum and file write. On write atomic via `os.CreateTemp` same dir + `os.Rename` plus file lock `<data>.lock` `O_CREATE|O_EXCL` retry loop 5ms 2000 tries + cleanup after each command. On read: missing/empty → empty store, wrapper missing/empty checksum or mismatch or invalid JSON → corruption: backup `<path>.corrupt.<nanosec>` integer `UnixNano()`, stderr warning "corrupt"/"checksum", recreate empty valid wrapper. Behavioral extra hard: **20 concurrent same room preserves all 20** (file never invalid JSON during concurrent, IDs unique, lock cleaned), **20 parallel diff rooms preserves all 20**, **20 concurrent joins preserves all 10/20 sorted**. Large history **800 msgs** latest N (`get-messages general 10` → `bulk790-799` for 800) performance <2s, all 800 retrievable, plus **200 rooms** sorted. Edge validation: empty room/user ID exit2, missing message exit2, invalid limit (`-1`, `abc`, `-100`) exit2, nonexist room → `[]`, leave all → `[]`, large number of rooms 200, concurrent joins 20, next_id after corruption reset to 1, Unicode emoji, newlines/tabs, persistence across restarts with many ops, room ID with dash/underscore/dot/colon.

**Turn 2 – Large Scale (Extra Hard, 53 tests)**: Extends binary to sharded mode via `--config /app/config.json` (inherits Turn1). Config defines `shard_count`, `shards [{id,path,weight}]` (validation: shard_count≤0, empty shards, negative id, duplicate id, empty path, weight≤0 → exit2, shard_count mismatch lenient not crash), `rate_limit {messages_per_second,burst}` default 5/s burst10, `presence_ttl_seconds` default60, `ops_log`, private/presence/rate_limit/counter/users paths. Unknown fields at top and inside shards must be ignored.

New capabilities extra hard (concurrent all 20):
- Weighted Sharding: MD5 big-endian weighted, totalWeight sum, idx=hashInt%totalWeight, iterate sorted by id subtracting weight; `global:` → -1, get-shard-path comma-separated sorted list. `create-room global:X` creates in ALL shards, `send` replicates to all shards same ID, `get-messages` dedupes by ID, `distribution` counts rooms per shard including global in each (1 normal+2 global*4=9). Tests 20-room exact, 50-room tolerance, **100-room tolerance** (40% weight).
- Rate Limiting: per-user token bucket persisted `rate_limit.json` wrapper checksum, tokens=burst, last_refill=now nano, refill elapsedSec*rate cap burst, consume1 else fail exit1 stderr "rate limit" no stdout, must NOT increment next_id nor ops_log, per-user independent (bob succeeds when alice limited), **refill after 1.6s** succeeds, **multiple cycles** (2 succeed, fail, 1.2s, succeed, fail, 1.2s, succeed), persistence across invocations (file contains bucket), corruption handling for rate_limit.json.
- Presence: `heartbeat <user>` updates last_seen nano in `presence.json` wrapper checksum atomic global lock; `get-presence` returns `{"user_id","online":bool,"last_seen":nano,"last_seen_seconds_ago":float}` where `online = now - last_seen ≤TTL*1e9`, if never seen online false last_seen0; `list-online` sorted within TTL. Tests: heartbeat→online, TTL 2s→3s sleep offline, list-online excludes, unknown user returns online false last_seen0, **multiple users TTL** (3 users online, 3s sleep → [], heartbeat bob → [bob]), corruption handling, wrapper checksum all files.
- Pagination: `get-messages [limit] [offset]` and `get-private [limit] [offset]` offset pagination sorted[offset:offset+limit] if limit>0 else [offset:], both directions private, spaces via Join, global ID order, performance **1000 room offset500** and **500 private offset250** and **2000 room offset1000** all <2s.
- Ops Log: append-only JSON lines, `ops-log` prints JSON array, must skip invalid JSON lines with warning stderr "corrupt"/"skip"/"warning", preserve order, content checks op types, **large 100 ops**.
- Snapshot/Restore: `snapshot <path>` dir mode copies all shard files+private+presence+rate_limit+counter+users+ops_log+config.json basename preserved; file mode (path.json) writes combined JSON with keys shards/private/presence/rate_limit/counter/users/ops_log. `restore` reverses dir and file modes via atomic writes global lock, must restore counter next_id exactly so next send gets expected ID, and post-snapshot mutations (new rooms, users, private msgs) gone – verified via exact file content equality and list-all-users/rooms, plus counter persistence after restore.

Why naive fails both extra hard:
- Flat JSON no wrapper → checksum strict tests fail (all files)
- WriteFile no lock → corruption under 20 parallel same room, diff rooms (10/20), concurrent joins 20 → loses (<20) and invalid JSON and lock files remain
- `args[2]` not Join → spaces tests room+private (both Turn1 and sharded Turn2) fail
- Per-room counter → global ID interleaving 1,2,3,4 and next_id persistence and after delete and after corruption reset fail
- No SetEscapeHTML(false) → special chars <>& fail room+private both modes, private special chars sharded
- No invalid limit/empty ID checks → exit2 tests fail
- No nonexist [] handling → nonexist returns error not [] and leave all not empty
- 100/200 rooms not handled sorted → fails
- Large history 500/800/1000/2000 O(n^2) or not perf → <2s fails
- Simple hash%shard_count not weighted → 20/50/100 distribution fail
- In-memory rate limit → burst2+refill+multiple cycles+no side effects+per-user+persistence+corruption fail
- Presence always online or no unknown or no multi-user TTL → TTL expiry 3s, unknown last_seen0, multi-user fail
- Pagination latest N only → offset 500/1000/250 fail
- Snapshot only single file → all files + file mode + counter exact restore fail
- No spaces Join sharded → fail
- No config validation exit2 / unknown tolerance → fail

## Completion Rates

Local validation oracle – aligned with grader (52 Turn1 extra hard, 53 Turn2 extra hard) – hard but oracle 100%:

| Model | Step1 (52 tests) | Step2 (53 tests) | Overall |
|-------|------------------|------------------|---------|
| Oracle | 52/52 (100%) | 53/53 (100%) | 2/2 |
| Avocado | 2/52 (3.8%) – fails concurrent 20/20 same+diff+joins, spaces both, private special chars, invalid limit, 200 rooms, Unicode | 1/53 (1.9%) – fails weighted 100, 20 concurrent, 2000 pagination, refill multiple cycles, file mode snapshot | 0/2 |
| Opus | 4/52 (7.7%) – basic rooms but misses global interleaving + private specials + lock cleanup + spaces + invalid args + 200 rooms | 2/53 (3.8%) – sharding partial but not refill+persistence+multi-shard 20 | 0/2 |
| Codex | 9/52 (17.3%) – core passes but fails HTML escaping private, concurrent diff 20/20, 200 rooms, next_id after corruption, concurrent joins 20 | 7/53 (13.2%) – pagination works but rate limit multiple cycles flaky, snapshot file mode + checksum all files after many ops partial | 0/2 |
| Sonnet | 3/52 (5.8%) | 1/53 (1.9%) | 0/2 |

Declared difficulty: **extra hard** – both steps extra hard to address too-easy feedback (previously 36 tests with ≥8 concurrent gave 9-10/10 pass for all models, 80% full pass Avocado; now 52 tests with all 20 concurrent same+diff rooms +20 concurrent joins +500-800 history +200 rooms + invalid args + Unicode + special chars + global ID + lock cleanup makes Turn1 hard, <21% even for strong models). Turn2 53 tests extra hard: 20 concurrent same+multi-shard all 20, weighted 100, 2000 pagination, multiple refill cycles, multi-user TTL, ops-log large 100, global broadcast multiple 5 msgs, checksum all files after many ops, config validation mismatch lenient, private special chars sharded, invalid args sharded. Ensures both steps hard, not too easy, but oracle proves solvable.

Test counts match actual pytest files (52 Turn1, 53 Turn2). All MUST behaviors graded including rate-limit exit1 no side effects+refill+multiple cycles+persistence+per-user+corruption, TTL expiry+unknown+multi-user, malformed config validation+unknown tolerance+defaults, private pagination offset+500 perf+2000 perf, ops-log invalid skipping+content order+large, snapshot/restore all files+file mode+counter exact.

## Model Analysis

**Failure Categorization (extra hard 52+53):**

1. **Checksum + HTML Escaping (25%)**: Default Marshal escapes `<>&` → MD5 mismatch. Fix `SetEscapeHTML(false)` + alphabetical field order + wrapper checksum for chat.json and all sharded files. Private special chars and private special chars sharded test.

2. **Atomic + Locking + Concurrent All 10/20 (30%)**: WriteFile no lock → corruption under 20 parallel same room (all 20 required), diff rooms all 20, concurrent joins 20 sorted, Turn2 same room 20 (all 20) + multi-shard 20 diff rooms + 20 concurrent multi-shard with unique IDs, lock files cleaned (`.lock`, `global.lock`). Needs `CreateTemp`+`Rename` + `O_CREATE|O_EXCL` retry.

3. **Spaces via Join (20%)**: Multiple separate args must be joined. Naive `args[2]` fails. Tests for both room and private Turn1 and sharded Turn2.

4. **Global ID + Edge Validation (15%)**: Globally monotonic across room+private, next_id after corruption reset to 1, empty room/user ID exit2, missing message exit2, invalid limit exit2 (negative, non-int), nonexist [] and leave all [], 100/200 rooms sorted, Unicode emoji, newlines/tabs, persistence across restarts 20 ops → next_id 41, room ID with dash/underscore/dot/colon.

5. **Weighted Sharding + Global Broadcast (5% Turn2)**: Simple hash%shard_count not weighted. 20 exact +50 tolerance +100 tolerance. Global rooms replicate same ID, dedup, distribution global*shard_count.

6. **Rate Limiting Refill Multiple Cycles + No Side Effects + Persistence (2.5% Turn2)**: In-memory resets, must persist, exit1 no ID/op-log, per-user independent, refill after 1.6s and multiple cycles 1.2s, persistence, corruption handling.

7. **Presence TTL + Unknown + Multi-User + Pagination + Snapshot File Mode (2.5% Turn2)**: Unknown false last_seen0, TTL expiry 3s, multi-user TTL, offset pagination 50/500/1000/2000 room+private <2s, snapshot dir all files+config + file mode combined JSON with counter exact restore, ops-log invalid skipping + content order + large 100.

**Cross-model**: Turn1 now discriminator with 52 tests, all models <21% even Codex (previously 9-10/10 pass when 36 tests). Turn2 remains extra hard.

**Reasoning gaps**: Spec details (checksum canonical, weighted hash, persistent token bucket with refill and multiple cycles and no side effects, broadcast dedup, global lock, spaces Join, counter exact restore, empty ID and invalid limit validation, 100/200 rooms, concurrent joins 20, Unicode), not flaky.

## Anti-Cheating Analysis

(a) **Hardcoded**: CLI invoking Go binary, file persistence checks via `json.load` + checksum, not source. Room names include zebra, alpha, room0-19, room-000..199 (200 rooms), room-0000.. (100), global:announce, global:multi, unknownToleranceRoom, defaultTest, nonexist. Distribution computes expected via Python MD5 weighted exact for 20/50/100 rooms. Pagination expects bulk490-499 for 500, bulk790-799 for 800, bulk500 for 1000, bulk1000 for 2000, pbulk250 for 500 varying with history size. Rate-limit tests check counter and ops_log side effects and per-user.

(b) **Overfitting**: Hidden hard include concurrent all 20 same+diff rooms+20 joins, spaces Join multiple args room+private both modes, global ID interleaving + next_id after corruption reset, 500/800-msg Turn1 latest N +100/200-room sorted +1000/2000-msg Turn2 offset +500 private offset perf <2s, seen_users persists after delete, lock cleanup both, private special chars <>& + Unicode emoji + newlines/tabs, invalid limit exit2, empty IDs exit2, missing message exit2, nonexist [] and leave all [], large rooms, rate-limit refill 1.6s + multiple cycles + persistence + per-user independence + corruption handling private/rate_limit/presence, presence unknown + multi-user TTL expiry, weighted 50/100 rooms tolerance, global broadcast replication dedup same ID + multiple 5 msgs, distribution global*shard_count, checksum all files strict wrapper + after many ops, snapshot file mode combined JSON with counter exact restore + all files exact, config validation exit2 for shard_count≤0 etc + unknown-field tolerance top and shard level + defaults + mismatch lenient, ops-log invalid skipping warning + content order + large 100. Overfitting to only general room fails.

(c) **Modifying test files**: Verifier isolated Docker, /tests separate, test.sh writes reward.txt based on pytest; agent modifying /tests doesn't help; binary must satisfy file persistence and checksum.

(d) **Bypassing intended path**: Go chat server with persistence, sharding, rate limiting, presence. Bypasses like Python script true would fail Go stdlib check (no dotted imports, go.mod no external) and behavioral concurrency all 10/20 same+diff+joins preserved with unique IDs and no lock leftover, spaces Join, global ID monotonic + after corruption reset, checksum strict for all files, rate-limit exit1 no ID/op-log + refill + multiple cycles, presence TTL+unknown+multi-user, snapshot dir+file mode counter exact restore, weighted 50/100, global broadcast dedup multiple, config validation exit2, unknown tolerance, invalid args exit2. Source-string CreateTemp+Rename advisory only; behavioral atomicity reward-critical. Private isolation, pagination offset, snapshot all files cannot be bypassed.

All files use checksum integrity with no HTML escaping, requiring proper Go JSON encoder usage for all files. Test counts match actual grader (52 Turn1 extra hard, 53 Turn2 extra hard) – both steps hard now, addressing too-easy feedback, while oracle proves solvable (48→52 Turn1, 42→53 Turn2, same room 10→20, diff rooms 10→20, history 500→800, rooms 100→200, concurrent joins 10→20, plus Unicode, newlines, 100/200 rooms, invalid args, etc.).
