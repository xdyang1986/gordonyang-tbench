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

Latest validation run (commit `5cec83e`) — **passing**, oracle calibration patch applied locally for harbor docker-compose-build volume mount:

| Check | Result |
|---|---|
| Structural | 8 files (Dockerfile, instruction.md, task.toml, tests) |
| Oracle | 1/1, Mean 1.000, reward 1.0, 42 pytest passed |
| Nop baseline | 1/1, Mean 0.000 (expected fail) |
| Difficulty | hard — 42 edge cases + durability + compaction |
| task.toml validation | fixed — authors, format=terminal_bench_single_turn, workstream=swe_public_repo, subdomain=distributed_systems, usecase=handle_events |

**Oracle run:**
```
harbor run -p message-queue -a oracle -n 1 --force-build
  1/1 Mean: 1.000
  Reward Distribution: reward=1.0 → 1
  42 passed
```

**Model notes:**
- Weak baseline (nop) fails (0.0), oracle succeeds (1.0) → task not trivially solvable, requires full implementation of partitioned logs, group state machine, WAL framing, recovery, compaction.
- No explicit revalidation triggered for this README update.

## Model Analysis

Task requires synthesizing Kafka semantics from spec: per-partition offsets, auto-hash partition selection, consumer group position vs committed separation, auto-create+subscribe on POLL, idempotent CREATE_TOPIC, DELETE purging group state, sorted LIST outputs, FETCH_RANGE joining, error vs invalid-input distinction, WAL with CRC framing and torn-tail handling, compaction minimality. Implementation must be deadlock-free, deterministic, stdlib-only, and survive restarts via correct logging of JOIN/COMMIT/SEEK/POLL position.

## Anti-Cheating Analysis

- No hardcoded outputs viable: broker behavior driven by arbitrary stdin command streams, partition hashing of arbitrary payloads, randomized durability tests involving crash truncations and compaction, group interleaving.
- Hidden tests not shipped in instruction; payloads never contain commas except to test invalid input.
- Grader runs black-box binary with fresh temp dirs per test, including `MQ_STATE_DIR` persistence across multiple invocations.
- Bypassing fails on offset per partition vs global, hash formula mismatch, missing auto-subscribe logging, incorrect compaction (must preserve positions where `pos != committed+1`), incorrect torn-tail truncation, wrong sorted order, or incorrect ERROR vs NONE vs exit handling.
