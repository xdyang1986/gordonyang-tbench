# codimango/rate-limit

## Task Overview

Build, from scratch in Go, a persistent log-structured token-bucket rate limiter exposed as a
command-line tool `rlctl`. The agent starts with an empty `/app/src` and must
implement the full CLI:

- `set <KEY> <CAPACITY> <REFILL>` / `peek <KEY> <TS>` / `delete <KEY>`
- `allow <KEY> <TOKENS> <TIMESTAMP_MS>` — consumes tokens or denies, deterministic refill math
- `scan [START] [END]` — buckets in ascending key order (range half-open `[START, END)`)
- `batch` — apply stream of `set`/`delete`/`allow` from stdin as single all-or-nothing unit
- `stats` — print `live=<L>\tdead=<D>`: live buckets vs dead (superseded/tombstoned) records
- `compact` — reclaim dead records (`dead → 0`) without changing peek/scan

State persists durably across separate process invocations. Standard library only.

**This is a log-structured store, not a map dump.** `set`/`delete`/`allow` append a record to a log,
and a `delete` always writes a tombstone even for absent key — exit 0 but adds dead record.
Current state is replay of log last record per key wins. Superseded sets and successful allows and
tombstones remain as **dead** records until `compact` rewrites log down to one live record per present bucket.

This log-structured contract is the point: `stats` dead count and `compact` **cannot be produced by
plain in-memory map** that only tracks current state, so solution recalling textbook rate limiter fails.
It mirrors database-engine shape but reframed to rate limiting domain.

Token bucket uses integer arithmetic: available = min(capacity, tokens + refill * delta_ms / 1000).
On set tokens reset to capacity last=0. On allow success tokens decrease and last advances; on deny
no record appended and state unchanged. Peek computes available without mutation. Batch aborts whole
unit on malformed line or denied allow.

## Test / Solution Details

- **Tests** (`tests/test_outputs.py`): builds agent source with `go build ./...`, drives binary over
subprocess with fresh per-test databases. Coverage includes core set/peek/allow/delete/scan,
exit-code contract (missing peek → exit 3, deny → exit 3), bytewise ordering, half-open scan,
refill math, batch success / blank handling / atomic rollback on malformed or denied allow,
parent-directory creation, cross-process persistence, stdlib-only check, log-structured
`stats`/`compact` contract (overwrites, allows, tombstones accumulate dead; delete absent still
adds dead; compact reclaims and durable), and seeded randomized model tracking both state and dead count.
Negative tests paired with positive state checks.

- **Reference solution** (`solution/solve.sh`): writes complete stdlib-only Go implementation —
append-only record log `S` for set/allow state and `D` for tombstone with replay, append+fsync writes,
temp+rename+fsync compact — and builds it.

- **Environment** (`environment/Dockerfile`): `ubuntu:24.04` + `golang-go`; `/app/src` and `/app/data`
created empty. No source shipped.

## Completion Rates

TBD — new task awaiting first validation run.

## Model Analysis

Difficulty comes from log-structured contract plus deterministic token bucket arithmetic across
explicit timestamps. `stats` must report dead superseded records, `compact` must reclaim them while
preserving peek output for any timestamp, which requires retaining more than current bucket map.
A solution recalling standard in-memory rate limiter cannot produce dead count or correct compact.
Batch all-or-nothing with allow-deny abort adds further state-machine complexity beyond textbook KV.
