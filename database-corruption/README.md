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
  after full header validation) and builds it. Passes the full suite (37/37
  locally); a naive implementation (greedy + counts padding + opens `--out` early)
  fails 13/37.
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
| v4 `67d8128` | maximize-record DP, nesting nuance left implicit | too easy — avocado 5/5, gpt 5/5, opus 1/5 |
| v5 `74b646e` | + trailing-zero-padding + no-clobber, three stacked implicit reqs | too easy — avocado 5/5, gpt 5/5 |
| v6 `1b54688` | debug-in-place (reverted — author prefers from-scratch) | too easy — avocado 5/5, gpt 5/5 |
| v7 (this) | from-scratch, five stacked implicit reqs (+embedded-magic, +in-place) | _tbd_ |

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
before validating) fails 13 of 37 tests spanning these requirements; the reference
passes all 37. The prior five from-scratch designs (v1–v5) all failed the
difficulty gate at avocado 5/5, and v6 (a debug-in-place experiment) was reverted
at the author's request; v7 pushes the from-scratch approach further by widening
the implicit-requirement surface. The open risk is unchanged: avocado has cleared
every edge so far even when only implied, so this may still land too easy.
