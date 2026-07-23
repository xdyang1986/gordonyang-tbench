# codimango/message-queue

## Task Overview

Build, from scratch in Go (stdlib only, enforced via import check), a **Kafka-like partitioned message queue broker** at `/app`. Durable, crash-consistent with compaction, plus TRIM retention and PRODUCE_BATCH atomic.

Commands (space-separated, timestamps ≥0, negative timestamp = invalid input → exit non-zero):
- **Topic lifecycle:** `CREATE_TOPIC` (idempotent), `DELETE_TOPIC` (removes messages + group state for topic, but groups persist even when empty - intended, leniently accepts GC)
- **Producer:** `PRODUCE` → offset, `PRODUCE_AUTO` → partition+offset hashed `sum(bytes)%partitions`, `PRODUCE_BATCH` count 1..100 atomic (all partitions validated before any append, comma-separated offsets), logged as individual PRODUCEs
- **Retention:** `TRIM` sets low watermark, `FETCH`/`FETCH_RANGE` respect low (effective start = max(start,low)), `PARTITION_INFO` low/high, `TOPIC_INFO` total retained sum(high-low), `COMMIT`/`SEEK` must respect low
- **Consumer:** `FETCH`, `FETCH_RANGE`, `POLL` (auto-creates group, subscribes, position init max(low, committed+1 else low), auto-advance over trimmed, advances 1), `COMMIT` (>=low or -1), `SEEK` (>=low..high), `JOIN_GROUP` (idempotent), `GET_GROUP_OFFSET` (NONE if committed<low or -1), `LIST_GROUPS`
- **Maintenance:** `COMPACT` atomic rewrite to minimal deterministic record set: CREATE per topic sorted asc, PRODUCE all 0..high-1 per partition sorted asc offset asc, TRIM where low>0 sorted, JOIN per group sorted asc topic sorted, COMMIT where committed !=-1 and >=low and topic exists sorted, SEEK where pos != expected_default (max(low, committed+1) or low) sorted, all timestamp 0, deterministic sorted order, temp file + atomic rename. When POLL initializes a missing position and then advances it, durable mode must log only one SEEK for the final next position.

Payloads: single tokens no spaces no commas 1..1024, topic/group names `[A-Za-z0-9._-]+` 1..255 not `.`/`..`. Invalid input → exit non-zero; app errors → `ERROR`.

## What makes this hard

- **Durable log with CRC framing:** `mq.log` records are `uint32 len + uint32 crc32 + payload`; recovery must stop at the first corrupt record, truncate the torn tail, and remain appendable afterward.
- **Atomic minimal compaction:** `COMPACT` rewrites the log to the minimal deterministic record set per spec, sorted deterministically, all timestamps 0, via temp file + atomic rename, and result must be strictly smaller (`after_size < before_size`). Previously over-pinned live log to exactly 12 records, failing valid alternate strategies that log an extra SEEK for initialization; now fixed to allow extra non-noop live WAL records that replay to same final state, requiring only post-compact exact sequence and size shrink.
- **TRIM low/high semantics** cascading into `FETCH`/`FETCH_RANGE`/`POLL`/`COMMIT`/`SEEK`/`PARTITION_INFO`/`TOPIC_INFO` with auto-advance/clear on trim.
- **SEEK/COMMIT persistence** across restart and compaction — including edge where SEEK targets offset 0 (collides with default). Reference logs only one SEEK per POLL for final next position (explicitly stated in spec).
- **PRODUCE_BATCH atomicity** (all partitions validated before any append) and **PRODUCE_AUTO** normalized-logging as a plain `PRODUCE`.
- **No-op suppression** (repeated CREATE/JOIN/COMMIT/SEEK/TRIM must not append) and **sorted-order** guarantees in compaction output.
- **Strict input validation** (invalid names, negative timestamps → non-zero exit) and **stdlib-only** enforcement. Fsync durability is best-effort guideline, not a hard correctness gate (see below), checked via informational test that always passes and accepts `O_SYNC`.

## Test / Solution Details

- **76 tests** via `go build`:
  * basic, auto-hash (foo 324%3=0, bar 309%3=0, baz 317%3=2), fetch NONE beyond high/low, fetch_range with low handling, list sorted, topic/partition info low/high, create idempotent, delete, produce errors
  * consumer groups: join/poll, auto-create, commit/get, -1 clear, seek, poll after produce, multi-partition, list_groups, offset NONE, delete lenient GC, produce_auto poll, poll isolation
  * error handling: invalid topic/partition, commit/seek beyond high/low, trim beyond high
  * invalid input: unknown cmd, arity, bad ints, 0/>1000 partitions, bad topic/group names, payload comma, negative timestamps (5 cases: CREATE, PRODUCE, FETCH, LIST_TOPICS, COMPACT), batch count 0/>100 and arity mismatch
  * blank lines, deterministic, stdlib-only (enforced), payload 1024 & topic 255 boundaries
  * durability: persist across restart, group committed/seek, auto-produce, torn-tail, bad CRC, truncate-then-append, compact preserves state/seek/trim, stray tmp, empty log clean, in-memory no persist
  * TRIM: basic, range with low, commit/seek error below low, poll auto-advance, poll after trim+produce, commit cleared on trim, persist low, many-messages (20 trim 15), offsets continue, delete+recreate resets low, large payload & 201-char topic & 1000 partitions, range low edge, compact preserves trim
  * BATCH: basic, same partition, atomic error (invalid partition → ERROR none appended), invalid input, persist, batch+trim
  * Fuzz: `test_fuzz_random` 20×100 random commands vs Python reference implementing identical low/high, committed, pos, auto-advance, clearing <low
  * **R06/R07 (5 tests, fixed per review):** 
    - `test_compact_minimal_deterministic_and_smaller` now does NOT assert `len(before)==12`; allows extra non-noop live WAL records (e.g., 2 SEEKs for one POLL) and only checks post-compact exact minimal sequence + `after_size < before_size` + that before replays to same final state. Spec now explicitly says POLL must log only one SEEK for final next position.
    - `test_produce_auto_logged_as_normalized_produce` checks PRODUCE_AUTO logged as PRODUCE not PRODUCE_AUTO
    - `test_noop_does_not_append_records` checks 5 types of no-op don't grow file
    - `test_compaction_preserves_sorted_order` checks sorted order of CREATE/PRODUCE/TRIM/JOIN/COMMIT/SEEK
    - `test_invalid_group_names` checks 5 commands with invalid group names exit non-zero
  * **Best-effort durability:** `test_fsync_best_effort` — informational only, always passes, accepts `Sync()` or `O_SYNC`/`O_DSYNC`/`fsync`, logs warning if missing. Spec says skipping fsync still passes functional tests, so this does NOT gate reward (fixes previous contradiction where best-effort acted as hard gate).
  * Total 76.

- **Reference solution:** Go with `Partition{msgs, low}`, `doTrim` clearing committed<low and advancing pos<low, `PRODUCE_BATCH` atomic validation then sequential produce, FETCH etc respect low, replay for TRIM, compact emits CREATE → PRODUCE all → TRIM low → JOIN → COMMIT >=low → SEEK != expected_default, `Sync()` in append/compact, atomic rename.

- **Environment:** `golang:1.26.2-bookworm`, WORKDIR /app, `allow_internet=true` (Go stdlib pre-bundled, no pip/apt needed; third-party rejected via import check).

## Completion Rates (online validation — commit 6d13bc5, 76 tests, 2026-07-23)

- Oracle: **3/3** — validated
- Opus 4.8 (agent): **5/5** — failed (solved every trial → too easy for this model)
- GPT-5.5 (codex): **5/5** — failed (too easy for this model)
- Avocado (metacode): **2/5** — validated
- avgReward **0.80**, validation passing.

## Failure Analysis (latest run)

Derived from downloaded trial CTRF artifacts. This run's only genuine completed failures came from Avocado; both frontier models solved every trial.

- **Opus 4.8 (agent) — 5/5.** All clean passes; too easy for this model.
- **GPT-5.5 (codex) — 5/5.** All clean passes; too easy for this model.
- **Avocado (metacode) — 2/5, one real reasoning edge.** 2 completed trials passed; 2 genuine failures were on `test_fuzz_random` only (75/76), and 1 trial produced no test output (build/no-run, reward 0). The fuzz failure is a real divergence from the Python reference on `LIST_GROUPS`: Avocado returned `g0,g1,g2` where the reference returns `g0,g2` — it registered group `g1` that was only touched by an **errored** `SEEK g1 t0 2 4 2`, whereas the reference does not create a consumer group from an invalid operation. Group-lifecycle edge: an errored SEEK must not leave the group registered.
- **Oracle — 3/3.** Reference solution passes.

**Assessment:** the task is on the easy side this run — both frontier models are 5/5. The only genuine discriminator is `test_fuzz_random`, which catches Avocado's group-lifecycle bug (errored consumer ops must not auto-create the group). To move off TOO_EASY for the frontier models, the task needs further hardening (e.g. idempotent-producer dedup or transactional semantics).

## Anti-Cheating Analysis

- No hardcoded outputs: arbitrary random stdin (fuzz) plus TRIM retention and BATCH atomicity.
- Tests run binary as subprocess with fresh tmp dirs, check durability via file size, CRC, exact minimal post-compact sequence, sorted order, no-op suppression, invalid names, boundaries, batch atomicity, low handling, stdlib import check (no dot), fsync best-effort informational (does not gate reward, accepts O_SYNC).
- Bypassing fails on offsets per partition, sum-bytes hash, low handling, batch atomicity, compaction minimal deterministic and strictly smaller, sorted order, CRC framing.
