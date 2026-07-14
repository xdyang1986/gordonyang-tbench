# codimango/unified-routing

## Task Overview

Build, from scratch in Go (standard library only), a command-line provider-routing tool `router` that
selects a provider per request **and** durably records every decision to a **crash-consistent binary
journal**, so an interrupted run can be restarted with `--resume` and continue exactly where it stopped
— without losing, duplicating, or corrupting decisions. The agent starts with an empty `/app/src`.

- `router --config <PATH> --requests <PATH> --journal <PATH> [--resume]`
- `--config` is JSON `{"strategy", "providers": [{"id","region","latency_ms","cost_per_1k","error_rate","capacity_rps","status"} ...]}`.
- `--requests` is newline-delimited JSON, one object per line with `id`, `user_region`, optional `sla_ms`.
- Prints one JSON value per request in order — the chosen provider id, or `null` when degraded.
- Exit `0` when every request routed · `1` when at least one decision is `null` · `2` on invalid input
  (bad config/requests, missing args, or a non-empty journal without `--resume`) · `3` on an
  unrecoverable corrupt journal. No stdout on exit 2/3.

## Routing model

For each request, in order: eligible providers are `status == up` and `capacity_rps > 0`; if `sla_ms` is
present keep those with `latency_ms <= sla_ms`. If none remain the decision is `null` (degraded).
Otherwise `effective_latency = latency_ms * 0.5` when `provider.region == user_region` else `latency_ms`,
and `score = effective_latency*w_lat + cost_per_1k*w_cost + error_rate*w_err` with weights by strategy
(`latency` 1/100/10000, `cost` 0.1/1000/10000, `balanced` 1/500/10000). The lowest score wins, ties
broken by lexicographically smallest id. The routing math is fully specified — the difficulty is the
durable log.

### What makes this hard

Difficulty comes from a **mandated binary journal format plus crash-recovery semantics** implemented in
Go's byte-level APIs — individually easy to get subtly wrong, each tested independently:

1. **Mandated on-disk framing.** 8-byte ASCII header `URJRNL01`, then one record per decision:
   `seq uint32 | id_len uint16 | id | status uint8 (0 routed / 1 null) | prov_len uint16 | prov |
   crc32 uint32`, all big-endian, CRC-32 (IEEE) over every preceding byte of the record. Tests assert
   the bytes on disk exactly. Each record is `fsync`'d before the next.
2. **Torn-tail recovery.** On `--resume`, a trailing record that is truncated or has a bad CRC is a
   crash mid-write: drop it and `Truncate` the file to the last valid record, then continue.
3. **Fatal-corruption distinction.** A bad header, a sequence gap, or a record whose id does not match
   the corresponding request is unrecoverable → exit `3` (not a recoverable tail).
4. **Idempotent resume.** Re-running `--resume` on a complete, consistent journal appends nothing and
   reprints the full decision list; resume from a partial journal appends only the missing records.
5. **Overwrite guard.** Running without `--resume` against a non-empty journal exits `2` untouched.

## Test / Solution Details

- **Tests** (`tests/test_outputs.py`): build the agent source with `go build ./...`, enforce
  stdlib-only (`go.mod` + import scan), then drive the built binary over `subprocess`. Using a Python
  codec that mirrors the mandated format, they assert on-disk bytes, inject torn/bad-CRC/corrupt
  journals to exercise recovery and exit codes, and cover fresh routing (region affinity, cost strategy,
  error-rate weight, status/capacity filtering, SLA degraded), blank-line handling, idempotent and
  partial resume, torn-tail and bad-CRC recovery, corruption (bad header / seq gap / id mismatch → 3),
  overwrite guard (→ 2), and the config/requests validation contract (→ 2).
- **Reference solution** (`solution/solve.sh`): writes a complete stdlib-only Go implementation
  (`encoding/json`, `encoding/binary`, `hash/crc32`, `os`, `flag`) — strategy-weighted routing, binary
  CRC-framed journal with fsync-per-record, recovery-aware replay with torn-tail truncation, and the
  0/1/2/3 exit contract — plus `go.mod`.
- **Environment** (`environment/Dockerfile`): `ubuntu:24.04` + `golang-go`; `/app/src` and `/app/data`
  created empty. No source shipped to the agent.

## Completion Rates

Latest validation run — **pending** (Go rewrite). Local: `go build` clean, reference passes 19/19 tests.

## Model Analysis

The routing logic is fully specified and easily implemented, so the difficulty concentrates in the
durable journal: exact big-endian CRC framing, the torn-tail-vs-fatal-corruption distinction with a
separate exit code, fsync durability, atomic truncation, and idempotent resume — expressed in Go's
manual byte-level APIs. Each corner is individually easy to overlook and is tested independently, so a
single missed corner fails the suite, which is what separates strong models from weaker ones.

## Anti-Cheating Analysis

- **Hardcoded outputs:** tests build and drive the real binary and assert on-disk bytes computed from
  inputs plus exit codes; no fixed literals to overfit.
- **Overfitting to visible tests:** grader tests are not shipped in the container; the agent sees only
  `instruction.md` and an empty `/app/src`.
- **Modifying test files:** grading builds `/app/src` and runs the harness's own `tests/`; the agent
  cannot alter the verifier.
- **Bypassing the solution path:** tests decode the real journal file and drive `--resume` recovery, so
  a stub that only prints strings fails the format, recovery, and exit-code assertions.
