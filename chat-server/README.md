# codimango/chat-server

## Description

**Task Goal**: Production-grade Go chat server in two hard-balanced steps – oracle 100% with per-step balance gate passing (at `adf4384`: avocado 0.8/0.33, opus 1.0/0.6, gpt 0.6/0.83) but zero end-to-end for frontier model due to Turn1→Turn2 code extension, not per-step difficulty.

**Turn 1 – Core (65 tests)**: CLI at `/app` module `chat-server` manages rooms, users, messages with durable split-file persistence and integrity.

Split persistence (mirrors sharded layout): three files in same dir as `--data`, each wrapper `{"data":<Data>,"checksum":md5 canonical}` canonical=`json.dumps(data, sort_keys=True, separators=(',',':'))` no HTML escape via `SetEscapeHTML(false)`:
- **chat.json**: `{"rooms":{roomID:{users:[]sorted,messages:[]sorted by id}}, "deleted_rooms":{roomID:{users:[],messages:[],deleted_at:int64 nano}}, "seen_users":{userID:bool}}`
- **private.json**: `{"private_messages":[]}`
- **counter.json**: `{"next_id":int64}` global monotonic across room+private, persists across restarts, not reset on delete/purge, only after corruption reset to 1.

Commands: `create-room` idempotent (empty ID exit2), `delete-room` tombstone – retains history: moves room's members+messages into `deleted_rooms` under roomID with `deleted_at` nano, behaves as though does not exist except `list-all-users` still reports ever-members, re-creating same ID starts empty and does NOT clear tombstone, `purge` is only way to remove tombstone (prints true/false, exit2 if no tombstone), `list-rooms` sorted omits deleted, `join`/`leave` idempotent, `list-users` sorted exit2 if nonexist/deleted, `send` via Join, `get-messages` latest N, `send-private`/`get-private`, `list-all-users` includes tombstone.

Persistence integrity: atomic CreateTemp same dir + Rename inode check (deterministic, not torn JSON), file locking via global.lock `/app/data/global.lock` in data dir – acquired by creating file with O_CREATE|O_EXCL; if exists retries and ultimately fails rather than proceeding (flock alone invalid per spec), per-file .lock allowed, locks cleaned, no tmp residue, corruption backup `.corrupt.<nanosec>` + stderr warning, wrapper checksum strict, deleted_rooms participates.

Concurrency: 30×2KB small-payload (not 20KB) to avoid Daytona 50× slower flake, file never invalid JSON, unique IDs preserved.

**Turn 2 – Large Scale (68 tests)**: Extends binary to sharded mode via `--config /app/config.json` (inherits Turn1 split-file compatibility).

Flags:
- `--data` default `/app/data/chat.json` – Turn1 compatibility mode, used when --config is not supplied. Persistence keeps Turn1 split-file layout: chat.json (rooms+deleted_rooms+seen_users), private.json, counter.json.
- `--config` default `/app/config.json` – sharded mode. If --config is supplied, file must exist and contain valid JSON that passes validation; missing file, malformed JSON, or validation failure exits 2. No silent fallback when --config given. If --config not supplied and default config missing, fallback to Turn1 split-file mode.

Config validates exit2: shard_count>0, shards non-empty array, id >=0 and < shard_count unique, path non-empty, weight>0 default1, rate_limit messages_per_second positive float (negative/zero/non-numeric → exit2), burst positive int (<=0/non-numeric → exit2), presence_ttl_seconds optional default60 must be non-negative number (negative/non-numeric → exit2), invalid JSON → exit2, count mismatch lenient, unknown fields ignored.

Sharded data files: each shard `{"rooms":{...},"deleted_rooms":{...},"seen_users":{...}}`, private, presence, rate_limit, counter global, users global, assignments `{roomID:shardID}` wrapper checksum atomic O_EXCL global lock.

Sharded semantics: weighted MD5 big-endian hashed, global: → -1 broadcast all shards same ID dedup, distribution includes global*shard_count, tombstone propagates to shards (or all shards if global) with deleted_at, purge removes tombstone+assignment.

Sticky assignment: room shard decided once at creation recorded in assignments.json, never migrated on reweight, new rooms use new weights, global stays -1, survives restarts and snapshot (dir copies config.json). Discriminator.

Rate limiting 0.05/s low rate to avoid wall-clock race, shared bucket send+send-private, global one token atomic byte-identical on reject.

Presence includes last_seen_seconds_ago, TTL testable, stdlib checked both steps.

## Completion Rates

### Latest — HEAD after AFTR fixes (65 + 68 tests)

Validation passing at `adf4384` (65+66) with balance gate passed avocado 0.8/0.33 opus 1.0/0.6. AFTR at adf4384 flagged BAD_GOLDEN (step2 --data mode wrote single file ['deleted_rooms','next_id','private_messages','rooms','seen_users'] not split), BAD_GRADING_WRONG (test_global_lock_mandated assumed file exists ⇒ held, but flock implementation valid), BAD_GRADING_WEAK (stdlib step2 missing, config validation partial), BAD_AMBIGUOUS (single-file wording for split layout).

Fixes in this commit:
- Instruction: --data is Turn1 compat used when --config not supplied, split layout; --config supplied must exist+valid JSON, missing/malformed/validation → exit2 no fallback; delete fallback prose.
- TTL rule: presence_ttl_seconds optional default 60, if present must be non-negative number, negative/non-numeric → exit2 (golden accepts 0).
- Golden: step2 reference in --data mode now honours split-file (chat.json deleted_rooms+seen_users+rooms, private.json, counter.json) via withGL closure using acquireLock in data dir, not single StoreData.
- Tests: added Turn1 split-file mode assertions (private.json+counter.json exist with valid wrapper checksums, chat.json must NOT contain private_messages/next_id, deleted_rooms key present) + config missing file explicit exits 2 + empty shards, id>=shard_count, negative/zero/non-numeric rate, burst<=0/non-numeric, TTL negative/non-numeric.
- Cosmetic: rename "single mode"/"single-file mode"/"hi from single mode" to "Turn1 split-file mode" in 8 places.

Oracle locally: Turn1 65/65 (9.1s), Turn2 68/68 (45s) after fixes.

Balance gate at adf4384: avocado 0.8/0.33, opus 1.0/0.6, gpt 0.6/0.83 – saturation fixed by tombstone under-spec (1 invariant) + sticky hint removal.

End-to-end vs per-step: Per-step gate passed, but full multi-turn at adf4384 was 0/10 for opus, 2/10 avocado, 2/11 gpt. Per-step runs step2 on seeded reference; multi-turn agent extends its own step-1 code, so frontier model finishing zero times is expected when step1 is hard enough to filter. This does not block – gate is per-step – but worth a sentence explaining.

### Calibration history

| Commit | Change | Oracle | Opus | Avocado | Read |
|--------|--------|--------|------|---------|------|
| ea0b7ef | 59 tests, strict concurrency | 3/3 | 1/10 | 6/10 | Inverted |
| 28beece | room schema, ops-log op, shared bucket, rate 0.05, timeout 2400 | 2/3 | 5/7 | 3/7 | Fix inversion |
| 9c5c969 | drop apt Dockerfile | 3/3 | — | — | Flake gone |
| af0137b | global broadcast rate-limit | 3/3 | 10/10 | 8/10 | Too easy |
| e5d715d | revert concurrency to functional req | 3/3 | 8/10 | 3/10 | Balanced |
| adf4384 | tombstone 1 invariant, remove sticky parenthetical | 3/3 | 1.0/0.6 | 0.8/0.33 | Saturation fixed |
| 7964fc0 | lock O_EXCL spec, stdlib step2, config validation gaps, README | 3/3 | — | — | AFTR fixes |

## Model Analysis

Failure categorization (65+68):
- Checksum+HTML: SetEscapeHTML(false)+canonical MD5 including deleted_rooms+assignments.
- Atomic+Lock O_EXCL: CreateTemp+Rename inode replacement, global.lock O_CREATE|O_EXCL retry fail, lock cleaned, tmp residue, 30×2KB.
- Spaces+Large+Unicode: Join, 10KB, emoji.
- Global ID+Tombstone: monotonic, purge, deleted_rooms checksum+corruption, recreate keeps tombstone, list-all-users includes tombstone.
- Weighted+Sticky+Broadcast: MD5 weighted, sticky persistence via assignments.json survives reweight+snapshot, global -1 same ID dedup, single token + byte-identical atomic.
- Rate+Presence+Pagination+Snapshot+Config: refill multiple cycles, no side effects, per-user, format-agnostic, fractional float64, shared bucket, presence TTL+seconds_ago, offset pagination, snapshot dir copies config+assignments+deleted_rooms, counter exact restore, config validation (empty shards, id>=count, duplicate, empty path, weight<=0, rate negative/zero/non-numeric, burst<=0/non-numeric, TTL negative/non-numeric, invalid JSON, mismatch lenient) + unknown tolerance.

Cross-model: Turn1 65, Turn2 68. Per-step balanced, multi-turn 0/10 opus expected due to Turn1 filter extending own code.

## Anti-Cheating

Hardcoded: CLI Go binary, file persistence json.load+checksum including deleted_rooms+assignments, distribution MD5 weighted, pagination offsets, rate-limit counter+ops_log side effects+byte equality, tombstone file content, sticky ids unchanged after reweight.

Overfitting: concurrency 30×2KB same+diff+20 joins sorted unique IDs, spaces Join both modes, global ID, tombstone, purge, sticky reweight, lock O_EXCL, stdlib both steps, etc.

Reward provenance ctrf-derived python -I -S.

## Submission Readiness

| Check | State |
|-------|-------|
| Validation | passing at adf4384, fixes make it pass AFTR (BAD_GOLDEN, BAD_GRADING_WRONG, BAD_GRADING_WEAK, BAD_AMBIGUOUS) |
| Structural | 10/10 |
| Oracle | 3/3 locally 65+68 |
| Balance gate | passed 0.8/0.33 avocado, 1.0/0.6 opus |
| AFTR | fixed: lock protocol O_EXCL, stdlib step2, config validation empty shards/id>=count/rate/burst/TTL, single-file wording → split-file, README, split-file test |
| Test counts | 65 + 68 |
| Note | per-step vs multi-turn: step2 runs on seeded ref per-step, multi-turn extends own step1 code → opus 0/10 end-to-end is expected, not per-step difficulty |
