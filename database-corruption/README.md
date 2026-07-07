# codimango/database-corruption

## Task Overview

Build, from scratch in Go, a command-line corruption checker `dbfsck` for an
append-only log file. The agent starts with an empty `/app/src` and must
implement the full CLI:

- `dbfsck --in <PATH> [--out <PATH>]`
- Prints a one-line JSON summary `{"recovered":R,"skipped":S}` — records
  recovered and bytes skipped.
- With `--out`, writes a repaired file (header + the recovered records only, in
  original order). Without `--out`, it only reports.
- Exit `0` clean (`skipped == 0`) · `1` corruption found (`skipped > 0`; repaired
  when `--out` is given) · `2` unusable input (unreadable, shorter than the
  header, or bad magic/version — no output file is written).

The on-disk format is fully specified in the instruction: an 8-byte header
(`"DBLG"` + `uint32` LE version `1`) followed by back-to-back records, each
`key_len:u32 · val_len:u32 · key · val · crc:u32` (little-endian), where the CRC
is CRC-32 (IEEE polynomial) over the record minus its CRC field.

**Recovery is by forward resynchronization**, which is the crux of the task. A
record is *valid at a position* iff its declared size fits within the bytes that
remain there **and** its CRC matches. Starting after the header, `dbfsck` outputs
a valid record and advances past it; on hitting anything else it advances **one
byte at a time** to the next valid record (or the end of file), counting the
bytes passed over as `skipped`. The consequence is the difficulty: a corrupt
length field must **not** desync the rest of the file — the records after a
corrupted region are still recovered. A naive reader that trusts the length
prefix and advances by the declared size overshoots (or undershoots) at a bad
length and loses everything after it.

## Test / Solution Details

- **Tests** (`tests/test_outputs.py`): builds the agent's source with
  `go build ./...`, enforces the standard-library-only constraint, then constructs
  database files as raw bytes in Python (`zlib.crc32` is the same IEEE CRC-32 as
  Go's `crc32.ChecksumIEEE`), injects corruption, and drives the built binary. A
  reference `scan()` implements the exact recovery contract, so every test's
  expected `{recovered, skipped}` and expected repaired bytes are computed, not
  hard-coded. Coverage: clean/empty/binary-safe files, content corruption
  (single, multiple, adjacent-merge, all), **length corruption mid-file / first
  record / understated length — all requiring resynchronization to recover the
  following records**, truncated tails, the unusable-input contract (exit 2, no
  output), detect-only mode, an oversized-length "bomb" (no crash / no
  over-allocation), the repaired file being clean when re-checked, and a seeded
  randomized model mixing clean / content-corrupt / length-corrupt records.
- **Reference solution** (`solution/solve.sh`): writes a complete stdlib-only Go
  implementation (`os.ReadFile`, `encoding/binary`, `hash/crc32`, `encoding/json`;
  greedy forward resync with `uint64` framing arithmetic and bounded reads) and
  builds it. Passes the full suite (26/26 locally).
- **Environment** (`environment/Dockerfile`): `ubuntu:24.04` + `golang-go`;
  `/app/src` and `/app/data` created empty. No source shipped to the agent.

## Completion Rates

First validation attempt (commit `bd91988`, pre-resync design) failed only the
difficulty gate — **too easy: avocado 5/5, opus 5/5** (structural 9/9, oracle
3/3, AI assessment Accept, contamination MEDIUM, provenance passed). The task was
then redesigned to require forward resynchronization; rates below to be filled in
from the next validation run.

| Runner | Pass rate |
|---|---|
| Oracle (reference solution) | _tbd_ |
| avocado (`avocado_dvsc_tester`) | _tbd_ |
| Opus | _tbd_ |
| GPT | _tbd_ |

## Model Analysis

Difficulty is concentrated in **forward resynchronization after a corrupt length
field**. The first design trusted the length prefix for framing (skip a
CRC-corrupt record by advancing its declared size, stop on a truncated tail); the
weak runner solved all five trials, so the platform rejected it as trivial. The
resync contract changes that: when a record's declared length is corrupt, a
reader that advances by the declared size desyncs and loses every record after
the bad one, whereas the required behavior is to scan forward byte by byte to the
next CRC-valid record and keep recovering. Locally, a naive length-trusting
implementation (the shape the weak runner produced) fails exactly the
length-corruption and randomized-model tests while still passing the
content-corruption and truncation cases — the intended separator between a rushed
and a careful solution. Bounded reads (never allocate from an unchecked length)
and the exit-code / `skipped`-byte accounting remain secondary correctness
requirements.
