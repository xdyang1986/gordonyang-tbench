# codimango/rate-limit

## Task Overview

Build, from scratch in Go, a **crash-consistent**, log-structured token-bucket rate limiter exposed
as a command-line tool `rlctl`. The agent starts with an empty `/app/src` and must implement the full
CLI:

- `set <KEY> <CAPACITY> <REFILL>` / `peek <KEY> <TS>` / `delete <KEY>`
- `allow <KEY> <TOKENS> <TIMESTAMP_MS>` — consumes tokens or denies, deterministic refill math
- `scan [START] [END]` — buckets in ascending raw-byte key order (range half-open `[START, END)`)
- `batch` — apply a **TAB-delimited** stream of `set`/`delete`/`allow` from stdin, all-or-nothing
- `stats` — print `live=<L>\tdead=<D>`
- `compact` — reclaim dead records (`dead → 0`) without changing peek/scan

State persists durably across separate process invocations. Standard library only.

### What makes this harder than a textbook token bucket

This is not a from-a-tutorial rate limiter or a Bitcask clone. The difficulty comes from a **mandated
binary log format plus asymmetric crash-recovery semantics** that a recalled solution gets wrong:

1. **Mandated on-disk framing.** The log is binary, not text lines. Every record is
   `uint32be(len) | payload | uint32be(crc32ieee(payload))`. `set`/`allow` payloads are
   `'S' | uint32be(keylen) | key | int64be(cap,refill,tokens,last)`; `delete` is `'D' | uint32be(keylen) | key`.
   Tests assert the bytes on disk exactly.
2. **Asymmetric crash recovery.** A truncated or CRC-bad record **at end-of-file** is a torn write:
   drop it, and truncate it away on the next append. A CRC-bad, truncated-in-the-middle, or
   unknown-type record **before** EOF is fatal corruption → exit code **4**. Getting only one side of
   this right fails half the recovery tests.
3. **Overflow-safe refill math.** `available = min(capacity, tokens + floor(refill * delta_ms / 1000))`
   must use saturating arithmetic — `refill * delta` overflows int64 at large timestamps; a naive
   64-bit multiply yields a negative/garbage value.
4. **Byte-safe keys.** Keys may contain any byte except NUL, TAB, and LF (spaces and other bytes are
   legal). This forces byte-safe framing, TAB-delimited `batch` input (not whitespace-split), and
   explicit key validation (a key containing NUL/TAB/LF is rejected with exit 2).
5. **Crash-safe compaction.** `compact` rewrites via temp+rename+fsync; a stale `<db>.compact.tmp`
   left by a crash mid-compaction must be ignored.

The log-structured contract is still the backbone: `set`/`delete`/successful-`allow` append; `delete`
always writes a tombstone; current state is the replay with last-record-per-key wins; superseded
records are **dead** until `compact`.

## Test / Solution Details

- **Tests** (`tests/test_outputs.py`): build agent source with `go build ./...`, drive the binary over
  subprocess with fresh per-test databases, and — using a Python codec that mirrors the mandated
  format — assert on-disk bytes, inject torn/corrupt/stale-temp files, and check recovery + exit codes.
  Coverage: core set/peek/allow/delete/scan, exit contract (missing peek → 3, deny → 3, corruption → 4),
  raw-byte ordering, half-open scan, refill math **including int64 overflow saturation**, byte-safe keys
  (spaces accepted, TAB/LF rejected), TAB-delimited batch (space-delimited rejected, blank = empty line
  only), log-structured `stats`/`compact`, crash recovery (torn tail dropped, mid-log corruption fatal,
  next write truncates torn bytes, stale `.compact.tmp` ignored), and a seeded randomized model.

- **Reference solution** (`solution/solve.sh`): writes a complete stdlib-only Go implementation —
  binary CRC-framed record log with recovery-aware replay, saturating refill arithmetic
  (`math/bits.Mul64`), append+truncate+fsync writes, temp+rename+fsync compaction — and builds it.

- **Environment** (`environment/Dockerfile`): `ubuntu:24.04` + `golang-go`; `/app/src` and `/app/data`
  created empty. No source shipped.

## Completion Rates

Validation **PASSING** (commit 42f285d): oracle 3/3, avocado (avocado_dvsc_tester) 2/5, gpt-5.5 0/5.
Structural 9/9, AI assessment Accept (0·0·2·0), contamination LOW, provenance pass.

## Model Analysis

The task keeps the from-scratch shape but replaces recallable, textbook requirements with a mandated
binary format and asymmetric crash-recovery rules that resist one-pass recall: a model must get the
exact CRC framing, the torn-tail-vs-mid-log-corruption asymmetry (with a distinct exit code),
int64-overflow saturation, and byte-safe key handling all correct simultaneously. Each is individually
easy to overlook and is tested independently, so a single missed corner fails the suite — strong models
(avocado 2/5) solve it some but not all of the time, while gpt-5.5 (0/5) does not.

## Anti-Cheating Analysis

- **Hardcoded outputs:** tests use randomized values and a 300-step seeded model; the on-disk-format
  tests assert exact bytes computed from inputs, not fixed literals.
- **Overfitting to visible tests:** the grader tests are not shipped in the container; the agent only
  sees `instruction.md` and an empty `/app/src`.
- **Modifying test files:** grading builds `/app/src` and runs the harness's own `tests/`; the agent
  cannot alter the verifier.
- **Bypassing the solution path:** tests drive the compiled binary end-to-end and decode the real log
  file, so a stub that only prints expected strings fails the format/recovery/stats assertions.
