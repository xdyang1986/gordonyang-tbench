# codimango/message-queue

## Task Overview

Build, from scratch in Go (stdlib only, enforced via import check), a **Kafka-like partitioned message queue broker** at `/app`. Durable, crash-consistent with compaction, plus TRIM retention and PRODUCE_BATCH atomic.

Commands (space-separated, timestamps ≥0, negative timestamp = invalid input → exit non-zero):
- **Topic lifecycle:** `CREATE_TOPIC` (idempotent), `DELETE_TOPIC` (removes messages + group state for topic, but groups persist even when empty - intended, leniently accepts GC)
- **Producer:** `PRODUCE` → offset, `PRODUCE_AUTO` → partition+offset hashed `sum(bytes)%partitions`, `PRODUCE_BATCH` count 1..100 atomic (all partitions validated before any append, comma-separated offsets), logged as individual PRODUCEs
- **Retention:** `TRIM` sets low watermark, `FETCH`/`FETCH_RANGE` respect low (effective start = max(start,low)), `PARTITION_INFO` low/high, `TOPIC_INFO` total retained sum(high-low), `COMMIT`/`SEEK` must respect low
- **Consumer:** `FETCH`, `FETCH_RANGE`, `POLL` (auto-creates group **only on success**, subscribes, position init max(low, committed+1 else low), auto-advance over trimmed, advances 1), `COMMIT` (>=low or -1, only on success creates group), `SEEK` (>=low..high, only on success creates group), `JOIN_GROUP` (idempotent, only on success creates group), `GET_GROUP_OFFSET` (NONE if committed<low or -1), `LIST_GROUPS`
- **Maintenance:** `COMPACT` atomic rewrite to minimal deterministic record set: CREATE per topic sorted asc, PRODUCE all 0..high-1 per partition sorted asc offset asc, TRIM where low>0 sorted, JOIN per group sorted asc topic sorted, COMMIT where committed !=-1 and >=low and topic exists sorted, SEEK where pos != expected_default (max(low, committed+1) or low) sorted, all timestamp 0, deterministic sorted order, temp file + atomic rename. When POLL initializes a missing position and then advances it, durable mode must log only one SEEK for the final next position (explicitly stated to forbid alternate 2-SEEK strategy).
- **Error handling (clarified per latest review):** For `JOIN_GROUP`, `POLL`, `COMMIT`, `SEEK`, if topic/partition invalid or offset out of range → `ERROR` and **group is NOT created and no subscription happens**. Only on success does auto-create happen. This makes SEEK error behavior unambiguous (previously ambiguous: "Auto-creates group and subscribes" listed after error cases, vs COMMIT's "Otherwise sets… Auto-creates…" that tied creation to success).

Payloads: single tokens no spaces no commas 1..1024, topic/group names `[A-Za-z0-9._-]+` 1..255 not `.`/`..`. Invalid input → exit non-zero; app errors → `ERROR`.

## What makes this hard

- **Durable log with CRC framing:** `mq.log` records are `uint32 len + uint32 crc32 + payload`; recovery must stop at the first corrupt record, truncate the torn tail, and remain appendable afterward.
- **Atomic minimal compaction (fixed over-pinning per review):** `COMPACT` rewrites the log to the minimal deterministic record set per spec, sorted deterministically, all timestamps 0, via temp file + atomic rename, and result must be strictly smaller (`after_size < before_size`). Previously test asserted `len(before)==12` for live log, failing valid alternate strategy where POLL logs 2 SEEKs (init 0 + advance 1). Now fixed to allow extra non-noop live WAL records that replay to same final state, requiring only post-compact exact sequence and size shrink, while spec explicitly says POLL must log only one SEEK for final next position.
- **TRIM low/high semantics** cascading into `FETCH`/`FETCH_RANGE`/`POLL`/`COMMIT`/`SEEK`/`PARTITION_INFO`/`TOPIC_INFO` with auto-advance/clear on trim.
- **SEEK/COMMIT persistence and group lifecycle on error (Issue 1 fixed from latest review):** Previously SEEK spec listed error cases then said "Auto-creates group and subscribes" — ambiguous whether failed SEEK still registers group. Avocado's code registered group first then checked offset, so `SEEK g1 t0 2 4 2` (offset 4, high 1) created g1 then returned ERROR, causing LIST_GROUPS to show `g0,g1,g2` vs reference `g0,g2`. This was only failing test (fuzz 75/76) in trials kBqDgXE/Naf3cFM, real miss but spec-reading variance. Now spec explicitly states: on application error (invalid topic/partition or offset out of range) group is **NOT** created and no subscription happens; only on success does auto-create happen. Updated for JOIN_GROUP, POLL, COMMIT, SEEK consistently.
- **PRODUCE_BATCH atomicity** (all partitions validated before any append) and **PRODUCE_AUTO** normalized-logging as a plain `PRODUCE`.
- **No-op suppression** (repeated CREATE/JOIN/COMMIT/SEEK/TRIM must not append) and **sorted-order** guarantees in compaction output.
- **Strict input validation** (invalid names, negative timestamps → non-zero exit) and **stdlib-only** enforcement. Fsync durability is best-effort guideline, not hard correctness gate — informational test `test_fsync_best_effort` always passes and accepts `O_SYNC`/`O_DSYNC` (fixes previous contradiction where best-effort acted as hard gate).

## Test / Solution Details

- **76 tests** via `go build`:
  * basic, auto-hash (foo 324%3=0, bar 309%3=0, baz 317%3=2), fetch NONE beyond high/low, fetch_range with low handling, list sorted, topic/partition info low/high, create idempotent, delete, produce errors
  * consumer groups: join/poll (auto-create only on success), commit/get, -1 clear, seek (only on success creates group), poll after produce, multi-partition, list_groups, offset NONE, delete lenient GC, produce_auto poll, poll isolation
  * error handling: invalid topic/partition, commit/seek beyond high/low, trim beyond high, **failed SEEK/COMMIT must not create group** (fuzz checks LIST_GROUPS)
  * invalid input: unknown cmd, arity, bad ints, 0/>1000 partitions, bad topic/group names, payload comma, negative timestamps (5 cases), batch count 0/>100 and arity mismatch
  * blank lines, deterministic, stdlib-only (enforced), payload 1024 & topic 255 boundaries
  * durability: persist across restart, group committed/seek, auto-produce, torn-tail, bad CRC, truncate-then-append, compact preserves state/seek/trim, stray tmp, empty log clean, in-memory no persist
  * TRIM: basic, range, commit/seek error below low, poll auto-advance, poll after trim+produce, commit cleared on trim, persist low, many-messages 20 trim 15, offsets continue, delete+recreate resets low, large payload & 201-char topic & 1000 partitions, range low edge, compact preserves trim
  * BATCH: basic, same partition, atomic error (invalid partition → ERROR none appended), invalid input, persist, batch+trim
  * Fuzz: `test_fuzz_random` 20×100 random commands vs Python reference implementing identical low/high, committed, pos, auto-advance, clearing <low, and **no group creation on error** (the SEEK edge)
  * **R06/R07 (5 tests, fixed per review):** 
    - `test_compact_minimal_deterministic_and_smaller` now does NOT assert `len(before)==12`; allows extra non-noop live WAL records (e.g., 2 SEEKs for one POLL) and only checks post-compact exact minimal sequence (CREATE, 2 PRODUCE, TRIM, JOIN, COMMIT latest, SEEK differing from expected_default) + `after_size < before_size`. Spec now explicitly says POLL must log only one SEEK for final next position to forbid alternate strategy if desired.
    - `test_produce_auto_logged_as_normalized_produce` checks PRODUCE_AUTO logged as PRODUCE
    - `test_noop_does_not_append_records` checks 5 types of no-op don't grow file
    - `test_compaction_preserves_sorted_order` checks sorted order
    - `test_invalid_group_names` checks invalid group names exit non-zero
  * **Best-effort durability:** `test_fsync_best_effort` — informational only, always passes, accepts `Sync()` / `O_SYNC` / `O_DSYNC` / `fsync`, logs warning if missing. Spec says skipping fsync still passes functional tests, so this does NOT gate reward (fixes previous contradiction).

- **Reference solution:** Go with `Partition{msgs, low}`, `doTrim` clearing committed<low and advancing pos<low, `PRODUCE_BATCH` atomic validation then sequential produce, FETCH etc respect low, `COMMIT`/`SEEK`/`JOIN`/`POLL` return ERROR before creating group on invalid input (group NOT created on error), replay for TRIM, compact emits CREATE → PRODUCE all → TRIM low → JOIN → COMMIT >=low → SEEK != expected_default, `Sync()` in append/compact.

- **Environment:** `golang:1.26.2-bookworm`, WORKDIR /app, `allow_internet=true` (stdlib pre-bundled; third-party rejected via import check).

## Completion Rates

**Latest online validation after review fixes (commit `51b1c6c` + `b51d81c`, 76 tests, 2026-07-22):**

| Agent | Raw Dashboard | Completed Trials | Genuine Pass | Infra Errors | Notes |
|---|---|---|---|---|---|
| Oracle | 3/3 | 3 | 3/3 (1.0) | 0 | reference |
| Claude-code (Opus 4.8) | 4/5 in earlier, 5/5 in latest, 1 infra | 5 | 5/5 | 1 throttling | Too Many Requests counted as fail in some runs, but all clean runs passed |
| Codex (gpt-5.5) | 3/5 in earlier, 1/5 after TRIM, 4/5 in latest | 4 | 4/5? Actually 12/12 completed passed per reviewer | 1-2 infra | Mostly passes; harder after TRIM+BATCH but still 80% |
| Avocado (metacode) | 0/5 in dashboard, 2/5 in another, 1/10 with 1 pass 9 infra | 1 | 1/1 passed all 76 | 9 infra (Daytona throttling / EnvironmentStartTimeoutError) | Avocado's only real execution passed all tests including compaction; other attempts never ran verifier |

**Corrected analysis per latest review (Issue: README previously inaccurate):**

- **Previous README claimed:** Avocado 0/5 with genuine near-misses 73-74/75 on SEEK-0/compaction edge, and one trial `xaBeBHK` was "build/no-run" failure.
- **Actual trial artifacts (commit `732d919` and `51b1c6c`):** On current code (76 tests), **12 of 12 completed trials passed all tests — zero genuine test failures.** All other trials errored before tests ran: mostly `DaytonaAuthenticationError: ThrottlerException: Too Many Requests` and `EnvironmentStartTimeoutError: Environment start timed out after 600s` due to Daytona rate-limiting, plus one codex agent process exit.
- **Avocado:** Ran to completion once and **passed all 76 tests**; other 9 attempts were infra errors (throttling/timeout), not reasoning misses. So `0/5` is throttling counted as failure.
- **Trial `xaBeBHK`:** Not a build/no-run failure. Agent's final code built and ran correctly in its own checks; real cause was **time — ran 1256s against 1200s limit**, blown by single long stall early in run around first build, with no verifier output (scored 0 without recorded test run). So timeout with working deliverable, not model failure. README now corrected.

- **Remaining genuine discriminator (after fixing SEEK ambiguity):** `test_fuzz_random` only failing test (75/76) in trials `kBqDgXE` and `Naf3cFM` where Avocado returned `g0,g1,g2` vs reference `g0,g2` because it registered group `g1` from errored `SEEK g1 t0 2 4 2` (offset 4, high 1). Both frontier models passed, Avocado passed 2 of 5 runs. After making spec explicit that **failed SEEK must NOT create group**, this test now measures capability, not spec-reading. Reference does ERROR first, never creates group.

- **Local post-fix calibration (commit `3248edd` + `ef102bb` with TRIM+BATCH+fuzz, 76 tests):**
```
harbor run -p message-queue -a oracle → 1.000 (76 passed)
harbor run -p message-queue -a opencode -m anthropic/claude-sonnet-4 -k 3 → 0.000 (0/3) — TRIM+BATCH hard for weaker models
```

- **Difficulty:** After TRIM+BATCH+fuzz, codex dropped from 5/5 → 1/5 in one run, metacode 4/5, but latest run with infra throttling shows 0/5 dashboard due to throttling, not logic. Task is **on easy side for frontier models** (Opus, Codex often 5/5 when not throttled), with only fuzz group-lifecycle edge as discriminator for Avocado. Further hardening (idempotent dedup, transactions) could move off TOO_EASY, but current spec/tests are now unambiguous and accurate.

## Anti-Cheating Analysis

- No hardcoded outputs: fuzz 20×100 random commands covering TRIM, BATCH, invalid SEEK/COMMIT group-creation edge.
- Tests run binary as subprocess with fresh tmp dirs, check durability via file size, CRC, exact minimal post-compact sequence (not over-pinning live log len), sorted order, no-op suppression, invalid names, boundaries, batch atomicity, low handling, stdlib import check, fsync best-effort informational (always passes, accepts O_SYNC).
- Bypassing fails on per-partition offsets, sum-bytes hash, low handling (including group auto-advance and clearing <low), batch atomicity (must validate all partitions before any append), compaction minimal deterministic and strictly smaller, sorted order, CRC framing, and group lifecycle on error (failed SEEK must NOT create group).
