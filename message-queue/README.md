# codimango/message-queue

## Task Overview

Build, from scratch in Go (stdlib only, enforced, internet enabled but not needed), a **Kafka-like partitioned message queue broker** as a single `package main` binary at `/app`. Durable, crash-consistent broker that reads commands from stdin and writes results to stdout.

Commands (space-separated tokens, no quoting, timestamps >=0, negative timestamp = invalid input → exit non-zero):
- **Topic lifecycle:** `CREATE_TOPIC <topic> <num_partitions> <ts>` (idempotent), `DELETE_TOPIC <topic> <ts>` (removes messages + consumer state for that topic, but groups persist even when empty — intended, leniently accepts GC)
- **Producer:** `PRODUCE <topic> <partition> <payload> <ts>` → `<offset>`, `PRODUCE_AUTO <topic> <payload> <ts>` → `<partition> <offset>` with `sum(bytes(payload)) % num_partitions`
- **Retention:** `TRIM <topic> <partition> <offset> <ts>` → sets low watermark to max(old low, offset), deleting messages with offset < low; `FETCH` returns `NONE` for trimmed offsets, `COMMIT`/`SEEK` must respect low
- **Consumer:** `FETCH`, `FETCH_RANGE` (effective start = max(start, low)), `POLL <group> <topic> <partition>` → `<offset> <payload>` advancing position and auto-advancing over trimmed ranges, `COMMIT` (>=low or -1), `SEEK` (>=low), `JOIN_GROUP`, `GET_GROUP_OFFSET` (returns NONE if committed < low), `LIST_GROUPS`
- **Metadata:** `LIST_TOPICS`, `TOPIC_INFO` → `<partitions> <total_retained>` where total = sum(high-low), `PARTITION_INFO` → `<low> <high>`
- **Maintenance:** `COMPACT` → atomic rewrite preserving all messages 0..high-1, TRIMs, groups, commits, seeks

Payloads: single tokens (no spaces, no commas, 1..1024B, no comma). Topic/group names `[A-Za-z0-9._-]+`, 1..255, not `.`/`..`. Invalid input (including negative timestamp) → exit non-zero; app errors → `ERROR`.

## What makes this hard (including hardening for too-easy)

- **High initial pass rate (too easy) fix:** Original version had 42 tests, oracle 3/3, metacode 5/5, opus 5/5, codex 5/5 → `TOO_EASY` (95%). Review noted flaky `test_delete_topic_removes_group_state` was only differentiator. After making that test lenient, pass rate became 5/5 across all models, even easier.
- **Hardening via TRIM/retention:** Added `TRIM` command with low/high watermark semantics, which cascades into every other command:
  - `PARTITION_INFO` now returns low/high, not always 0; `TOPIC_INFO` totals retained `sum(high-low)`
  - `FETCH`/`FETCH_RANGE` must treat offset < low as `NONE` and adjust effective start to low
  - `COMMIT` must reject offset < low (except -1) → `ERROR`; `SEEK` must reject below low
  - Consumer groups: position < low auto-advanced to low, committed < low cleared (GET → NONE), POLL after trim must respect new low
  - Durable: TRIM logged only when low increases, recovery replays TRIM and clears/advances group state, compaction must preserve low via TRIM records after emitting all PRODUCEs
  - This interacts with existing durability, compaction, and group logic, requiring correct ordering: CREATE → PRODUCE all (0..high-1) → TRIM low → JOIN → COMMIT (committed>=low) → SEEK
- **Other review fixes (Issues 1-4):**
  - **Issue 1 (group persistence ambiguity):** Spec now explicitly says groups remain visible after DELETE_TOPIC even when empty (Kafka behavior). Test leniently accepts `g` or `NONE` to eliminate 4/5 vs 5/5 variance, but reference keeps empty group.
  - **Issue 2 (wrong example):** Fixed auto-partition example second POLL from `0 bar` to `1 bar` and removed leftover note *“Actually 309%3=0. Let's use different payloads.”*
  - **Issue 3 (stdlib not enforced):** Spec says stdlib only, previously `allow_internet=true` with no check. Now `allow_internet=true` (matches all other Go tasks) but added `test_stdlib_only` that rejects any import containing `.` and external `go.mod` requires.
  - **Issue 4 (negative timestamp):** Spec now explicitly lists negative timestamp as invalid input; added 5 negative-ts cases.
- **Additional hard tests:** 20 extra cases added (now 61 total): many-messages + trim + range, trim then produce offsets continue, delete+recreate resets low, trim group commit after trim, trim group seek/poll, persist group low after restart, large 1024B payload & 201-char topic, 1000 partitions, fetch_range low edge, compact preserves trim.
- **Local calibration after hardening:** `harbor run -p message-queue -a opencode -m anthropic/claude-sonnet-4 -k 3` → **0.000 mean (0/3)** vs previously 0.8 for metacode, indicating increased difficulty. Oracle still **1.000** (61 passed). Sonnet now fails on trim logic.
- Online validation after TRIM (commit `d41c9f4`) shows: oracle 3/3, codex 1/5 (was 5/5), metacode 3/4 running (was 5/5), opus pending — moving from TOO_EASY toward balanced.

## Test / Solution Details

- **Tests** (`tests/test_outputs.py`): 61 tests via `go build -o /tmp/agent_mq .`:
  * basic produce/fetch, auto-hash (foo 324%3=0, bar 309%3=0, baz 317%3=2), fetch NONE beyond high, fetch_range, list sorted, topic_info/partition_info (now low/high), create idempotent, delete, produce errors
  * consumer groups: join/poll, auto-create, commit/get, commit -1 clear, seek, poll after produce, multi-partition, list_groups, offset NONE, delete removes state (lenient), produce_auto→poll, poll isolation
  * error handling: FETCH etc ERROR, commit/seek beyond high, commit -2, trim beyond high
  * invalid input: unknown cmd, arity, bad ints, 0/>1000 partitions, bad names, payload comma, **negative timestamps** (5 cases) → exit non-zero
  * blank lines, deterministic, **stdlib-only**
  * durability: persist across restart, group committed/seek persist, auto-produce persist, torn-tail, bad CRC, truncate-then-append, compact preserves state+seek+trim, stray tmp ignored, empty log clean, in-memory no persist
  * **TRIM (10 new):** basic (low moves, FETCH NONE for trimmed, TOPIC_INFO retained count), fetch_range with low, commit/seek error below low, poll auto-advance after trim, poll after trim+produce, commit cleared on trim, persist group low, many-messages (20 msgs trim 15), produce offsets continue after trim, delete+recreate resets low, large payload 1024B + 201-char topic, 1000 partitions, range low edge, compact preserves trim
- **Reference solution:** Go stdlib-only with `Partition{msgs, low}`, `doTrim` that advances low and clears/advances group committed/positions < low, updated FETCH/FETCH_RANGE/TOPIC_INFO/PARTITION_INFO/COMMIT/SEEK/POLL to respect low, replay handles TRIM, compact emits CREATE → PRODUCE all → TRIM low → JOIN → COMMIT (>=low) → SEEK (!= expected_default where expected=max(low,committed+1)), atomic rename, fsync, handles negative timestamp as invalid.
- **Environment:** `golang:1.26.2-bookworm`, WORKDIR /app, `allow_internet=true`, stdlib check in tests.

## Completion Rates

**Latest online validation (before hardening, commit `bcce87b`):** TOO_EASY — avocado 5/5, opus 5/5, gpt-5.5 5/5.

**After hardening with TRIM (commit `d41c9f4` + extra tests, local + online running):**

| Agent | Model | Pass Rate (latest job) | Mean | Notes |
|---|---|---|---|---|
| Oracle | oracle | 3/3 | 1.000 | reference with TRIM passes 61 tests |
| Codex | gpt-5.5 | 1/5 (prev 5/5) | 0.200 | TRIM causes 4 failures — difficulty up |
| Metacode | meta/avocado-5.14-code | 4/5 (prev 5/5) → running 3/4 (0.75) → fluctuating, not 5/5 | 0.75 | harder than before |
| Claude-code | claude-opus-4-8 | 4/5 (prev 5/5) → pending | 0.80 | previously 5/5, now 4/5 in earlier run |
| Opencode | anthropic/claude-sonnet-4 (local) | 0/3 | 0.000 | local calibration shows TRIM is hard for weaker models |

This moves from **TOO_EASY (95% pass)** toward balanced 20-80%. Final validation is still pending for opus/avocado at time of this README update; local oracle remains 1.0.

**Structural checks:** 9/9 pass, AI assessment Accept, no solution leak, provenance clean, contamination MEDIUM (not found).

**Fixes from review (no explicit model revalidation triggered for this README edit):**
- Issue 1: clarified group persistence + lenient test
- Issue 2: fixed example 0 bar → 1 bar, removed note
- Issue 3: allow_internet true + test_stdlib_only
- Issue 4: negative timestamp → invalid input + tests
- New: added TRIM retention to address TOO_EASY

## Model Analysis

Task now requires integrating 19 commands with interacting low/high watermarks, consumer group committed/position cascade on TRIM, and crash-consistent WAL with compaction that preserves low. Failure attribution is genuine: missing TRIM handling (low check in FETCH/COMMIT/SEEK/POLL, auto-advance, clearing committed < low, compaction TRIM emit) causes failures, not spec ambiguity.

## Anti-Cheating Analysis

- No hardcoded outputs: arbitrary stdin streams with TRIM interleaving, many partitions, large payloads, retention.
- Tests run binary as subprocess with fresh tmp dirs, including persistence + trim + compaction.
- Bypassing fails on per-partition offsets, sum-bytes hash, low/high handling, group auto-advance, compaction preserving TRIM, CRC framing, stdlib import check.
