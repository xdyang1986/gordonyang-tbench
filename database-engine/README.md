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

Measured by the codimango validation harness (commit `cd4fd82`):

| Runner | Pass rate |
|---|---|
| Oracle (reference solution) | 3/3 |
| avocado (`avocado_dvsc_tester`) | 4/5 |
| Opus (`claude-opus-4-6`) | 5/5 |
| GPT-5.5 | 5/5 |

Validation status: **passing** — structural 9/9, oracle 3/3, difficulty gate
passed ("avocado not trivial and ≥1 agent solved"), AI assessment **Accept**
(0 Critical / 0 High / 0 Medium / 1 Low), contamination MEDIUM, provenance clean.

## Model Analysis

Once the edge semantics (half-open `scan` range, bytewise ordering, empty-value
handling) are stated explicitly in the instruction, the stronger agents solve it
reliably (Opus and GPT-5.5 at 5/5). avocado lands at 4/5: the single trial it
misses is the remaining implicitly-specified behavior — `batch` atomicity, where
a malformed line must leave the store completely unchanged. A rushed
implementation applies `batch` operations incrementally and leaves a
partially-mutated store on the failing line. That one behavior is what keeps the
task off a trivial 5/5 for the weakest runner while remaining solvable by the
rest.
