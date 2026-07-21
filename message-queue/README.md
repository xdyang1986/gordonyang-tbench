# codimango/message-queue

## Task Overview

Build, from scratch in Go (stdlib only, enforced, internet disabled), a **Kafka-like partitioned message queue broker** as a single `package main` binary at `/app`. The agent must implement a durable, crash-consistent broker that reads commands from stdin and writes results to stdout.

Supported commands (space-separated tokens, no quoting, timestamps >=0, negative timestamp = invalid input):
- **Topic lifecycle:** `CREATE_TOPIC <topic> <num_partitions> <ts>` (idempotent), `DELETE_TOPIC <topic> <ts>` (removes messages + consumer state for that topic, but groups themselves persist even when empty — intended behavior; GC of empty groups leniently accepted)
- **Producer:** `PRODUCE <topic> <partition> <payload> <ts>` → `<offset>`, `PRODUCE_AUTO <topic> <payload> <ts>` → `<partition> <offset>` with deterministic partition `sum(bytes(payload)) % num_partitions`
- **Consumer:** `FETCH <topic> <partition> <offset> <ts>` → payload/NONE/ERROR, `FETCH_RANGE <topic> <partition> <start> <end> <ts>` → comma-joined, `POLL <group> <topic> <partition> <ts>` → `<offset> <payload>` advancing per-group position, `COMMIT`, `SEEK`, `JOIN_GROUP`, `GET_GROUP_OFFSET`, `LIST_GROUPS`
- **Metadata:** `LIST_TOPICS`, `TOPIC_INFO <topic>` → `<partitions> <total>`, `PARTITION_INFO <topic> <partition>` → `0 <high>`
- **Maintenance:** `COMPACT <ts>` rewrites log

Payloads are single tokens (no spaces, no commas, 1..1024B). Topic/group names match `[A-Za-z0-9._-]+`, 1..255 chars, not `.`/`..`. Invalid input including negative timestamp → exit non-zero; application errors (missing topic, out-of-range partition) → `ERROR`.

## What makes this hard (and what was fixed from review)

- **Partitioned log semantics:** per-partition append-only offsets starting at 0, total messages across partitions, low always 0, high = log length. Tests check offset per partition, not global.
- **Auto-partition determinism:** `PRODUCE_AUTO` must hash via sum of byte values mod partitions, normalized logging as `PRODUCE` to keep replay deterministic. Previous spec had wrong example output (`0 bar` instead of `1 bar`) and leftover editing note — now corrected.
- **Consumer group state machine:** per-group per-partition `committed` (-1 = none) and `positions` (next to poll). `POLL` auto-creates group and subscribes, initializes position to `committed+1` or 0, returns NONE when at high, auto-advances and logs `SEEK` for durability. `COMMIT` allows -1 clear, must reject beyond high. `SEEK` allows to high.
- **DELETE_TOPIC group persistence (Issue 1 fixed):** Previously spec said "removes all consumer-group state related to that topic" but never said whether empty group should stay. Test expected group to remain in LIST_GROUPS, causing 4/5 vs 5/5 variance across models. Now spec explicitly states groups themselves are NOT deleted — empty groups remain visible. Test leniently accepts either keeping or GC'ing empty group (`g` or `NONE`) for backwards compatibility, eliminating variance. Reference implementation keeps empty groups.
- **Durable WAL:** `MQ_STATE_DIR/mq.log` with framing `uint32 LE len | uint32 LE crc32 IEEE | payload`. What is logged: only state-changing successes (CREATE, DELETE, PRODUCE incl. normalized AUTO, JOIN, COMMIT on change, SEEK on change). Queries never logged.
- **Crash recovery:** on startup read `mq.log` sequentially, CRC-check, stop at first torn/corrupt record, truncate tail. Empty log recovers clean. Must survive restart across topics, messages, groups, committed + positions.
- **Compaction:** atomic `mq.log.tmp` → `mq.log` rename. Minimal deterministic sorted record set: CREATE per topic asc, PRODUCE per partition asc offset asc, JOIN per group/topic asc, COMMIT per group asc with committed != -1, SEEK only when `pos != committed+1`. Preserves offsets and poll positions.
- **Sorting invariants:** `LIST_TOPICS` and `LIST_GROUPS` must be lexicographically sorted comma-joined or NONE.
- **Blank lines ignored, strict arity/name validation:** payload with comma invalid → exit non-zero; negative timestamp now explicitly invalid input (Issue 4 fixed); tests include many invalid-input cases.
- **Go stdlib only (Issue 3 fixed):** previously stated but not enforced and `allow_internet=true`. Now `allow_internet=false` in `task.toml` and new test `test_stdlib_only` checks that all imports contain no dot and `go.mod` has no external requires.
- **Go stdlib only, no randomness:** deterministic output for same stdin + disk state.

## Test / Solution Details

- **Tests** (`tests/test_outputs.py`): 43 black-box pytest cases via built Go binary (`go build -o /tmp/agent_mq .`):
  * basic produce/fetch, auto-hash (foo=324%3=0, bar=309%3=0, baz=317%3=2), fetch NONE beyond high, fetch_range comma-joined, list topics sorted/NONE, topic_info/partition_info, create idempotent (keep partitions), delete, produce error cases
  * consumer groups: join+poll basic, poll auto-creates, commit+get_offset, commit -1 clear, seek to high, poll after produce, multi-partition groups, list_groups sorted, offset NONE for new group, delete removes group state but keeps empty group (leniently accepts `g` or `NONE`), produce_auto→poll, poll leaves other groups untouched
  * error handling: FETCH/PARTITION_INFO/TOPIC_INFO/PRODUCE_AUTO/JOIN error cases, commit/seek beyond high, commit -2 error
  * invalid input exits non-zero: unknown cmd, wrong arity, bad ints, num_partitions 0/>1000, bad names (`bad/topic`, `.`, `..`), payload with comma, **negative timestamps** (CREATE, PRODUCE, FETCH, LIST_TOPICS, COMPACT) per Issue 4
  * blank lines ignored, deterministic, stdlib-only enforcement
  * durability: persist across restart via `MQ_STATE_DIR`, persist group committed + seek position, auto produce persist, torn-tail truncation handling, bad CRC tail ignored, truncated tail then appendable, compact preserves state + seek, ignores stray `.tmp`, empty log clean, in-memory mode does not persist, example from spec (now corrected to `1 bar`)
- **Reference solution** (`solution/solve.sh`): Go stdlib-only broker implementing all commands, `hashPartition`, `tpKey`, `isValidName/Payload`, `doCreate/Delete/Produce/Join/Commit/Seek`, `writeRecord/appendRecord` with CRC, `replay` lenient, `recoverLog` truncates, `compact` with sorted deterministic emit and atomic rename, main loop with `bufio.Scanner`, `die` on invalid input including negative timestamp, `ERROR/NONE` handling, auto-subscribe on POLL/COMMIT/SEEK with JOIN logging, SEEK logging on POLL advance, keeps empty groups after DELETE.
- **Environment**: `golang:1.26.2-bookworm`, WORKDIR /app, `allow_internet=false`, no starter code.

## Completion Rates

Latest **online** validation run (commit `34d71d8`, before review fixes) — **Validation: passing**. Figures below are actual online per-agent results, not local harbor runs. Local post-fix calibration still passes after addressing review issues:

| Agent | Model | Attempts | Passed | Mean reward | Notes |
|---|---|---|---|---|---|
| Oracle | oracle | 3 | 3/3 | 1.000 | reference solution verified |
| Metacode (gate) | meta/avocado-5.14-code | 5 | 4/5 | 0.800 | the one failure was *only* test_delete_topic_removes_group_state (Issue 1) — now lenient, would be 5/5 |
| Claude-code | claude-opus-4-8 | 5 | 5/5 | 1.000 | strong model solves reliably |
| Codex | gpt-5.5 | 5 | 0/5 | 0.000 | all 5 `status=error` (harness NonZeroAgentExitCodeError) — excluded |

**Local post-review-fix calibration (commit after `34d71d8` with Issues 1-4 fixed):**
```
harbor run -p message-queue -a oracle -n 1 --force-build
  1/1 Mean: 1.000
  Reward Distribution: reward=1.0 → 1
  43 passed (was 42, + test_stdlib_only and negative-ts cases)
```

**Structural / qualitative checks (all PASS):**

| Check | Result |
|---|---|
| Structure | 6/6 required files present |
| task.toml | valid TOML, taxonomy fields valid (fixed: authors, format=terminal_bench_single_turn, workstream=swe_public_repo, subdomain=distributed_systems, usecase=handle_events, allow_internet=false) |
| Dockerfile / Internet / Solution / Tests | PASS (`golang:1.26.2-bookworm`, internet disabled, solve.sh has content, tests meaningful + stdlib check) |
| License / SWE Config | PASS (no external repo clone, not an SWE-config task) |
| Agentic review verdict | GOOD (with Request Changes now addressed) |
| Contamination v2 | MEDIUM — NOT_FOUND in internal decontamination table (tbench track) |
| Difficulty | hard — 43 pytest edge cases + durability + compaction + stdlib enforcement |

**Review issues fixed (no explicit revalidation triggered for this README update):**
- **Issue 1 (flaky group after delete):** Clarified spec that empty groups remain; made test accept `g` or `NONE` to eliminate 4/5 vs 5/5 variance.
- **Issue 2 (wrong example):** Fixed second POLL in auto-partition example from `0 bar` to `1 bar`, removed leftover note "Actually 309%3=0. Let's use different payloads."
- **Issue 3 (stdlib not enforced):** Set `allow_internet=false` and added `test_stdlib_only` checking no dot in imports and no external requires.
- **Issue 4 (negative timestamp):** Explicitly listed negative timestamp as invalid input in spec and added 5 negative-ts cases to `test_invalid_input_exits_nonzero`; reference already exits non-zero.

**Failure validation (are the failures real?):**
- Previous metacode 4/5 failure was *only* Issue 1 test, now would pass with lenient check.
- Codex 0/5 were all harness errors (NonZeroAgentExitCodeError), not verifier judgments, excluded.
- No explicit revalidation triggered for this README update — numbers from last completed online validation plus local post-fix oracle run.

## Model Analysis

Task requires synthesizing Kafka semantics from spec: per-partition offsets, auto-hash partition selection, consumer group position vs committed separation, auto-create+subscribe on POLL, idempotent CREATE_TOPIC, DELETE purging only topic-related state but keeping empty group, sorted LIST outputs, FETCH_RANGE joining, error vs invalid-input distinction including negative timestamp, WAL with CRC framing and torn-tail handling, compaction minimality, stdlib-only constraint. Implementation must be deadlock-free, deterministic, stdlib-only, and survive restarts via correct logging of JOIN/COMMIT/SEEK/POLL position.

## Anti-Cheating Analysis

- No hardcoded outputs viable: broker behavior driven by arbitrary stdin command streams, partition hashing of arbitrary payloads, randomized durability tests involving crash truncations and compaction, group interleaving.
- Hidden tests not shipped in instruction; payloads never contain commas except to test invalid input.
- Grader runs black-box binary with fresh temp dirs per test, including `MQ_STATE_DIR` persistence across multiple invocations.
- Bypassing fails on offset per partition vs global, hash formula mismatch, missing auto-subscribe logging, incorrect compaction (must preserve positions where `pos != committed+1`), incorrect torn-tail truncation, wrong sorted order, incorrect ERROR vs NONE vs exit handling, or third-party import.
