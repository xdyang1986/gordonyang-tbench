# codimango/database-corruption

## Task Overview

Build, from scratch in Go, a command-line corruption checker `dbfsck` for an
append-only log file. The agent starts with an empty `/app/src` and must
implement the full CLI:

- `dbfsck --in <PATH> [--out <PATH>]`
- Prints a one-line JSON summary `{"recovered":R,"skipped":S}` — records
  recovered and bytes not covered by any recovered record.
- With `--out`, writes a repaired file (header + the recovered records, in
  offset order). Without `--out`, it only reports.
- Exit `0` clean (`skipped == 0`) · `1` corruption found (`skipped > 0`; repaired
  when `--out` is given) · `2` unusable input (unreadable, shorter than the
  header, or bad magic/version — no output file is written).

The on-disk format is fully specified in the instruction: an 8-byte header
(`"DBLG"` + `uint32` LE version `1`) followed by back-to-back records, each
`key_len:u32 · val_len:u32 · key · val · crc:u32` (little-endian), where the CRC
is CRC-32 (IEEE polynomial) over the record minus its CRC field.

**Recovery maximizes the number of records, which is the crux.** A record is
*valid at an offset* iff its declared size fits within the bytes that remain
there **and** its CRC matches. Because a valid record may begin at an offset that
lies **inside** another valid record's bytes, the maximum-record recovery is
**not** the greedy "take the first valid record and advance past it": it can
require skipping a valid record so that more records fit. A correct solution is a
dynamic program over byte offsets (`best[p] = max(best[p+1], 1 + best[p+size])`).
`skipped` is the number of bytes not covered by any recovered record.

## Test / Solution Details

- **Tests** (`tests/test_outputs.py`): builds the agent's source with
  `go build ./...`, enforces the standard-library-only constraint, then constructs
  database files as raw bytes in Python (`zlib.crc32` is the same IEEE CRC-32 as
  Go's `crc32.ChecksumIEEE`), injects corruption, and drives the built binary. A
  reference DP `scan_max()` computes the exact contract; `scan_greedy()` proves a
  crafted input is a genuine separator (greedy recovers strictly fewer records).
  Coverage: clean/empty/binary-safe files, content corruption, length corruption
  (resync), truncated tails, the unusable-input contract (exit 2, no output),
  detect-only mode, an oversized-length "bomb" (no crash / no over-allocation),
  the repaired file being clean when re-checked, the **max-record overlap
  separator** (a valid record whose value bytes are themselves two valid records,
  where the maximum is the two inner records, not the one enclosing record), and
  a seeded randomized model. **Tie-fairness:** exact-output assertions use only
  inputs with a unique optimum; the randomized test asserts the invariant maximum
  count and independently validates the tool's output (valid, non-overlapping, in
  order, consistent `skipped`), so a different-but-optimal tie-break is not
  punished.
- **Reference solution** (`solution/solve.sh`): writes a complete stdlib-only Go
  implementation (`os.ReadFile`, `encoding/binary`, `hash/crc32`, `encoding/json`;
  right-to-left DP with `uint64` framing arithmetic and bounded reads) and builds
  it. Passes the full suite (24/24 locally).
- **Environment** (`environment/Dockerfile`): `ubuntu:24.04` + `golang-go`;
  `/app/src` and `/app/data` created empty. No source shipped to the agent.

## Completion Rates

Calibration history (difficulty gate is the only one that has failed; structural
9/9, oracle 3/3, AI assessment Accept, contamination MEDIUM, provenance passed
throughout):

| Attempt | Design | Result |
|---|---|---|
| v1 `bd91988` | trust-length framing, `{valid,corrupt,truncated}` | too easy — avocado 5/5, opus 5/5 |
| v2 `a583368` | forward-resync recovery, `{recovered,skipped}` | too easy — avocado 5/5, opus 5/5, gpt 5/5 |
| v3 `d26488d` | maximize-record DP, nesting nuance stated explicitly | too easy — avocado 5/5, gpt 4/5 (contamination LOW) |
| v4 (this) | maximize-record DP, nesting nuance left implicit | _tbd_ |

## Model Analysis

The difficulty rests on recognizing that maximum-record recovery is a dynamic
program, not a greedy scan. The first two designs were fully-specified
from-scratch parsing tasks and the weak runner solved every trial (consistent
with the `dr-buffer` finding that pure from-scratch algorithm tasks do not beat
avocado). v3 added a real algorithmic trap — because a valid record can nest
inside another valid record's bytes, the greedy "take the first valid record and
jump past it" recovers fewer records than the optimum — but with the nesting
nuance stated explicitly the weak runner implemented the DP and still went 5/5.
v4 keeps the DP contract (the instruction states the objective: recover the
largest possible number of non-overlapping intact records) but leaves the nesting
consequence implicit: a rushed solver assumes a clean sequential log and writes
greedy, missing the overlap trials, while a careful solver reasons that corruption
can create nested valid frames and reaches for the DP. This mirrors
`database-engine`'s implicit batch-atomicity separator. Locally, a greedy
first-valid implementation passes every test except the overlap-separator cases;
the DP reference passes the full suite. The open risk (flagged during design):
without the explicit hint the stronger runners may also miss the nesting, which
would swing the task from "too easy" to "too hard".
