# codimango/database-corruption

## Task Overview

Fix a defective Go command-line corruption checker `dbfsck` for an append-only
log file. A near-complete but buggy implementation ships in `/app/src` (copied in
by the Dockerfile from `environment/app_src/`); the agent must diagnose and repair
it so it meets the full CLI contract:

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
- **Reference solution** (`solution/solve.sh`): overwrites `/app/src` with the
  corrected stdlib-only Go implementation (`os.ReadFile`, `encoding/binary`,
  `hash/crc32`, `encoding/json`; right-to-left DP with `uint64` framing and bounded
  reads; output opened only after header validation) and builds it. Passes the
  full suite (33/33 locally); the shipped defective source fails 6/33.
- **Environment** (`environment/Dockerfile`): `ubuntu:24.04` + `golang-go`;
  `COPY app_src/ /app/src/` ships the **defective** implementation the agent must
  fix. `solution/` and `tests/` are not present in the agent container.

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
| v6 `1b54688` | **debug-in-place** — ship defective dbfsck, agent finds & fixes | too easy — avocado 5/5, gpt 5/5 (avocado fixed both defects) |

**Status: parked.** Six calibration attempts (five from-scratch, one debug-in-place)
all failed the difficulty gate at avocado 5/5. The task is sound on every other
axis; avocado is simply too strong for a binary-format corruption-recovery task in
any shape tried. See Model Analysis.

## Model Analysis

Five fully-specified *from-scratch* designs (v1–v5) all failed the difficulty gate
as too easy — avocado 5/5 every time, including v5's three stacked implicit
requirements. avocado reads the spec carefully and handles every edge (DP,
padding, no-clobber) even when only implied, so difficulty tuning on a greenfield
recovery task moves only the stronger models. Per the `dr-buffer` finding, the one
shape that has pushed avocado below 5/5 in this repo is **debug-in-place**, so v6
switches to it: a near-complete, plausible-looking `dbfsck` ships in `/app/src`
with two planted defects the agent must locate and fix (bug-finding is avocado's
relative weakness vs. greenfield coding):

1. **Greedy recovery.** The shipped code takes every valid record left to right,
   which under-recovers when a valid record nests inside another's bytes; the fix
   is to maximize the recovered count with a DP.
2. **Output opened before validation.** The shipped code `os.Create`s `--out`
   before checking the header, so an unusable input leaves a stray/clobbered file;
   the fix is to validate fully before writing anything.

The record framing, CRC checking, and trailing-zero-padding accounting are already
correct, so the defects are localized. Locally the shipped source fails 6/33
tests (3 overlap + 3 no-clobber/exit-2); the fix passes 33/33. Open risk: avocado
is a strong enough coder that it may still diagnose both defects (or rewrite the
recovery), in which case the task stays too easy — this is the last untried lever,
not a guarantee.
