# codimango/message-queue

## Task Overview

Build, from scratch in Go (stdlib only), a **Kafka-like partitioned message queue broker** as a single `package main` binary at `/app`. The agent must implement a durable, crash-consistent broker that reads commands from stdin and writes results to stdout.

Supported commands (space-separated tokens, no quoting):
- **Topic lifecycle:** `CREATE_TOPIC <topic> <num_partitions> <ts>` (idempotent), `DELETE_TOPIC <topic> <ts>` (removes messages + consumer state)
- **Producer:** `PRODUCE <topic> <partition> <payload> <ts>` → `<offset>`, `PRODUCE_AUTO <topic> <payload> <ts>` → `<partition> <offset>` with deterministic partition `sum(bytes(payload)) % num_partitions`
- **Consumer:** `FETCH <topic> <partition> <offset> <ts>` → payload/NONE/ERROR, `FETCH_RANGE <topic> <partition> <start> <end> <ts>` → comma-joined, `POLL <group> <topic> <partition> <ts>` → `<offset> <payload>` advancing per-group position, `COMMIT`, `SEEK`, `JOIN_GROUP`, `GET_GROUP_OFFSET`, `LIST_GROUPS`
- **Metadata:** `LIST_TOPICS`, `TOPIC_INFO <topic>` → `<partitions> <total>`, `PARTITION_INFO <topic> <partition>` → `0 <high>`
- **Maintenance:** `COMPACT <ts>` rewrites log

Payloads are single tokens (no spaces, no commas, 1..1024B). Topic/group names match `[A-Za-z0-9._-]+`, 1..255 chars, not `.`/`..`. Invalid input → exit non-zero; application errors (missing topic, out-of-range partition) → `ERROR`.

## What makes this hard

- **Partitioned log semantics:** per-partition append-only offsets starting at 0, total messages across partitions, low always 0, high = log length. Tests check offset per partition, not global.
- **Auto-partition determinism:** `PRODUCE_AUTO` must hash via sum of byte values mod partitions, normalized logging as `PRODUCE` to keep replay deterministic.
- **Consumer group state machine:** per-group per-partition `committed` (-1 = none) and `positions` (next to poll). `POLL` auto-creates group and subscribes, initializes position to `committed+1` or 0, returns NONE when at high, auto-advances and logs `SEEK` for durability. `COMMIT` allows -1 clear, must reject beyond high. `SEEK` allows to high. `DELETE_TOPIC` must purge all related group state.
- **Durable WAL:** `MQ_STATE_DIR/mq.log` with framing `uint32 LE len | uint32 LE crc32 IEEE | payload`. What is logged: only state-changing successes (CREATE, DELETE, PRODUCE incl. normalized AUTO, JOIN, COMMIT on change, SEEK on change). Queries never logged.
- **Crash recovery:** on startup read `mq.log` sequentially, CRC-check, stop at first torn/corrupt record, truncate tail. Empty log recovers clean. Must survive restart across topics, messages, groups, committed + positions.
- **Compaction:** atomic `mq.log.tmp` → `mq.log` rename. Minimal deterministic sorted record set: CREATE per topic asc, PRODUCE per partition asc offset asc, JOIN per group/topic asc, COMMIT per group asc with committed != -1, SEEK only when `pos != committed+1`. Preserves offsets and poll positions.
- **Sorting invariants:** `LIST_TOPICS` and `LIST_GROUPS` must be lexicographically sorted comma-joined or NONE.
- **Blank lines ignored, strict arity/name validation:** payload with comma invalid → exit non-zero; tests include many invalid-input cases.
- **Go stdlib only, no randomness:** deterministic output for same stdin + disk state.

## Test / Solution Details

- **Tests** (`tests/test_outputs.py`): 42 black-box pytest cases via built Go binary (`go build -o /tmp/agent_mq .`):
  * basic produce/fetch, auto-hash, fetch NONE beyond high, fetch_range comma-joined, list topics sorted/NONE, topic_info/partition_info, create idempotent (keep partitions), delete, produce error cases
  * consumer groups: join+poll basic, poll auto-creates, commit+get_offset, commit -1 clear, seek to high, poll after produce, multi-partition groups, list_groups sorted, offset NONE for new group, delete removes group state, produce_auto→poll, poll leaves other groups untouched
  * error handling: FETCH/PARTITION_INFO/TOPIC_INFO/PRODUCE_AUTO/JOIN error cases, commit/seek beyond high, commit -2 error
  * invalid input exits non-zero: unknown cmd, wrong arity, bad ints, num_partitions 0/>1000, bad names (`bad/topic`, `.`, `..`), payload with comma
  * blank lines ignored, deterministic
  * durability: persist across restart via `MQ_STATE_DIR`, persist group committed + seek position, auto produce persist, torn-tail truncation handling, bad CRC tail ignored, truncated tail then appendable, compact preserves state + seek, ignores stray `.tmp`, empty log clean, in-memory mode does not persist, example from spec
- **Reference solution** (`solution/solve.sh`): Go stdlib-only broker implementing all commands, `hashPartition`, `tpKey`, `isValidName/Payload`, `doCreate/Delete/Produce/Join/Commit/Seek`, `writeRecord/appendRecord` with CRC, `replay` lenient, `recoverLog` truncates, `compact` with sorted deterministic emit and atomic rename, main loop with `bufio.Scanner`, `die` on invalid input, `ERROR/NONE` handling, auto-subscribe on POLL/COMMIT/SEEK with JOIN logging, SEEK logging on POLL advance for durability.
- **Environment**: `golang:1.26.2-bookworm`, WORKDIR /app, no starter code.

## Completion Rates

Latest **online** validation run (commit `34d71d8`, multimango.com) — **Validation: passing**. Numbers below are the actual online per-agent results, not a local harbor run.

| Agent | Model | Attempts | Passed | Mean reward | Notes |
|---|---|---|---|---|---|
| Oracle | oracle | 3 | 3/3 | 1.000 | reference solution verified |
| Metacode (gate) | meta/avocado-5.14-code | 5 | 4/5 | 0.800 | genuine discriminator (see below) |
| Claude-code | claude-opus-4-8 | 5 | 5/5 | 1.000 | strong model solves reliably |
| Codex | gpt-5.5 | 5 | 0/5 | 0.000 | ⚠ all 5 `status=error` (harness) — excluded |

**Structural / qualitative checks (all PASS):**

| Check | Result |
|---|---|
| Structure | 6/6 required files present |
| task.toml | valid TOML, taxonomy fields valid |
| Dockerfile / Internet / Solution / Tests | PASS (`golang:1.26.2-bookworm`, internet enabled, solve.sh has content, tests meaningful) |
| License / SWE Config | PASS (no external repo clone, not an SWE-config task) |
| Agentic review verdict | GOOD |
| Contamination v2 | MEDIUM — NOT_FOUND in internal decontamination table (tbench track) |
| Difficulty | hard — 42 pytest edge cases + durability + compaction |

**Failure validation (are the failures real?):**
- **Metacode 4/5 — the one failure is genuine.** The failing trial finished with `status=completed`, `reward=0`, no exception, agent ran to completion and the verifier executed — i.e. avocado produced a solution that legitimately failed the pytest suite. A valid discriminating data point, not an infra flake.
- **Codex 0/5 — NOT a valid difficulty signal.** All 5 trials ended in `status=error` with `NonZeroAgentExitCodeError` (the codex CLI itself exited 1 mid-run, after ~390s and real token spend), so these are agent-harness errors rather than verifier judgments. The separate codex agentic-review run passed 1/1 (verdict GOOD), confirming codex *can* run the task. These 5 errored trials are excluded from the calibration read.

**Calibration read:**
- **Well-calibrated discriminator.** Oracle 3/3 and Opus 4.8 5/5 confirm the spec is solvable; the avocado gate lands at 4/5 (not the too-easy 5/5) with one genuine completed failure — the task separates model strength without being trivially solvable or unfair.
- No explicit revalidation triggered for this README update — figures read from the last completed online validation run via `codimango api`.

## Model Analysis

Task requires synthesizing Kafka semantics from spec: per-partition offsets, auto-hash partition selection, consumer group position vs committed separation, auto-create+subscribe on POLL, idempotent CREATE_TOPIC, DELETE purging group state, sorted LIST outputs, FETCH_RANGE joining, error vs invalid-input distinction, WAL with CRC framing and torn-tail handling, compaction minimality. Implementation must be deadlock-free, deterministic, stdlib-only, and survive restarts via correct logging of JOIN/COMMIT/SEEK/POLL position.

## Anti-Cheating Analysis

- No hardcoded outputs viable: broker behavior driven by arbitrary stdin command streams, partition hashing of arbitrary payloads, randomized durability tests involving crash truncations and compaction, group interleaving.
- Hidden tests not shipped in instruction; payloads never contain commas except to test invalid input.
- Grader runs black-box binary with fresh temp dirs per test, including `MQ_STATE_DIR` persistence across multiple invocations.
- Bypassing fails on offset per partition vs global, hash formula mismatch, missing auto-subscribe logging, incorrect compaction (must preserve positions where `pos != committed+1`), incorrect torn-tail truncation, wrong sorted order, or incorrect ERROR vs NONE vs exit handling.
