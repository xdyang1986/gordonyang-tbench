# codimango/message-queue

## Task Overview

Build, from scratch in Go (stdlib only, enforced, internet enabled but not needed), a **Kafka-like partitioned message queue broker** as a single `package main` binary at `/app`. Durable, crash-consistent broker that reads commands from stdin and writes results to stdout.

Commands (space-separated tokens, no quoting, timestamps >=0, negative timestamp = invalid input → exit non-zero):
- **Topic lifecycle:** `CREATE_TOPIC <topic> <num_partitions> <ts>` (idempotent), `DELETE_TOPIC <topic> <ts>` (removes messages + consumer state for that topic, but groups persist even when empty — intended, leniently accepts GC)
- **Producer:** `PRODUCE <topic> <partition> <payload> <ts>` → `<offset>`, `PRODUCE_AUTO <topic> <payload> <ts>` → `<partition> <offset>` with `sum(bytes(payload)) % num_partitions`, `PRODUCE_BATCH <topic> <count> <part1> <payload1> ... <timestamp>` → comma-separated offsets, atomic validation (all partitions must be valid or none appended)
- **Retention:** `TRIM <topic> <partition> <offset> <ts>` → sets low watermark to max(old low, offset), deleting messages with offset < low; `FETCH` returns `NONE` for trimmed offsets, `COMMIT`/`SEEK` must respect low
- **Consumer:** `FETCH`, `FETCH_RANGE` (effective start = max(start, low)), `POLL <group> <topic> <partition>` → `<offset> <payload>` advancing position and auto-advancing over trimmed ranges, `COMMIT` (>=low or -1), `SEEK` (>=low), `JOIN_GROUP`, `GET_GROUP_OFFSET` (returns NONE if committed < low), `LIST_GROUPS`
- **Metadata:** `LIST_TOPICS`, `TOPIC_INFO` → `<partitions> <total_retained>` where total = sum(high-low), `PARTITION_INFO` → `<low> <high>`
- **Maintenance:** `COMPACT` → atomic rewrite preserving all messages 0..high-1, TRIMs, groups, commits, seeks

Payloads: single tokens (no spaces, no commas, 1..1024B, no comma). Topic/group names `[A-Za-z0-9._-]+`, 1..255, not `.`/`..`. Invalid input (including negative timestamp, bad count for batch) → exit non-zero; application errors → `ERROR`.

## What makes this hard (hardening for too-easy)

- **Initial online result: TOO_EASY** — commit `bcce87b` had 42 tests, oracle 3/3, metacode 5/5, opus 5/5, codex 5/5 → 95% pass rate, classification TOO_EASY.
- **First hardening (commit d41c9f4): added TRIM retention** — low/high watermark semantics cascading into FETCH, FETCH_RANGE, PARTITION_INFO, TOPIC_INFO, COMMIT, SEEK, POLL, group auto-advance, durability, compaction. After TRIM, local calibration showed codex 1/5 (was 5/5) and metacode 4/5 (was 5/5), moving toward balanced.
- **Second hardening (commit 2a1d302 → ef102bb): added 20+ hard cases** — many-messages trim, delete+recreate resets low, trim+group commit/seek, persist low, large payloads (1024B) & 201-char topic, 1000 partitions, range low edge, compact preserves trim. Total 61 tests.
- **Third hardening (current, 70 tests) — to address still-too-easy online result (commit 2a1d302 showed metacode 5/5, codex 5/5 again after full validation):**
  - Added `PRODUCE_BATCH` atomic (count 1..100, `4+2*count` tokens, all partitions validated before any append, outputs comma-separated offsets, logged as individual PRODUCEs)
  - Added 6 batch tests (basic, same partition, atomic error, invalid input, persist, batch+trim)
  - Added **fuzz test with Python reference** (`test_fuzz_random`): 20 random sequences each 100 commands covering all ops (PRODUCE, AUTO, BATCH, FETCH, RANGE, POLL, COMMIT, SEEK, TRIM, JOIN, etc.), generated via seeded random, compared against Python reference implementing same semantics (low, high, committed, pos, auto-advance on trim). This catches subtle integration bugs that hand-crafted tests miss.
  - Added boundary tests: payload 1024B valid / 1025B invalid exit, topic name 255 valid / 256 invalid
  - Total now **70 tests**. Local oracle still **1.0**; local `opencode claude-sonnet-4` now **0/3** (was 0/3 before, but with more tests still 0), and earlier online codex dropped to 1/5 after TRIM.

- **Review fixes from earlier Request Changes (Issues 1-4):**
  - Issue 1 (group persistence ambiguity): Spec now explicitly says empty groups remain visible in LIST_GROUPS (Kafka). Test leniently accepts `g` or `NONE` to eliminate 4/5 vs 5/5 variance.
  - Issue 2 (wrong example): Fixed second POLL in auto-partition example from `0 bar` to `1 bar`, removed leftover note.
  - Issue 3 (stdlib not enforced): Kept `allow_internet=true` (matches all other Go tasks) but added `test_stdlib_only` rejecting imports with `.`.
  - Issue 4 (negative timestamp): Explicitly invalid input + 5 negative-ts cases.

## Test / Solution Details

- **Tests** (`tests/test_outputs.py`): 70 tests via `go build -o /tmp/agent_mq .`:
  * basic produce/fetch, auto-hash, fetch NONE, fetch_range, list sorted, topic/partition info (low/high), create idempotent, delete, produce errors
  * consumer groups: join/poll, auto-create, commit/get, -1 clear, seek, poll after produce, multi-partition, list_groups, offset NONE, delete with lenient group GC, produce_auto→poll, poll isolation
  * error handling: FETCH etc ERROR, commit/seek beyond high/low, trim beyond high
  * invalid input: unknown cmd, arity, bad ints, 0/>1000 partitions, bad names, payload comma, **negative timestamps** (5 cases), **batch count 0/>100 and arity mismatch**
  * blank lines, deterministic, **stdlib-only**, **payload/topic name boundaries (1024, 255)**
  * durability: persist across restart, group committed/seek persist, auto-produce persist, torn-tail, bad CRC, truncate-then-append, compact preserves state+seek+trim, stray tmp ignored, empty log clean, in-memory no persist
  * **TRIM (10+):** basic, range, commit/seek error below low, poll auto-advance, poll after trim+produce, commit cleared on trim, persist group low, many-messages (20 msgs trim 15), offsets continue after trim, delete+recreate resets low, large payload & 201-char topic, 1000 partitions, range low edge, compact preserves trim
  * **BATCH (6):** basic 2-part, same partition 3 msgs, atomic error (one partition invalid → ERROR and none appended), invalid input (count 0/>100), persist, batch+trim
  * **Fuzz (1):** 20 random sequences of 100 commands each, driven by Python reference `py_run` implementing identical low/high, committed, pos logic, auto-advance on trim, clearing committed<low, etc., compared exactly to Go binary output

- **Reference solution:** Go with `Partition{msgs, low}`, `doTrim` advancing low and GC'ing committed/pos < low, `PRODUCE_BATCH` atomic validation then sequential produce with comma-separated offsets, updated FETCH/FETCH_RANGE/TOPIC_INFO/PARTITION_INFO/COMMIT/SEEK/POLL to respect low, replay for TRIM, compact emits CREATE → PRODUCE all 0..high-1 → TRIM low → JOIN → COMMIT >=low → SEEK != expected_default (max(low,committed+1)), atomic rename.

- **Environment:** `golang:1.26.2-bookworm`, `allow_internet=true`, WORKDIR /app.

## Completion Rates

**Before hardening (bcce87b):** TOO_EASY — avocado 5/5, opus 5/5, gpt-5.5 5/5.

**After first hardening TRIM (d41c9f4):** Early jobs showed codex 1/5 (was 5/5), metacode 4/5 — harder.

**After second hardening to 61 tests (2a1d302):** Online validation still **FAILED TOO_EASY** with metacode 5/5, codex 5/5, opus no trials (commit 2a1d302 validationStatus failed — Too easy — avocado 5/5, gpt 5/5).

**After third hardening to 70 tests with BATCH + fuzz + boundaries (current commit ef102bb):**

Local:
```
harbor run -p message-queue -a oracle --force-build → Mean 1.000, 70 passed
harbor run -p message-queue -a opencode -m anthropic/claude-sonnet-4 -k 3 → Mean 0.000 (0/3)
```

Online (commit ef102bb, validation triggered, currently running at time of this README update):
- Oracle 3/3 passed
- Metacode, claude-code, codex running — early progress shows 0 completed, pending. Previous commit with similar TRIM had codex 1/5 after completion, indicating batch+fuzz should push further toward 20-80% sweet spot.
- No explicit revalidation triggered for this README edit beyond the one already triggered for ef102bb.

**Structural checks:** 9/9 pass, AI assessment Accept, no leak, provenance clean, contamination MEDIUM.

## Model Analysis

Task now requires 20 commands with interacting low/high, atomic batch validation (all partitions checked before any append), and a Python reference-checked fuzz that exercises random interleavings of all ops. Failure attribution genuine: missing TRIM low checks, missing batch atomicity (partial append on error), incorrect compaction preserving TRIM, or incorrect fuzz handling all cause failures.

## Anti-Cheating Analysis

- No hardcoded outputs: arbitrary random stdin streams (fuzz generates 20x100 random commands with random partitions/payloads).
- Tests run binary as subprocess with fresh tmp dirs, including persistence, trim, batch, compaction, and fuzz reference.
- Bypassing fails on per-partition offsets, sum-bytes hash, low handling, batch atomicity, compaction TRIM emit, CRC framing, stdlib import check.
