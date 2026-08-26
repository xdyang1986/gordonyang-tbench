# codimango/chat-server

## Description

**Task Goal**: Production-grade Go chat server in two hard-balanced steps – oracle 100% with a monotonic model spread (opus 8/10, gpt 5/9, avocado 3/10 at `e5d715d`).

**Turn 1 – Core (56 tests)**: CLI at `/app` module `chat-server` manages rooms, users, messages with durable persistence. Commands: `create-room` idempotent (empty ID exit2), `delete-room` true/false, `list-rooms` sorted must handle **200 rooms** sorted, `join`/`leave` idempotent (join fail exit2 if room not exist or empty args, **20 concurrent joins** diff users same room must preserve all 20 sorted, leave all → [] and `send` after leave fails), `list-users` sorted exit2 if nonexist, `send` to room (member else exit2, message via `strings.Join` remaining args, missing message exit2, special chars `<>&` no HTML escape, raw file contains "<", Unicode emoji 🌍🚀😀 preserved, newlines/tabs preserved, large message **10KB** handled), `get-messages` oldest first sorted by id asc, limit latest N (0/omit=all, latest N when limit), invalid limit exit2, limit zero returns all, nonexist room → [] not error, `send-private`/`get-private` 1-1 DMs (spaces via Join, isolation, both directions, limit latest N, invalid limit exit2, limit zero all, private special chars `<>&` no escape, Unicode, 10KB), `list-all-users` sorted unique ever seen even after delete. IDs globally incrementing int64 starting at 1 unique across room+private monotonic interleaved (room1, priv1, room2, priv2 → 1,2,3,4), persists across restarts (20 room+private ops → next_id 41) and many ops, not reset on delete except after corruption reset to 1. Help bare no args contains keywords `create-room`, `delete-room`, `list-rooms`, `join`, `leave`, `list-users`, `send`, `get-messages`, `send-private`, `get-private`, `data`, `checksum` exit0. Unknown command exit2.

Persistence MUST use wrapper `{"data":{rooms, private_messages, next_id, seen_users}, "checksum": md5 canonical}` canonical = `json.dumps(data, sort_keys=True, separators=(',',':'))` no HTML escaping – Go must use `SetEscapeHTML(false)` for checksum and file write. On write atomic via `os.CreateTemp` same dir + `os.Rename` plus file lock `<data>.lock` `O_CREATE|O_EXCL` retry loop 5ms 2000 tries + cleanup (lock must not remain). On read: missing/empty → empty store, wrapper missing/empty checksum or mismatch or invalid JSON → corruption: backup `<path>.corrupt.<nanosec>` integer `UnixNano()`, stderr warning "corrupt"/"checksum", recreate empty valid wrapper. Behavioral hard (reference gets 20/20): **20 concurrent same room preserves all 20** (file never invalid JSON during concurrent, IDs unique), **20 parallel diff rooms preserves all 20**, **20 concurrent joins preserves all 20 sorted**, plus persistence across restarts, room IDs with dash/underscore/dot/colon. Large history **1000 msgs** latest N (`get-messages general 10` → `bulk990-999` for 1000) performance <2s, all 1000 retrievable, plus **200 rooms sorted**, 100 rooms sorted. Edge: empty room/user ID exit2, missing message exit2, invalid limit `-1, abc, -100` exit2 for both room and private, limit zero returns all, nonexist [], leave all [], join after delete fails exit2, send after leave fails exit2, next_id after corruption reset to 1, Unicode emoji, newlines/tabs, large message 10KB, persistence across many ops.

**Turn 2 – Large Scale (58 tests)**: Extends binary to sharded mode via `--config /app/config.json` (inherits Turn1). Config defines `shard_count`, `shards [{id,path,weight}]` (validation: shard_count≤0, empty shards, negative id, duplicate id, empty path, weight≤0 → exit2, shard_count mismatch lenient not crash), `rate_limit {messages_per_second,burst}` default 5/s burst10 – `messages_per_second` may be fractional so it must be parsed as a float, `presence_ttl_seconds` default60, `ops_log`, private/presence/rate_limit/counter/users paths. Unknown fields at top and inside shards must be ignored. Concurrency requirements are strict all-20: 20 concurrent multi-shard sends all preserved with unique IDs, 20 concurrent joins all 20 sorted. Edge coverage: 200 rooms sharded, large message 10KB sharded + private 10KB, nonexist empty and leave-all empty sharded, unicode emoji sharded, private special chars sharded. Rate_limit persistence is format-agnostic (flat map and nested `buckets` both accepted).

New capabilities:
- Weighted Sharding: MD5 big-endian weighted, totalWeight sum, idx=hashInt%totalWeight, iterate sorted by id subtracting weight; `global:` → -1, get-shard-path comma-separated sorted list. `create-room global:X` creates in ALL shards, `send` replicates to all shards same ID, `get-messages` dedupes by ID, `distribution` counts rooms per shard including global in each (1 normal+2 global*4=9). Tests 20-room exact, 100-room tolerance (40% weight) plus 200-room sorted.
- Rate Limiting: per-user token bucket persisted `rate_limit.json` wrapper checksum, tokens=burst, last_refill=now nano, refill elapsedSec*rate cap burst, consume1 else fail exit1 stderr "rate limit" no stdout, must NOT increment next_id nor ops_log, per-user independent (bob succeeds when alice limited), refill after 1.6s succeeds, **multiple cycles** (2 succeed fail sleep 1.2s succeed fail sleep 1.2s succeed), persistence across invocations format-agnostic (flat and nested buckets both accepted), corruption handling for rate_limit.json. The bucket is **shared across `send` and `send-private`** – one quota per user, not per message type. A `global:` send costs exactly **one** token (not one per shard), and a rate-limited `global:` send must leave every shard file byte-identical – no partial replication, no counter advance, no ops-log append.
- Presence: `heartbeat <user>` updates last_seen nano in `presence.json` wrapper checksum atomic global lock; `get-presence` returns `{"user_id","online":bool,"last_seen":nano,"last_seen_seconds_ago":float}` where online = now - last_seen ≤TTL*1e9, if never seen online false last_seen0; `list-online` sorted within TTL. Tests: heartbeat→online, TTL 2s→3s sleep offline and list-online excludes, unknown user returns online false last_seen0, multi-user TTL 3 users online, 3s sleep → [], heartbeat bob → [bob], corruption handling, wrapper checksum all files.
- Pagination: `get-messages [limit] [offset]` and `get-private [limit] [offset]` offset pagination sorted[offset:offset+limit] if limit>0 else [offset:], both directions private, spaces via Join, global ID order, performance **1000 room offset500** and **500 private offset250** both <2s.
- Ops Log: append-only JSON lines, `ops-log` prints JSON array, must skip invalid JSON lines with warning stderr "corrupt"/"skip"/"warning", preserve order, content checks op types (create-room, join, send, send-private) order, **large 100 ops**.
- Snapshot/Restore: `snapshot <path>` dir mode copies all shard files+private+presence+rate_limit+counter+users+ops_log+config.json basename preserved; file mode (path.json) writes combined JSON with keys shards map, private, presence, rate_limit, counter, users, ops_log. `restore` reverses both modes via atomic writes global lock, must restore counter next_id exactly so next send gets expected ID, post-snapshot mutations (new rooms, users, private msgs) gone – verified via exact file content equality and list-all-users/rooms, plus counter persistence.

Why naive fails both steps (56/58):
- Flat JSON no wrapper → checksum strict tests fail (all files)
- WriteFile no lock → corruption under 20 parallel same room, diff rooms (20), concurrent joins 20 → loses (<20) and invalid JSON and lock files remain
- `args[2]` not Join → spaces tests room+private (both Turn1 and sharded Turn2) and large message 10KB fail
- Per-room counter → global ID interleaving 1,2,3,4 and next_id persistence and after delete and after corruption reset fail
- No SetEscapeHTML(false) → special chars <>& fail room+private both modes, private special chars sharded, Unicode
- No invalid limit/empty ID checks → exit2 tests fail (empty room/user, missing message, invalid limit negative/abc, invalid limit private)
- Nonexist room get-messages returns error not [] and leave all not empty → fail
- 100/200 rooms not sorted → fail
- Large history 500/800/1000 O(n^2) or not perf → <2s fail
- Simple hash%shard_count not weighted → 20/100/200 distribution fail
- In-memory rate limit → burst2+refill+multiple cycles+no side effects+per-user+persistence+corruption fail
- `int` for `messages_per_second` → unmarshal error on fractional rates (0.05) → every rate-limit test fails
- Separate quotas for `send` vs `send-private`, or per-shard token cost on `global:` sends, or a partially-replicated rate-limited broadcast → the three global-broadcast rate-limit tests fail
- Presence always online or no unknown or no multi-user TTL → TTL expiry 3s, unknown last_seen0, multi-user fail
- Pagination latest N only → offset 500/1000/250/1000 fail
- Snapshot only single file → all files + file mode + counter exact restore fail
- No spaces Join sharded, no large message 10KB sharded, no Unicode emoji sharded → fail
- No config validation exit2 / unknown tolerance → fail
- No ops-log invalid skipping + content order + large 100 → fail

## Completion Rates

### Latest online validation — commit `e5d715d` (56 + 58 tests, now 59 + 61 after robustness fixes)

**Validation status: PASSING.** Structural 10/10 PASS, oracle 3/3, provenance clean, contamination not checked (repo not yet covered by the pipeline). Agentic Full-Task Review at this commit: **GOOD / GENUINELY_HARD**, all 13 rubrics PASS, secondary issues NONE.

| Stage | Agent / Model | Full multi-turn | Turn 1 | Turn 2 (given T1 pass) |
|-------|---------------|-----------------|--------|------------------------|
| Oracle | oracle | 3/3 (100%) | 3/3 | 3/3 |
| Agent | claude-code / claude-opus-5 | **8/10 (80%)** | 8/10 | 8/8 |
| Codex | gpt-5.5 | **5/9 (56%)** | 6/9 | 5/6 |
| Metacode | meta/avocado-code-flex-5p15 | **3/10 (30%)** | 6/10 | 3/6 |

- Turn 2 only runs after a Turn-1 pass, so Turn-2 denominators equal Turn-1 passes.
- One gpt trial is excluded from the denominator: it errored in harbor's **agent setup** (`apt-get install ripgrep` could not reach `deb.debian.org`), before the agent ran. Harbor classifies it as transient. This is agent tooling, not the task image — the task Dockerfile no longer uses apt at all.
- **Calibration read: correctly ordered and stably mixed.** opus 80% > gpt 56% > avocado 30%, no inversion, and avocado is comfortably inside the "not trivial, at least one solve" band rather than sitting on the 10/10 cliff. Turn 1 filters (avocado 6/10, gpt 6/9) and Turn 2 filters again (avocado 3/6).

#### Calibration history — what actually moved the numbers

| Commit | Change | Oracle | Opus | Avocado | Read |
|--------|--------|--------|------|---------|------|
| `ea0b7ef` | 59 tests, strict concurrency | 3/3 | 1/10 | 6/10 | **Inverted** — weak model beat strong model |
| `28beece` | Specified room schema / ops-log `op` / shared bucket; rate 1/s → 0.05/s in no-refill tests; agent timeout 1200→2400s | 2/3 | 5/7 | 3/7 | Inversion fixed, but Turn 2 stopped discriminating |
| `9c5c969` | Dropped apt from Dockerfile | 3/3 | — | — | Oracle BUILD_FAILED flake gone |
| `af0137b` | Added global-broadcast rate-limit tests; restored "at least 20 concurrent" wording to Turn 1 | 3/3 | 10/10 | 8/10 | **Too easy** — the "20 concurrent" sentence was the Turn-1 filter |
| `e5d715d` | Reverted Turn-1 concurrency wording to a functional requirement (no test parameters in the spec) | 3/3 | 8/10 | 3/10 | **Balanced** ✅ |

Three lessons from this sequence:

1. **The inversion was caused by unspecified requirements, not by difficulty.** Room-object key names (`users`/`messages`), the ops-log `op` field, and the shared `send`/`send-private` bucket were asserted by tests but absent from the spec. Writing them down fixed the ordering.
2. **A wall-clock-sensitive rate limit is a flake, not a discriminator.** At `rate=1/s, burst=2` with four CLI invocations between drain and assertion, any implementation slower than ~250 ms per invocation legitimately refills a token. Dropping to `0.05/s` removed the race; `rate=1/s` is now used only in the dedicated refill test.
3. **Putting test parameters in the spec removes the difficulty.** Stating "20 parallel sends… 20 concurrent joins" took Turn 1 from a real filter to 10/10 for every model. The spec now states the *requirement* ("must preserve every message and user under concurrent operations"); 20 stays a test parameter.

#### Where models fail now

- **Avocado (T1 6/10, T2 3/6)** — mixed: some trials never get single-file persistence + concurrency correct; those that do still collapse on `--config` sharded mode (weighted hashing, broadcast, presence, snapshot).
- **gpt-5.5 (T1 6/9)** — Turn 1 is the wall; when it clears Turn 1 it usually clears Turn 2 (5/6).
- **claude-opus-5 (T1 8/10, T2 8/8)** — only Turn 1 catches it, and Turn 2 is free once reached. This is the weakest part of the current calibration: at the frontier the task is a Turn-1 test.

Oracle proves all 120 tests (59 + 61) on all three online trials; verifier time 10–18s for Turn 1 and 24–33s for Turn 2, well inside the 600s verifier timeout. Reproduced locally in a 2-CPU / 4 GB container at this commit: Turn 1 56/56 in 9.1s, Turn 2 58/58 on three consecutive runs (23.5s / 23.1s / 23.2s) — the timing-sensitive rate-limit and presence-TTL tests are stable.

## Model Analysis

**Failure Categorization (hard 59+61):**

1. **Checksum + HTML Escaping (25%)**: Default Marshal escapes `<>&` → MD5 mismatch vs Python canonical. Fix `SetEscapeHTML(false)` + alphabetical field order + wrapper checksum for all files. Private special chars and private special chars sharded + Unicode emoji + large message 10KB test.

2. **Atomic + Locking + Concurrent (30%)**: WriteFile no lock → corruption under 20 parallel same room (all 20 required) and diff rooms all 20, concurrent joins 20 sorted, plus Turn2 strict 20 multi-shard all 20 + 20 joins all 20 sorted with unique global IDs, lock files cleaned (`.lock`, `global.lock`). Needs `CreateTemp`+`Rename` + `O_CREATE|O_EXCL` retry. The spec states the requirement, not the scale — 20 is a test parameter, which is what keeps this a discriminator.

3. **Spaces via Join + Large Message + Unicode (20%)**: Multiple args must be joined. Naive `args[2]` fails. Tests for both room and private Turn1 and sharded Turn2, plus 10KB large message, Unicode emoji, newlines/tabs, 500 private pagination.

4. **Global ID + Edge Validation (15%)**: Globally monotonic across room+private, next_id after corruption reset to 1, empty room/user ID exit2, missing message exit2, invalid limit exit2 (negative, non-int), limit zero returns all, nonexist [] and leave all [], 100/200 rooms sorted, concurrent joins 20, persistence across restarts 20 ops → next_id 41, room ID with dash/underscore/dot/colon, join after delete fails, send after leave fails, plus sharded nonexist [] and leave-all [].

5. **Weighted Sharding + Global Broadcast (5% Turn2)**: Simple hash%shard_count not weighted. 20 exact +100 tolerance (40% weight) +200 rooms sorted. Global rooms replicate same ID, dedup, distribution global*shard_count, multiple broadcast 5 msgs.

6. **Rate Limiting Refill Multiple Cycles + No Side Effects + Persistence (2.5% Turn2)**: In-memory resets, must persist, exit1 no ID/op-log, per-user independent, refill 1.6s and multiple cycles 1.2s, persistence format-agnostic (flat and buckets both accepted), corruption handling. Also: fractional `messages_per_second` (float64), one bucket shared across `send`/`send-private`, one token per `global:` broadcast, and byte-identical shards on a rate-limited broadcast.

7. **Presence TTL + Unknown + Multi-User + Pagination + Snapshot File Mode (2.5% Turn2)**: Unknown false last_seen0, TTL expiry 3s, multi-user TTL, offset pagination 50/500/1000 room+private <2s, snapshot dir all files+config + file mode combined JSON with counter exact restore, ops-log invalid skipping + content order + large 100, 200 rooms sharded, large message 10KB sharded, unicode.

**Cross-model**: Turn1 59 tests, Turn2 61 tests. Measured at `e5d715d`: opus-5 8/10, gpt-5.5 5/9, avocado 3/10, oracle 3/3 — monotonic in model strength with the weak-model gate mixed rather than saturated at either end.

**Reasoning gaps**: Spec details (checksum canonical, weighted hash, persistent token bucket with refill multiple cycles and no side effects, broadcast dedup and single-token cost, global lock, spaces Join, counter exact restore, empty ID and invalid limit validation, 200 rooms, concurrent joins, Unicode, 10KB, 1000-msg history), not flaky. The two known flake sources have been removed: wall-clock-sensitive rate limits (now 0.05/s except in the dedicated refill test) and apt-dependent image builds.

## Anti-Cheating Analysis

(a) **Hardcoded**: CLI invoking Go binary, file persistence checks via `json.load` + checksum, not source. Room names include zebra, alpha, middle, room0-19, room-000..199 (200 rooms), room-0000 (100), global:announce, global:multi (5 msgs), global:rate, global:atomic, global:shared, unknownToleranceRoom, defaultTest, room-0..99 (100), room-000..199 sharded (200 rooms), nonexist. Distribution computes expected via Python MD5 weighted exact for 20/100/200 rooms. Pagination expects bulk490-499 for 500, bulk990-999 for 1000 (Turn1), bulk500 for 1000, pbulk250 for 500 varying. Rate-limit tests check counter and ops_log side effects, per-user independence, refill and multiple cycles, and per-shard byte equality on a rejected broadcast – bucket storage shape is format-agnostic so no artificial format blocker remains.

(b) **Overfitting**: Hidden hard includes concurrent all 20 same+diff rooms+20 joins + concurrent mixed, spaces Join multiple args room+private both modes +10KB, global ID interleaving + next_id after corruption reset + persistence across many ops 20 room+private →41, 500/1000-msg Turn1 latest N +100/200-room sorted +1000-msg Turn2 offset +500 private offset perf <2s, seen_users persists after delete, lock cleanup both, private special chars <>& + Unicode emoji + newlines/tabs + large message 10KB + large message 10KB sharded + 200 rooms sharded + 20 multi-shard all 20 + 20 joins sharded + unicode emoji sharded + private special chars sharded, invalid limit exit2, empty IDs exit2, missing message exit2, nonexist [] and leave all [] and join after delete fails and send after leave fails and limit zero all (both single and sharded), rate-limit refill 1.6s + multiple cycles 1.2s + persistence format-agnostic + per-user independence + corruption handling private/rate_limit/presence, presence unknown + multi-user TTL expiry, weighted 50/100/200 rooms, global broadcast replication dedup same ID + multiple 5 msgs, distribution global*shard_count, checksum all files strict wrapper + after many ops, snapshot file mode combined JSON with counter exact restore + all files exact + ops_log, config validation exit2 for shard_count≤0 etc + unknown-field tolerance top and shard level + defaults, ops-log invalid skipping warning + content order + large 100, 200 rooms sharded, 20 multi-shard all 20, 20 joins sharded all 20. Overfitting to only general room fails.

(c) **Modifying test files**: Verifier isolated Docker, /tests separate; agent modifying /tests doesn't help; binary must satisfy file persistence and checksum and locking. `test.sh` derives `reward.txt` from `/logs/verifier/ctrf.json` (requires `summary.tests > 0`, `summary.passed == summary.tests`, `summary.failed + summary.other == 0`) rather than from a process exit status, and runs the parser under `python -I -S` so a `sitecustomize.py` / `atexit` shim cannot force a passing reward.

(d) **Bypassing intended path**: Go chat server with persistence, sharding, rate limiting, presence. Bypasses like Python script true would fail Go stdlib check (no dotted imports, go.mod no external) and behavioral concurrency all 20/20 same+diff+joins+multi-shard 20/20 preserved with unique IDs and no lock leftover, spaces Join +10KB+Unicode both modes +10KB sharded +1000 pagination +200 rooms, global ID monotonic + after corruption reset, checksum strict for all files, rate-limit exit1 no ID/op-log + refill + multiple cycles + persistence format-agnostic + per-user + corruption + single-token broadcast with byte-identical shards on rejection, presence TTL+unknown+multi-user, snapshot dir+file mode counter exact restore, weighted 20/100/200, global broadcast dedup multiple, config validation exit2, unknown tolerance, invalid args exit2. Source-string CreateTemp+Rename advisory only; behavioral atomicity reward-critical. Private isolation, pagination offset, snapshot all files cannot be bypassed.

All files use checksum integrity with no HTML escaping, requiring proper Go JSON encoder usage for all files.

## Submission Readiness

| Check | State at `e5d715d` |
|-------|--------------------|
| Validation status | **passing** |
| Structural | 10/10 PASS |
| Oracle | 3/3 |
| Balance gate | passed — avocado not trivial (3/10) and ≥1 agent solved (opus 8/10) |
| Agentic Full-Task Review | **GOOD / GENUINELY_HARD**, 13/13 rubrics PASS, secondary issues NONE |
| Provenance | clean |
| Contamination | not checked — repo not yet covered by the pipeline |
| Test counts | 56 Turn 1 + 58 Turn 2, matching the pytest files |
| Reward provenance | ctrf-derived, not exit-status-derived |

Optional follow-ups raised by the AFTR (none blocking): add the Turn-1 stdlib-only / `go.mod` dependency check to Turn 2 as well, and add explicit validation tests for an empty `shards` array, `shard id >= shard_count`, negative shard weights, and invalid rate-limit config.
