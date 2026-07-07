# codimango/database-corruption

## Task Overview

Build, from scratch in Go, a command-line corruption checker `dbfsck` for an
append-only log file. The agent starts with an empty `/app/src` and must
implement the full CLI:

- `dbfsck --in <PATH> [--out <PATH>]`
- Prints a one-line JSON summary `{"valid":V,"corrupt":C,"truncated":T}`.
- With `--out`, writes a repaired file (header + the valid records only, in
  original order). Without `--out`, it only reports.
- Exit `0` clean · `1` corruption found (repaired when `--out` is given) · `2`
  unusable input (unreadable, shorter than the header, or bad magic/version — no
  output file is written).

The on-disk format is fully specified in the instruction: an 8-byte header
(`"DBLG"` + `uint32` LE version `1`) followed by back-to-back records, each
`key_len:u32 · val_len:u32 · key · val · crc:u32` (little-endian), where the CRC
is CRC-32 (IEEE polynomial) over the record minus its CRC field.

The task is deliberately specified at the level of a normal reference for a binary
format: the layout and the scanning/exit rules are stated, and a careful
implementation must handle the edge behaviors the tests exercise (continue past a
CRC-corrupt record using the length prefix for framing; a truncated trailing
record; a garbage/oversized length field that must not drive a huge allocation;
empty keys/values and binary-safe bytes; the repaired output being itself clean).

## Test / Solution Details

- **Tests** (`tests/test_outputs.py`): builds the agent's source with
  `go build ./...`, enforces the standard-library-only constraint, then constructs
  database files as raw bytes in Python (`zlib.crc32` is the same IEEE CRC-32 as
  Go's `crc32.ChecksumIEEE`), injects corruption, and drives the built binary.
  Coverage includes clean files, header-only (empty) databases, exact recovered
  output, content corruption in first/middle/last/all positions, truncated tails
  (mid-record and partial length prefix), the unusable-input contract (bad magic,
  wrong version, sub-header, zero-length, missing file → exit 2 with no output),
  detect-only mode, empty-key/empty-value and binary-safe (NUL/tab/newline)
  records, the repaired file being clean when re-checked, and a seeded randomized
  content-corruption model. For **adversarial length-prefix corruption**, where a
  smarter recovery strategy could legitimately differ, the tests only require that
  the tool terminates, does not panic or over-allocate, reports corruption, and
  emits only genuinely-valid records (an in-order subsequence of the good ones) —
  so an alternative recovery strategy is not punished.
- **Reference solution** (`solution/solve.sh`): writes a complete stdlib-only Go
  implementation (`os.ReadFile`, `encoding/binary`, `hash/crc32`, `encoding/json`;
  uint64 framing arithmetic; bounded reads) and builds it. Passes the full suite.
- **Environment** (`environment/Dockerfile`): `ubuntu:24.04` + `golang-go`;
  `/app/src` and `/app/data` created empty. No source shipped to the agent.

## Completion Rates

_To be filled in from the codimango validation harness after the first passing
run (structural / oracle / difficulty gate / AI assessment / contamination /
provenance)._

| Runner | Pass rate |
|---|---|
| Oracle (reference solution) | _tbd_ |
| avocado (`avocado_dvsc_tester`) | _tbd_ |
| Opus | _tbd_ |
| GPT | _tbd_ |

## Model Analysis

The difficulty is concentrated in three behaviors that a rushed implementation
gets wrong even though they are specified: (1) **continuing past a CRC-corrupt
record** rather than aborting on the first bad checksum — recovery must use the
length prefix for framing and keep scanning; (2) **bounded reads** — a truncated
tail or a garbage length field must be detected by checking the declared record
size against the bytes that actually remain, never by allocating from an
unchecked length (a naive reader panics on the short read or tries to allocate
gigabytes); and (3) getting the **truncated-vs-corrupt accounting and exit codes**
right. These are the analogue of `database-engine`'s batch-atomicity separator:
stated in the spec, but easy to implement incorrectly under time pressure.
