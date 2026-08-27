# codimango/chat-server

## Description

**Task Goal**: Production-grade Go chat server in two hard-balanced steps – oracle 100% with monotonic spread (at `adf4384`: avocado 0.8/0.33, opus 1.0/0.6, gpt 0.6/0.83) after tombstone under-spec + sticky hint removal.

**Turn 1 – Core (65 tests)**: CLI at `/app` module `chat-server` manages rooms, users, messages with durable split-file persistence and integrity.

Split persistence (mirrors sharded layout): three files in same dir as `--data`, each wrapper `{"data":<Data>,"checksum":md5 canonical}` canonical=`json.dumps(data, sort_keys=True, separators=(',',':'))` no HTML escape via `SetEscapeHTML(false)`:
- **chat.json**: `{"rooms":{roomID:{users:[]sorted,messages:[]sorted by id}}, "deleted_rooms":{roomID:{users:[],messages:[],deleted_at:int64 nano}}, "seen_users":{userID:bool}}`
- **private.json**: `{"private_messages":[]}`
- **counter.json**: `{"next_id":int64}` global monotonic across room+private, persists across restarts, not reset on delete/purge, only after corruption reset to 1.

Commands: `create-room` idempotent (empty ID exit2), `delete-room` tombstone – retains history: moves room's members+messages into `deleted_rooms` under roomID with `deleted_at` nano, behaves as though does not exist except `list-all-users` still reports ever-members, re-creating same ID starts empty and does NOT clear tombstone, `purge` is only way to remove tombstone (prints true/false, exit2 if no tombstone), `list-rooms` sorted omits deleted, `join`/`leave` idempotent (join fails exit2 if room not exist or deleted, leave all → [] and `send` after leave fails), `list-users` sorted exit2 if nonexist or deleted, `send` (member else exit2, message via `strings.Join` remaining args, missing message exit2, `<>&` no HTML escape raw file contains "<", Unicode emoji preserved, newlines/tabs, 10KB, cannot send to deleted room exit2), `get-messages` oldest first sorted by id asc limit latest N (0/omit=all), invalid limit exit2, limit zero all, nonexist or deleted → [] not error, `send-private`/`get-private` DM both directions limit latest N, `list-all-users` sorted unique ever seen including tombstone members.

IDs globally incrementing int64 starting at 1 unique across room+private monotonic interleaved (room1,priv1,room2,priv2→1,2,3,4). Help bare no args contains `create-room,delete-room,purge,list-rooms,join,leave,list-users,send,get-messages,send-private,get-private,data,checksum` exit0. Unknown command exit2.

Persistence integrity: wrapper checksum strict, atomic via `os.CreateTemp` same dir + `os.Rename` (deterministic inode replacement check, not truncate), file locking: global lock `/app/data/global.lock` mandatory for multi-file ops (send, send-private, delete-room, purge) – lock is acquired by creating it with O_CREATE|O_EXCL; if exists command retries and ultimately fails rather than proceeding. Per-file `.lock` also allowed. Locks cleaned after each op, no `tmp-*.json` residue after burst. Corruption handling: missing → empty data (chat `{"rooms":{},"deleted_rooms":{},"seen_users":{}}`, private `{"private_messages":[]}`, counter `{"next_id":1}`), empty file TrimSpace empty → empty data, wrapper missing/empty checksum or mismatch or invalid JSON → backup `<path>.corrupt.<nanosec>` integer UnixNano(), stderr warning "corrupt"/"checksum", recreate empty valid wrapper. `deleted_rooms` participates in checksum and corruption recovery like any other field.

Concurrency: file never invalid JSON during concurrent ops, parallel sends same room + different rooms + parallel joins must preserve every message and user with unique IDs. 30×2KB small-payload concurrency used for determinism.

**Turn 2 – Large Scale (67 tests)**: Extends binary to sharded mode via `--config /app/config.json` (inherits Turn1 split-file compatibility). When config exists and valid JSON, sharded active; else fallback to Turn1 split files.

Config validates (exit2): shard_count>0, shards non-empty array, each shard id >=0 and < shard_count unique, path non-empty, weight>0 default1, rate_limit messages_per_second positive float/int (negative, zero, non-numeric → exit2), burst positive int (<=0 or non-numeric → exit2), presence_ttl_seconds optional default60 must be >=0 (negative or non-numeric → exit2), invalid JSON → exit2, shard_count mismatch lenient, unknown fields ignored.

Sharded data files (all wrapper checksum, atomic CreateTemp+Rename, O_EXCL global lock, corruption backup, lock cleaned): each shard `{"rooms":{...},"deleted_rooms":{...},"seen_users":{...}}` with users,messages keys, private, presence `{userID:last_seen_nano}`, rate_limit flat or nested buckets behavioral verified, counter global, users global, assignments `{roomID:shardID}` with wrapper checksum atomic.

Sharded semantics: weighted consistent hashing MD5 big-endian, totalWeight sum, weighted_index=hashInt%totalWeight iterate sorted id subtract weight; `global:` prefix → -1 broadcast creates room in ALL shards replicated same ID, send replicates same message same ID to all shards, get-messages dedupes by ID sorted, get-shard-path returns comma-separated sorted list for global, distribution counts rooms per shard including global in each (1 normal+2 global*4=9).

Tombstone propagates to sharded: `delete-room` moves to `deleted_rooms` in its shard (or all shards if global) with deleted_at nano, prints true/false, behaves as though does not exist except list-all-users, re-create starts empty not clearing tombstone, `purge` removes tombstone and sticky assignment (exit2 if no tombstone).

Shard Assignment Stability (Sticky): room's shard decided once at creation and recorded in assignments.json, consulted for every op on existing rooms. If config weights change afterwards, existing rooms stay on original shard never rehashed or migrated. Only new rooms use new weights. Global rooms remain in all shards. Persisted mapping survives restarts and included in snapshots (dir and file modes copy config.json). This is the discriminator that moved opus off 1.0 in step2.

Rate limiting: per-user token bucket shared across send and send-private, persisted rate_limit.json wrapper, refill elapsed*rate cap burst, exit1 stderr "rate limit" no stdout no next_id increment no ops_log. Low rate 0.05/s in no-side-effect tests to avoid wall-clock race, 1/s only in refill test. Global broadcast costs one token not per shard and atomic – rate-limited global send leaves every shard byte-identical (no partial).

Presence, pagination, ops-log, snapshot/restore as before but now includes assignments and deleted_rooms. Presence includes last_seen_seconds_ago. Ops-log invalid line skipping with warning.

Lock protocol (CHAT-007): global.lock acquired via O_CREATE|O_EXCL, existence means held – test creates file with O_EXCL, mutating command must not succeed (fail or block). Implementation using flock alone fails spec fairly.

Stdlib only checked in both steps via `go list -f '{{join .Imports " "}}'` no dotted imports and go.mod no external require.

## Completion Rates

### Latest online validation — commit `adf4384` (65 + 66 tests before AFTR fixes, then 65+67)

**Validation status: PASSING.** Structural 10/10 PASS, oracle 3/3 locally 65/65 (9.1s) + 66/66 ×2 (29.2/28.8s), balance gate passed avocado 0.8/0.33, opus 1.0/0.6, gpt 0.6/0.83. AFTR at adf4384: BAD_GRADING_WRONG (test_global_lock_mandated flock vs O_EXCL), BAD_AMBIGUOUS (single-file wording), BAD_GRADING_WEAK (stdlib check missing in step2, config validation partial – empty shards, id>=shard_count, negative/non-numeric rate, invalid burst/TTL). Tombstone under-spec and sticky hint removal landed – avocado moved off 1.0 on step1 (0.8) and restored step2 (0.33).

| Stage | Agent / Model | Full multi-turn | Turn 1 | Turn 2 (given T1 pass) |
|-------|---------------|-----------------|--------|------------------------|
| Oracle | oracle | 3/3 (100%) | 3/3 | 3/3 |
| Agent | claude-code / claude-opus-5 | 6/10 | 10/10 | 6/10 |
| Codex | gpt-5.5 | 7/12 | 7/12? | 10/12? |
| Metacode | meta/avocado-code-flex-5p15 | 0.8/0.33 at adf4384 | 0.8 | 0.33 |

At HEAD with fixes (lock protocol spec, stdlib step2, expanded config validation 4 missing cases, single-file wording, README update) expected to stay green with same balance and pass AFTR.

Calibration history:

| Commit | Change | Oracle | Opus | Avocado | Read |
|--------|--------|--------|------|---------|------|
| `ea0b7ef` | 59 tests, strict concurrency | 3/3 | 1/10 | 6/10 | Inverted |
| `28beece` | room schema, ops-log op, shared bucket, rate 1→0.05, timeout 1200→2400 | 2/3 | 5/7 | 3/7 | Inversion fixed |
| `9c5c969` | drop apt Dockerfile | 3/3 | — | — | BUILD_FAILED flake gone |
| `af0137b` | global-broadcast rate tests, restore 20 concurrent wording | 3/3 | 10/10 | 8/10 | Too easy |
| `e5d715d` | revert concurrency to functional req | 3/3 | 8/10 | 3/10 | Balanced |
| `adf4384` | tombstone under-spec (1 invariant), remove sticky parenthetical | 3/3 | 1.0/0.6 | 0.8/0.33 | Saturation fixed |
| HEAD | lock protocol O_EXCL spec, stdlib step2, config validation empty shards/id>=count/rate/burst/TTL, wording split-file, README | 3/3 | — | — | AFTR fixes |

Lessons: inversion from unspecified keys (users/messages, op, shared bucket, deleted_rooms, assignments), wall-clock flake (0.05/s), test params in spec removes difficulty (20 concurrent → functional req), exhaustive enumeration → transcription not reasoning (tombstone 3 repeats → 1 inference-heavy), hint parenthetical → gives away sticky, lock protocol must be specified not assumed (flock vs O_EXCL).

#### Where models fail now
- Avocado: needs atomic inode rename + global O_EXCL lock cleanup + sticky assignment persistence + tombstone behavior inference + split-file checksum.
- Opus: now caught by sticky (1.0/0.6) – assignment must be recorded and never migrated.
- GPT: infra BUILD_FAILED previously counted as failure (ripgrep apt), now fixed multi-stage golang:1.24 + python:3.12.

## Model Analysis

**Failure Categorization (hard 65+67):**
1. Checksum+HTML (25%): SetEscapeHTML(false) + alphabetical + wrapper for all files including deleted_rooms, assignments.
2. Atomic+Locking+Concurrent (30%): CreateTemp+Rename inode check 30×2KB, global.lock O_CREATE|O_EXCL retry fail, no tmp residue, no lock leftover.
3. Spaces+Large+Unicode (15%): Join remaining args, 10KB, emoji.
4. Global ID+Tombstone+Edge (15%): monotonic, purge, deleted_rooms checksum+corruption, empty ID, invalid limit, limit zero, nonexist [], leave-all [], join after delete fails, send after leave fails, recreate keeps tombstone, purge exit2.
5. Weighted+Sticky+Global Broadcast (10% T2): hash%weight, sticky persistence via assignments.json survives reweight and snapshot, new rooms use new weights, global -1 broadcast dedup same ID, distribution global*shard_count, single token cost + atomic byte-identical shards on rate-limited.
6. Rate Limit+Presence+Pagination+Snapshot (5%): refill multiple cycles, no side effects (counter, ops_log), per-user independent, persistence format-agnostic, corruption handling, fractional rate float64, shared bucket send/send-private, presence TTL+unknown+multi, seconds_ago field, offset pagination <2s 1000/500, snapshot dir copies config+assignments+deleted_rooms, file mode combined JSON counter exact restore, ops-log invalid skipping + ts field + order + large 100, config validation (empty shards, id>=count, duplicate id, empty path, weight<=0, negative/non-numeric rate, burst<=0, TTL negative/non-numeric, invalid JSON, mismatch lenient) + unknown tolerance + defaults.

**Cross-model**: Turn1 65, Turn2 67 after AFTR fixes. Balanced 0.8/0.33 avocado, 1.0/0.6 opus at adf4384.

**Reasoning gaps**: canonical checksum with deleted_rooms, sticky recorded at creation never migrated, O_EXCL lock protocol retry fail, tombstone inference (does-not-exist except list-all-users), purge only way, global broadcast atomic single token, config validation extended.

## Anti-Cheating Analysis

(a) Hardcoded: CLI Go binary, file persistence json.load + checksum including deleted_rooms+assignments, distribution MD5 weighted exact 20/100/200, pagination bulk offsets, rate-limit counter+ops_log side effects+per-shard byte equality, bucket format-agnostic, tombstone verifies file content users+messages+deleted_at, sticky verifies ids unchanged after reweight + new rooms follow new weights.

(b) Overfitting: hidden hard includes 30×2KB concurrent same+diff+20 joins, spaces Join, global ID interleaving, tombstone moves to deleted_rooms with deleted_at, purge, recreate keeps tombstone, sticky reweight, lock O_EXCL exists⇒held, stdlib both steps, large message 10KB, Unicode, 1000-msg perf, 200 rooms, etc.

(c) Modifying tests: Docker isolated, ctrf-derived reward python -I -S.

(d) Bypassing: stdlib check both steps, concurrency atomic inode, O_EXCL lock, spaces Join, checksum all files including deleted_rooms+assignments, rate-limit single token atomic, sticky, tombstone.

## Submission Readiness

| Check | State at HEAD (after AFTR fixes) |
|-------|----------------------------------|
| Validation status | passing expected (adf4384 was passing) |
| Structural | 10/10 PASS |
| Oracle | 3/3 locally 65/65 + 67/67 |
| Balance gate | passed at adf4384 — avocado 0.8/0.33, opus 1.0/0.6, gpt 0.6/0.83 |
| Agentic Full-Task Review | fixes: lock protocol O_EXCL specified, stdlib step2 added, config validation empty shards/id>=count/negative rate/burst/TTL/non-numeric added, single-file wording fixed to split-file, README updated |
| Test counts | 65 Turn1 + 67 Turn2 |
| Reward provenance | ctrf-derived |
