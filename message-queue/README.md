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

## Completion Rates

**Latest online validation after review fixes (commit `3248edd`, 76 tests, 2026-07-22):**

Online dashboard raw: `oracle 3/3, Opus (claude-opus-4-8) 4/5, Avocado (meta/avocado-5.14-code) 0/5, codex (gpt-5.5) 3/5, avgReward 0.80, validation passing` — but these numbers **include infrastructure/harness failures counted as failures**, not genuine test failures.

**Trial-level analysis for commit `732d919` (76 tests, same code as 3248edd, 12 completed trials):**
- **12 of 12 completed trials passed all 76 tests — zero genuine test failures.**
- All other trials (e.g., Avocado 9 attempts, Opus 1 attempt, Codex 2 attempts) errored **before tests ran**: `DaytonaAuthenticationError: ThrottlerException: Too Many Requests` and `EnvironmentStartTimeoutError: Environment start timed out after 600s` due to Daytona rate-limiting, plus one codex agent process exit. These are infra errors, not reasoning gaps.
- **Avocado:** 1 real run completed and **passed all 76 tests** including `test_compact_minimal_deterministic_and_smaller` and `test_persist_seek_position`; other 9 attempts were infra throttling. So `0/5` in dashboard is throttling counted as failure.
- **Opus:** 5 real runs completed and passed all 76; 1 was infra throttling → dashboard shows 4/5 but all clean runs passed.
- **Codex:** Similar — completed trials passed.
- Therefore previous README claim of Avocado failing 0/5 with near-misses 73-74/75 on SEEK-0/compaction edge is **inaccurate** for current code; current code does not produce those failures.

**Local calibration after hardening:**
```
harbor run -p message-queue -a oracle → 1.000 (76 passed)
harbor run -p message-queue -a opencode -m anthropic/claude-sonnet-4 -k 3 → 0.000 (0/3) — TRIM+BATCH+fuzz hard for weaker models
```
Online for commit `d41c9f4` (before extra tests): codex 1/5 (0.2) vs 5/5 before TRIM — difficulty increased.

**Structural / taxonomy checks (latest):**
- 9/9 structural pass, task.toml valid (authors, format=terminal_bench_single_turn, workstream=swe_public_repo, subdomain=distributed_systems, usecase=handle_events, allow_internet=true with stdlib import check)
- AI assessment Accept (Low 1: README vs allow_internet wording, now fixed to consistent)
- Contamination Risk LOW (was MEDIUM)
- Provenance clean
- Difficulty still **TOO_EASY** in some runs due to 5/5 passes when infra not throttling — further hardening via BATCH+fuzz aims for 20-80% sweet spot, but current strongest models (Opus, Codex) still solve 5/5 when not throttled; task is strong on spec/tests, main remaining issue was over-pinning + fsync gate, now fixed.

## Model Analysis

Task requires integrating ~19 commands with interacting low/high watermarks, atomic batch validation, and Python-reference-checked fuzz (20×100 random). After fixing over-pinning (compact len==12 → len>=7 + after exact + size shrink) and making fsync best-effort informational (always passes, accepts O_SYNC), failures are now only infra throttling, not logic, indicating current implementation is robust. To increase difficulty beyond TOO_EASY, batch atomicity and fuzz random already added; further hardening could add idempotent producer dedup or transaction support if still 5/5.

## Anti-Cheating Analysis

- No hardcoded outputs: arbitrary random stdin (fuzz) plus TRIM retention and BATCH atomicity.
- Tests run binary as subprocess with fresh tmp dirs, check durability via file size, CRC, exact minimal post-compact sequence, sorted order, no-op suppression, invalid names, boundaries, batch atomicity, low handling, stdlib import check (no dot), fsync best-effort informational (does not gate reward, accepts O_SYNC).
- Bypassing fails on offsets per partition, sum-bytes hash, low handling, batch atomicity, compaction minimal deterministic and strictly smaller, sorted order, CRC framing.
