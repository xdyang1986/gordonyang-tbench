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

State persists durably across separate process invocations (atomic temp-file +
rename, fsync). Standard library only.

The task is deliberately specified at the level of a normal CLI reference: the
core surface is described, and a careful implementation must handle the standard
edge behaviors the tests exercise (half-open range boundaries; empty-string
values as real values distinct from absent keys; batch rollback on a malformed
line; multi-word values; bytewise ordering).

## Test / Solution Details

- **Tests** (`tests/test_outputs.py`): builds the agent's source with
  `go build ./...`, then drives the resulting binary over subprocess with fresh
  per-test databases. Coverage includes core put/get/delete/scan, exit-code
  contract (missing key → exit 3), bytewise ordering, half-open range semantics
  and scan arg forms, empty-value-vs-missing, value-with-spaces, batch success /
  last-write-wins / blank-line handling / **atomic rollback on a malformed
  line**, parent-directory creation, cross-process persistence, a standard-
  library-only check, and a seeded randomized model check mixing single ops and
  batches. Negative tests are paired with positive state checks (not crash-pass).
- **Reference solution** (`solution/solve.sh`): writes a complete stdlib-only Go
  implementation (map + gob, atomic temp-file+rename+fsync, validate-then-apply
  batch) and builds it. Passes the full suite.
- **Environment** (`environment/Dockerfile`): `ubuntu:24.04` + `golang-go`;
  `/app/src` and `/app/data` created empty. No source shipped to the agent.

## Completion Rates

Measured by the codimango validation harness (commit `3d707bd`):

| Runner | Pass rate |
|---|---|
| Oracle (reference solution) | 3/3 |
| avocado (`avocado_dvsc_tester`) | 0/5 |
| Opus (`claude-opus-4-6`) | 2/5 |
| GPT-5.5 | 0/5 |

Difficulty gate: **passed** ("avocado not trivial and ≥1 agent solved").

## Model Analysis

The weaker/mid agents (avocado, GPT-5.5) went 0/5 while Opus solved it 2/5. The
implementation is not algorithmically hard; the failures cluster on the precise
edge semantics a rushed implementation gets wrong — most commonly treating the
`scan` END bound as inclusive, treating an empty-string value as a missing key,
and applying `batch` operations incrementally so a later malformed line leaves a
partially-mutated store. Correct handling of all of these together is what
separates a passing solution from a plausible-but-wrong one.
