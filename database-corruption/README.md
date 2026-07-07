# codimango/database-corruption

## Task Overview

Build, from scratch in Go, a command-line corruption checker `dbfsck` for a
pre-allocated append-only log file. The agent starts with an empty `/app/src` and
must implement the full CLI:

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
  Additional implicit-requirement coverage: the magic `"DBLG"` appearing inside a
  record value or a corrupt region must be treated as data (recovery is by
  CRC/length framing, never by scanning for magic); in-place repair
  (`--in == --out`) must read the whole input before writing; and a larger
  scattered-corruption input checks correctness (and tractable DP performance) at
  scale.
- **Reference solution** (`solution/solve.sh`): writes a complete stdlib-only Go
  implementation (`os.ReadFile`, `encoding/binary`, `hash/crc32`, `encoding/json`;
  right-to-left DP with `uint64` framing and bounded reads; output opened only
  after full header validation) and builds it. Passes the full suite (42/42
  locally); a naive implementation (greedy + counts padding + opens `--out` early)
  fails 15/42.
- **Environment** (`environment/Dockerfile`): `ubuntu:24.04` + `golang-go`;
  `/app/src` and `/app/data` created empty. No source shipped to the agent.

## Completion Rates

Latest validation run (commit `afde591`) — **passing**:

| Check | Result |
|---|---|
| Structural | 9/9 |
| Oracle | 3/3 |
| Difficulty balance | passed — avocado 4/5, opus 2/5, gpt 5/5 |
| AI assessment | Accept (0 Critical / 0 High / 2 Medium / 1 Low) |
| Contamination | LOW |
| Provenance | SUSPECT — review recommended (non-blocking) |

The difficulty gate passes because the weak runner (avocado) is not trivial (4/5)
while at least one stronger runner solves it (gpt 5/5). The AI-assessment Mediums
are the intended "spec requires some guessing" implicit difficulty (see Model
Analysis). Provenance is a non-blocking soft flag on the spec prose.

## Model Analysis

This is a from-scratch task whose difficulty comes entirely from requirements that
are stated only implicitly in the prompt, so a rushed solution must get all of
them right on every one of avocado's five trials. v7 stacks five:

1. **Maximize recovered records.** A valid record can begin inside another valid
   record's bytes, so greedy recovery is suboptimal; the maximum needs a DP. The
   instruction states only the objective, not that nesting is possible.
2. **Trailing zero padding is not corruption.** The file is pre-allocated, so a
   run of `0x00` at the end is unused free space — excluded from `skipped`. But
   zeros *between* records are corruption, and a record whose value legitimately
   ends in `0x00` must not be trimmed.
3. **No-clobber / in-place safety.** On an unusable input the tool writes nothing
   and leaves a pre-existing `--out` untouched; and `--in == --out` must work
   (read fully before writing). Both require validating and reading before opening
   the output for write.
4. **Magic is data outside the header.** `"DBLG"` inside a record value or a
   damaged region is ordinary data — recovery is by CRC/length framing, never by
   scanning for the magic bytes.
5. **No alignment / offset assumptions and bounded reads.** Records may start at
   any byte offset (including inside a corrupt region's declared span), and an
   oversized length field must never drive a huge allocation or a crash.

Locally a naive implementation (greedy + counts padding as skipped + opens `--out`
before validating) fails 15 of 42 tests spanning these requirements; the reference
passes all 42. Widening the implicit-requirement surface to these five independent
requirements is what clears the difficulty gate: the weak runner slips on at least
one of its five trials while a stronger runner still solves the whole suite.
