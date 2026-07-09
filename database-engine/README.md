# codimango/database-engine

## Task Overview

Build, from scratch in Go, a persistent ordered key-value store exposed as a
command-line tool `dbctl`. The agent starts with an empty `/app/src` and must
implement the full CLI:

- `put <KEY> <VALUE>` / `get <KEY>` / `delete <KEY>`
- `scan [START] [END]` — entries in ascending key order (range is half-open
  `[START, END)`)
- `batch` — apply a stream of `put`/`delete` operations from stdin as a single
  all-or-nothing unit
- `stats` — print `live=<L>\tdead=<D>`: live keys vs. dead (superseded/tombstoned)
  records
- `compact` — reclaim dead records (`dead → 0`) without changing `get`/`scan`

State persists durably across separate process invocations. Standard library
only.

**This is a log-structured store, not a map dump.** `put`/`delete` append a
record to a log, and a `delete` always writes a tombstone (even for an absent
key — exit 0, but it adds a dead record). Current state is the replay of the log
(last record per key wins). Superseded puts and tombstones remain as **dead**
records until `compact` rewrites the log down to one live record per present key.

This log-structured contract is the point: `stats` (`dead` count) and `compact`
**cannot be produced by a plain in-memory map** that only tracks current state,
so a solution that recalls a textbook key-value CLI fails. It was added in
response to a novelty review that rated the previous map-based version MEDIUM
recall risk (a "textbook KV store CLI the strong models already know"). A careful
implementation must also handle the standard edge behaviors the tests exercise
(half-open range boundaries; empty-string values as real values distinct from
absent keys; batch rollback on a malformed line; multi-word values; bytewise
ordering).

## Test / Solution Details

- **Tests** (`tests/test_outputs.py`): builds the agent's source with
  `go build ./...`, then drives the resulting binary over subprocess with fresh
  per-test databases. Coverage includes core put/get/delete/scan, exit-code
  contract (missing key → exit 3), bytewise ordering, half-open range semantics
  and scan arg forms, empty-value-vs-missing, value-with-spaces, batch success /
  last-write-wins / blank-line handling / **atomic rollback on a malformed
  line**, parent-directory creation, cross-process persistence, a standard-
  library-only check, the **log-structured `stats`/`compact` contract**
  (overwrites and tombstones accumulate dead records; a delete of an absent key
  still adds a dead record; `compact` reclaims them and is durable across
  processes), and a seeded randomized model that tracks both current state and
  the dead-record count. Negative tests are paired with positive state checks
  (not crash-pass).
- **Reference solution** (`solution/solve.sh`): writes a complete stdlib-only Go
  implementation — an append-only record log (`P\tkey\tval` / `D\tkey`) with
  replay for reads, append+fsync writes, and a temp-file+rename+fsync `compact` —
  and builds it. Passes the full suite (36/36 locally).
- **Environment** (`environment/Dockerfile`): `ubuntu:24.04` + `golang-go`;
  `/app/src` and `/app/data` created empty. No source shipped to the agent.

## Completion Rates

The previous map-based version validated passing (avocado 4/5, opus 5/5, gpt 5/5)
but drew a novelty review of MEDIUM recall risk — a textbook KV-store CLI. This
version adds the log-structured `stats`/`compact` contract to defeat that recall.

Latest validation run (commit `3b6f996`) — **passing**:

| Check | Result |
|---|---|
| Structural | 9/9 |
| Oracle | 3/3 |
| Difficulty balance | passed — avocado 3/5, opus 5/5, gpt 0/5 |
| AI assessment | Accept (0 Critical / 0 High / 0 Medium / 1 Low) |
| Contamination | MEDIUM |
| Provenance | clean |

The log-structured contract both defeated recall and improved difficulty: the
reference passes the full suite (**36/36**), while a plausible **recalled
solution** — the textbook `map`+rewrite KV with `stats`/`compact` bolted on the
obvious way (`dead` always 0, `delete` a plain map delete with no tombstone) —
**fails 7/36**, all of them the log-structured tests. avocado moved from 4/5 to
3/5 (better-centered) and gpt now fails entirely, consistent with the task no
longer yielding to recalled code.

## Model Analysis

Difficulty and novelty now come from the log-structured contract: `stats` must
report `dead` (superseded + tombstoned records) and `compact` must reclaim it,
which requires retaining more than the current key→value state. A solution that
recalls a standard KV CLI — or that reimplements the store as a plain in-memory
map — cannot produce the `dead` count or a correct `compact`, so it fails the
suite regardless of how the current-state `get`/`scan` behavior is written. The
standard edge behaviors from the prior version (half-open `scan`, empty-value vs.
absent, `batch` all-or-nothing rollback) remain in place.
