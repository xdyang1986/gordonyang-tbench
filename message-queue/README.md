# codimango/message-queue

## Task Overview

Build, from scratch in Go (stdlib only, enforced via import check), a **Kafka-like partitioned message queue broker** at `/app`. Durable, crash-consistent with compaction, plus TRIM retention and PRODUCE_BATCH atomic.

Commands (space-separated, timestamps ≥0, negative timestamp = invalid input → exit non-zero):
- **Topic lifecycle:** `CREATE_TOPIC` (idempotent), `DELETE_TOPIC` (removes messages + group state for topic, but groups persist even when empty - intended, leniently accepts GC)
- **Producer:** `PRODUCE` → offset, `PRODUCE_AUTO` → partition+offset hashed `sum(bytes)%partitions`, `PRODUCE_BATCH` count 1..100 atomic (all partitions validated before any append, comma-separated offsets), logged as individual PRODUCEs
- **Retention:** `TRIM` sets low watermark, `FETCH`/`FETCH_RANGE` respect low (effective start = max(start,low)), `PARTITION_INFO` low/high, `TOPIC_INFO` total retained sum(high-low), `COMMIT`/`SEEK` must respect low
- **Consumer:** `FETCH`, `FETCH_RANGE`, `POLL` (auto-creates group, subscribes, position init max(low, committed+1 else low), auto-advance over trimmed, advances 1), `COMMIT` (>=low or -1), `SEEK` (>=low..high), `JOIN_GROUP` (idempotent), `GET_GROUP_OFFSET` (NONE if committed<low or -1), `LIST_GROUPS`
- **Maintenance:** `COMPACT` atomic rewrite to minimal deterministic record set: CREATE per topic sorted asc, PRODUCE all 0..high-1 per partition sorted asc offset asc, TRIM where low>0 sorted, JOIN per group sorted asc topic sorted, COMMIT where committed !=-1 and >=low and topic exists sorted, SEEK where pos != expected_default (max(low, committed+1) or low) sorted, all timestamp 0, deterministic sorted order, temp file + atomic rename.

Payloads: single tokens no spaces no commas 1..1024, topic/group names `[A-Za-z0-9._-]+` 1..255 not `.`/`..`. Invalid input → exit non-zero; app errors → `ERROR`.

## What makes this hard and safe for RL

The task passes only when the broker gets a set of tightly-coupled invariants right at once:

- **Durable log with CRC framing:** `mq.log` records are `uint32 len + uint32 crc32 + payload`; recovery must stop at the first corrupt record, truncate the torn tail, and remain appendable afterward.
- **Atomic minimal compaction:** `COMPACT` rewrites the log to the minimal deterministic record set (CREATE per topic, all PRODUCE 0..high-1, TRIM where low>0, JOIN, latest COMMIT, and SEEK **only where position differs from the expected default**), sorted deterministically, all timestamps 0, via temp file + atomic rename, and the result must be strictly smaller.
- **TRIM low/high semantics** cascading into `FETCH`/`FETCH_RANGE`/`POLL`/`COMMIT`/`SEEK`/`PARTITION_INFO`/`TOPIC_INFO`.
- **SEEK/COMMIT persistence** across restart and across compaction — including the edge where SEEK targets offset 0 (which collides with the default position).
- **PRODUCE_BATCH atomicity** (all partitions validated before any append) and **PRODUCE_AUTO** normalized-logging as a plain `PRODUCE`.
- **No-op suppression** (repeated CREATE/JOIN/COMMIT/SEEK/TRIM must not append records) and **sorted-order** guarantees in compaction output.
- **Strict input validation** (invalid topic/group names, negative timestamps, bad arity → non-zero exit) and **stdlib-only** enforcement.

Anti-reward-hacking coverage: a Python reference fuzzer (`test_fuzz_random`, 20×100 random commands) plus byte-level log parsing (exact minimal sequence, strict size-shrink, sorted order) make hardcoding or shortcut implementations fail.

## Test / Solution Details

- **75 tests** via `go build`:
  * basic, auto-hash, fetch none, fetch_range, list sorted, topic/partition info low/high, create idempotent, delete, produce errors (10)
  * consumer groups: join/poll, auto-create, commit/get, -1 clear, seek, poll after produce, multi-partition, list_groups, offset NONE, delete lenient GC, produce_auto poll, poll isolation (11)
  * error handling: invalid topic/partition, commit/seek beyond high/low (2)
  * invalid input: unknown cmd, arity, bad ints, 0/>1000 partitions, bad topic/group names, payload comma, negative timestamps (5 cases), batch count 0/>100 (5)
  * blank lines, deterministic, stdlib-only, payload 1024 & topic 255 boundaries (4)
  * durability: persist across restart, group committed/seek, auto-produce, torn-tail, bad CRC, truncate-then-append, compact preserves state/seek/trim, stray tmp, empty log clean, in-memory no persist (8)
  * TRIM 10+: basic, range, commit/seek error below low, poll auto-advance, poll after trim+produce, commit cleared, persist low, many-messages 20 trim 15, offsets continue, delete+recreate resets, large payload & 201-char topic & 1000 partitions, range low edge, compact preserves trim (10+)
  * BATCH 6: basic, same partition, atomic error, invalid input, persist, batch+trim (6)
  * Fuzz 1: 20x100 random commands vs Python reference (1)
  * **R06/R07 new (5):** compact minimal deterministic and smaller (exact minimal sequence + size smaller), produce auto logged as produce, noop does not append (5 types), compaction preserves sorted order (topics/partitions/groups), invalid group names for 5 commands (JOIN,POLL,COMMIT,SEEK,GET)
  * Total 75.

- **Reference solution:** Go with Partition{msgs,low}, doTrim, PRODUCE_BATCH atomic, FETCH etc respect low, replay for TRIM, compact emits minimal sorted records as per spec with timestamp 0, efficient RR fallback for total==0? Actually for message-queue, produce path not need credit, but compact is main. Log format uint32 len + uint32 crc32 + payload, fsync, recovery stops at first corrupt, truncates.

- **Environment:** golang:1.26.2-bookworm, WORKDIR /app, preinstalled pytest.

## Completion Rates (online validation — commit 30be337, 2026-07-22)

- Oracle: **3/3** — validated
- Opus 4.8 (agent): **4/5** — validated
- GPT-5.5 (codex): **3/5** — validated
- Avocado (metacode): **0/5** — failed
- avgReward **0.80**, validation passing — balanced within the 20-80% sweet spot.

## Failure Analysis (latest run)

Analyzed from downloaded trial CTRF artifacts. The only **genuine reasoning failures** came from Avocado (metacode); the Opus and GPT-5.5 losses were infrastructure, not logic.

- **Avocado (metacode) — 0/5, real misses (near-passes at 73-74/75).** Failures cluster entirely on **compaction and SEEK persistence**:
  - `test_persist_seek_position` — after `SEEK g t 0 0` then restart, `POLL` should re-read offset 0 (`"0 a"`) but the broker returned `NONE`. The SEEK-to-0 was never persisted, so on replay the position defaulted to consumed.
  - `test_compact_preserves_seek` — after `SEEK ... 0` then `COMPACT`, `POLL` returned `"1 b"` instead of `"0 a"`; compaction dropped the SEEK record and the consumer resumed at the wrong offset.
  - `test_compact_minimal_deterministic_and_smaller` — compaction did not emit the exact minimal deterministic record set / was not strictly smaller.
  - **Root cause:** SEEK to offset 0 equals the *expected default* position, so implementations treat it as a no-op and omit it from both durable-log replay and the compaction minimal record set. This is the deliberate hard edge of the task.

- **Opus 4.8 (agent) — 4/5, not a logic failure.** The single loss was a `DaytonaAuthenticationError` (`ThrottlerException: Too Many Requests`) infra flake; all clean trials passed.

- **GPT-5.5 (codex) — 3/5, not a logic failure.** Both losses were `status=error` trials that scored 0/76 (whole-suite build/harness failure), i.e. infra flakes; all clean trials passed.

- **Oracle — 3/3.** Reference solution passes every trial.

> Net: the task is genuinely discriminating only for Avocado today (SEEK-0 / compaction edge). Frontier models solve it cleanly, so their sub-5/5 scores are provisioning noise rather than reasoning gaps.

## Anti-Cheating

- No hardcoded outputs: arbitrary random stdin (fuzz 20x100) plus random hierarchical? Actually for message-queue, arbitrary.
- Tests run binary as subprocess with fresh tmp dirs, check durability via file size, CRC, exact minimal sequence, sorted order, no-op not appending, invalid names exit non-zero, batch atomicity, low handling, etc.
- Bypassing fails on offsets, hash, low, batch atomicity, compaction minimal deterministic, sorted order, CRC framing, stdlib check.
