# codimango/chat-server

## Description

**Task Goal**: Production-grade Go chat server in two steps – hard balanced Turn1 (48 tests) and extra hard Turn2 (42 tests).

**Turn 1 – Core (Hard, 48 tests)**: CLI at `/app` module `chat-server` manages rooms, users, messages with durable persistence. Commands: `create-room` idempotent (empty ID exit2), `delete-room` true/false, `list-rooms` sorted (100 rooms), `join`/`leave` idempotent (join fail exit2 if room not exist or empty args, concurrent 10 joins preserve all 10 sorted, leave all → []), `list-users` sorted exit2 if nonexist, `send` to room (member else exit2, message via `strings.Join` remaining args, missing message exit2, special chars `<>&` no escape, raw file contains "<"), `get-messages` oldest first sorted by id asc, limit latest N, invalid limit exit2, nonexist room → [] not error, `send-private`/`get-private` 1-1 DMs (spaces via Join, isolation, both directions, limit latest N, invalid limit exit2, private special chars `<>&`), `list-all-users` sorted unique ever seen even after delete, IDs globally incrementing int64 starting at 1 unique across room+private monotonic interleaved (room1, priv1, room2, priv2), persists across restarts, not reset on delete except after corruption reset to 1. Help on bare no args contains keywords `create-room`, `delete-room`, `list-rooms`, `join`, `leave`, `list-users`, `send`, `get-messages`, `send-private`, `get-private`, `data`, `checksum` exit0. Unknown command exit2.

Persistence MUST use wrapper `{"data":{rooms, private_messages, next_id, seen_users}, "checksum": md5 canonical}` where canonical = `json.dumps(data, sort_keys=True, separators=(',',':'))` no HTML escaping – Go must use `SetEscapeHTML(false)` for checksum and file write. On write atomic via `os.CreateTemp` same dir + `os.Rename` plus file lock `<data>.lock` `O_CREATE|O_EXCL` retry loop 5ms 2000 tries + cleanup. On read: missing/empty → empty store, wrapper missing/empty checksum or mismatch or invalid JSON → corruption: backup `<path>.corrupt.<nanosec>` integer `UnixNano()`, stderr warning "corrupt"/"checksum", recreate empty valid wrapper. Behavioral hard: 10 concurrent same room preserves all 10 (extra hard, file never invalid JSON, IDs unique, lock cleaned), 10 parallel diff rooms preserves all 10, 10 concurrent joins preserves all 10 sorted. Large history 500 msgs latest N `get-messages general 10` → bulk490-499 perf <2s, all 500 retrievable, plus 100 rooms sorted.

Concepts: Go stdlib only (`go list -f '{{join .Imports " "}}'` no dotted imports, go.mod no external), CLI `--data` default, JSON persistence wrapper checksum canonical no escape, global ID monotonic, file locks cleanup, concurrent safety all 10/10, spaces Join, edge validation (empty IDs, invalid limit, missing message, nonexist empty, leave all empty, 100 rooms, concurrent joins, next_id after corruption reset).

Why naive fails Turn1 (48 tests hard):
- Flat JSON no wrapper → fails checksum strict test (requires data+checksum)
- `os.WriteFile` no lock → corruption under 10 parallel same room, diff rooms, concurrent joins → loses messages or invalid JSON, lock files remain fail cleanup tests
- `message := args[2]` not Join → fails spaces tests for room and private when message passed as multiple separate args
- Per-room counter → fails global ID interleaving (room1, priv1, room2, priv2 must be 1,2,3,4) and next_id persistence and after delete and after corruption reset
- Default `json.Marshal` escapes `<>&` → `\u003c` → MD5 mismatch and raw file no "<" → fails special chars room+private
- Invalid limit not checked → should exit2 but returns 0 → fails invalid limit tests
- Empty room/user ID not validated → should exit2 but succeeds → fails empty ID tests
- Missing message arg not checked → should exit2 → fails
- Nonexistent room get-messages returns error not [] → fails
- Leave all users should return [] not error → fails if not handling
- 100 rooms not handled sorted → fails
- Large history 500 O(n^2) or not perf → fails <2s

**Turn 2 – Large Scale (Extra Hard, 42 tests)**: Extends binary to sharded mode via `--config /app/config.json` (inherits Turn1). Config defines `shard_count`, `shards [{id,path,weight}]` (validation: shard_count≤0, empty shards, negative id, duplicate id, empty path, weight≤0 → exit2), `rate_limit {messages_per_second,burst}` default 5/s burst10, `presence_ttl_seconds` default60, `ops_log`, private/presence/rate_limit/counter/users paths. Unknown fields at top and inside shards must be ignored (tolerant).

New capabilities extra hard but solvable (≥9/10 concurrent):
- Weighted Sharding: MD5 big-endian weighted, totalWeight sum, idx=hashInt%totalWeight, iterate sorted by id subtracting weight; global: prefix → -1, get-shard-path comma-separated sorted list. create-room global:X creates in ALL shards, send replicates to all shards same ID, get-messages dedupes by ID, distribution counts rooms per shard including global in each (1 normal+2 global*4=9). Tests 20-room exact and 50-room weight tolerance.
- Rate Limiting: per-user token bucket persisted in rate_limit.json wrapper checksum, tokens=burst, last_refill=now nano, refill elapsedSec*rate, cap burst, consume1 else fail exit1 stderr "rate limit" no stdout, must NOT increment next_id nor append ops_log, per-user independent (bob succeeds when alice limited), refill after 1.6s sleep succeeds, persistence across invocations (file contains bucket), corruption handling for rate_limit.json.
- Presence: heartbeat <user> updates last_seen nano in presence.json wrapper checksum atomic global lock; get-presence returns {"user_id","online":bool,"last_seen":nano,"last_seen_seconds_ago":float} where online = now - last_seen ≤TTL*1e9, if never seen online false last_seen0; list-online sorted within TTL. Tests: heartbeat→online, TTL 2s→3s sleep offline and list-online excludes, unknown user returns online false last_seen0, corruption handling, wrapper checksum all files.
- Pagination: get-messages [limit] [offset] and get-private [limit] [offset] offset pagination sorted[offset:offset+limit] if limit>0 else [offset:], both directions private, spaces via Join, global ID order, performance 1000 room offset500 and 500 private offset250 <2s.
- Ops Log: append-only JSON lines, ops-log prints JSON array, must skip invalid JSON lines with warning stderr "corrupt"/"skip"/"warning", preserve order, content checks op types.
- Snapshot/Restore: snapshot <path> dir mode copies all shard files+private+presence+rate_limit+counter+users+ops_log+config.json basename preserved; file mode (path.json) writes combined JSON with keys shards/private/presence/rate_limit/counter/users/ops_log. restore reverses dir and file modes via atomic writes global lock, must restore counter next_id exactly so next send gets expected ID, and post-snapshot mutations gone – verified via exact file content equality and list-all-users/rooms.

Why naive fails Turn2 extra hard:
- Simple hash%shard_count not weighted → 20 exact + 50 tolerance fail.
- In-memory bucket resets → burst2 2 succeed 3rd fail exit1 no side effects + per-user + refill 1.6s + persistence + corruption fail.
- Presence always online or no unknown → TTL expiry and unknown last_seen0 fail.
- Pagination latest N only, no offset or O(n^2) → 1000 offset and 500 private perf fail.
- Snapshot only single file → restore expecting private, presence, counter exact, users, rate_limit, ops_log fail; file mode missing keys fail.
- No global lock → concurrent 10 same room and multi-shard diff rooms lose (<9 total) and lock files remain.
- No spaces Join sharded → room and private spaces fail.

## Completion Rates

Local validation oracle (`solve.sh`) – aligned with grader (48 Turn1 hard, 42 Turn2 extra hard):

| Model | Step1 (48 tests) | Step2 (42 tests) | Overall |
|-------|------------------|------------------|---------|
| Oracle | 48/48 (100%) | 42/42 (100%) | 2/2 |
| Avocado (Claude Sonnet 4.5) | 3/48 (6.3%) – fails concurrent 10/10 + diff rooms 10/10 + concurrent joins 10 + spaces Join both + private special chars + invalid limit + 100 rooms | 1/42 (2.4%) – fails weighted 50, refill, file mode snapshot, checksum all files | 0/2 |
| Opus (Claude Opus 4.6) | 5/48 (10.4%) – basic rooms but misses global ID interleaving + private special chars + lock cleanup + spaces + invalid args | 3/42 (7.1%) – sharding partial but not refill+multi-shard+file mode | 0/2 |
| Codex (GPT-5) | 10/48 (20.8%) – core passes but fails HTML escaping private, concurrent diff 10/10, 100 rooms, next_id after corruption | 9/42 (21.4%) – pagination works, rate limit refill/persistence flaky | 0/2 |
| Sonnet 4 | 4/48 (8.3%) | 2/42 (4.8%) | 0/2 |

Declared difficulty: **hard** – Turn1 now 48 tests extra hard but solvable (oracle 100%) requiring all 10/10 concurrent same+diff rooms +10 concurrent joins, checksum strict wrapper canonical no escape room+private, corruption backup integer nanosec + stderr warnings, stdlib-only, global ID monotonic interleaved + after corruption reset, spaces Join both room+private multiple args, private special chars, large history 500 latest N <2s, 100 rooms sorted, edge validation empty IDs, missing message, invalid limit, nonexistent empty, leave all empty, seen_users persists. Turn2 42 tests extra hard: weighted 50, global*shard_count, rate-limit burst2+refill+persistence+no side effects+per-user+corruption, presence unknown+TTL, snapshot dir+file mode counter exact restore, checksum all files, corruption all files, concurrent ≥9/10, config defaults+unknown tolerance, ops-log order+invalid skipping, spaces Join sharded, pagination 1000+500 <2s. Low pass rate <21% Turn1 even for strong models, ensuring hard, but solvable.

Test counts match actual pytest files (48 Turn1, 42 Turn2). All MUST behaviors graded including earlier missing and new hard extras. Cascade fixed: Turn1 solution Turn1-only (no sharded commands) → Turn2 baseline fails before Turn2 solve, avoiding DQE zeroing.

## Model Analysis

**Failure Categorization (hard 48 tests):**

1. **Checksum + HTML Escaping (25%)**: Default Marshal escapes `<>&` → MD5 mismatch vs Python canonical. Fix `SetEscapeHTML(false)` + alphabetical field order (`Content, From, ID, RoomID, Timestamp, To` for Message, `Messages, Users` for Room, `NextID, PrivateMessages, Rooms, SeenUsers` for Store) + wrapper checksum for chat.json and all sharded files (private.json, presence.json, rate_limit.json, counter.json, users.json). Private special chars test catches private path.

2. **Atomic + Locking + Concurrent All 10 (30% of failures)**: Only WriteFile → corruption under 10 parallel same room, diff rooms, concurrent joins. Hard requires file never invalid JSON and **all 10** same room (Turn1) + all 10 diff rooms + 10 concurrent joins sorted + Turn2 at least 9/10 same+multi-shard, IDs unique, lock files cleaned (`.lock`, `global.lock`). Needs `CreateTemp`+`Rename` + `O_CREATE|O_EXCL` retry 5ms 2000 tries. Advisory source check only.

3. **Spaces via Join (20% of failures)**: `send general alice Hello World with spaces` as multiple separate args must be joined. Naive `args[2]` fails. Tests for both room and private in Turn1 and sharded Turn2. Instruction says remaining args joined.

4. **Global ID + Edge Validation (15%)**: IDs globally monotonic across room+private, not per-room, plus next_id after corruption reset to 1, plus empty room/user ID exit2, missing message exit2, invalid limit exit2 (negative, non-int), nonexist room returns [] not error, leave all empty [], 100 rooms sorted. Models miss empty ID and invalid limit checks.

5. **Weighted Sharding + Global Broadcast (5% Turn2)**: Simple hash%shard_count not weighted (totalWeight sum). 20-room exact + 50-room tolerance. Global rooms replicate same ID, dedup, distribution counts global*shard_count.

6. **Rate Limiting Refill + No Side Effects + Persistence (2.5% Turn2)**: In-memory resets, must persist, exit1 no stdout, no ID increment, no ops_log, per-user independent, refill after 1.6s, persistence, corruption handling.

7. **Presence TTL + Unknown + Pagination + Snapshot File Mode (2.5% Turn2)**: Unknown returns online false last_seen0, TTL expiry 3s, offset pagination for both get-messages and get-private 1000+500 <2s, snapshot dir all files + file mode combined JSON with counter exact restore.

**Cross-model**: Avocado 6% Turn1 fails concurrent 10/10 same+diff+joins and spaces Join both + private special chars + invalid limit + 100 rooms; Codex 20.8% Turn1 passes core but fails private special chars, concurrent diff 10/10, next_id after corruption; Opus 10.4% Turn1 misses global interleaving + private special chars + lock cleanup + spaces + invalid args. Turn1 now discriminator (previously 9-10/10 pass for all models when 36 tests, now 3-10/48). Turn2 remains extra hard.

**Reasoning gaps, not setup**: Failures due to spec details (checksum canonical, weighted hash, persistent token bucket with refill and no side effects, broadcast dedup, global lock, spaces Join, counter exact restore, empty ID and invalid limit validation, 100 rooms, concurrent joins), not flaky.

## Anti-Cheating Analysis

(a) **Hardcoded outputs**: CLI invoking Go binary, file persistence checks via `json.load` + checksum, not source. Room names include zebra, alpha, middle, room0-9, room-000..099 (100 rooms), global:announce, unknownToleranceRoom, defaultTest, nonexist checks. Distribution computes expected via Python MD5 weighted exact for 20 and 50 rooms. Pagination expects bulk490-499 for 500 (Turn1 latest N) and bulk500/pbulk250 for Turn2 offset varying with history size. Rate-limit tests check counter and ops_log side effects and per-user.

(b) **Overfitting to visible tests**: Hidden hard include concurrent all 10 same room + all 10 diff rooms + 10 concurrent joins, spaces Join multiple args room+private (Turn1 and sharded Turn2), global ID interleaving + next_id after corruption reset, 500-msg Turn1 latest N +100-room sorted +1000-msg Turn2 offset +500 private offset perf <2s, seen_users persists after delete, file lock and global.lock cleanup, private special chars <>& no escape, invalid limit exit2, empty room/user ID exit2, missing message exit2, nonexist room [] and leave all [], rate-limit refill 1.6s + persistence + per-user independence + corruption handling private/rate_limit/presence, presence unknown + TTL expiry, weighted 50 rooms tolerance, global broadcast replication dedup same ID, distribution global*shard_count, checksum all files strict wrapper, snapshot file mode combined JSON with counter exact restore, config validation exit2 for shard_count≤0 etc + unknown-field tolerance top and shard level, ops-log invalid line skipping warning + content order. Overfitting to only general room fails.

(c) **Modifying test files**: Verifier runs isolated Docker, /tests separate, test.sh writes reward.txt based on pytest exit; agent modifying /tests after container start doesn't help; binary must satisfy behavior via file persistence.

(d) **Bypassing intended path**: Go chat server with persistence, sharding, rate limiting, presence. Bypasses like Python script returning true would fail Go stdlib check (no dotted imports, go.mod no external) and behavioral concurrency all 10/10 same+diff+joins preserved with unique IDs and no lock leftover, spaces Join, global ID monotonic + after corruption reset, checksum strict for all files, rate-limit exit1 no ID/op-log + refill, presence TTL+unknown, snapshot dir+file mode counter exact restore, weighted 50, global broadcast dedup, config validation exit2, unknown tolerance, invalid args exit2. Source-string CreateTemp+Rename advisory only; behavioral atomicity reward-critical. Private isolation, pagination offset, snapshot all files, config edge cases cannot be bypassed by trivial echo.

All files use checksum integrity with no HTML escaping, requiring proper Go JSON encoder usage for all files. Test counts match actual grader (48 Turn1 hard, 42 Turn2 extra hard) – balanced hard but still solvable (oracle 100%).
