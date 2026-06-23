# codimango/traffic-throttle

## Description

The agent must implement a **concurrent, health-aware traffic throttler** in Go.
It is a token-bucket rate limiter with two enforcement modes (a non-blocking
`Allow`/`AllowN` for load shedding and a blocking `Wait`/`WaitN` for pacing),
**separate read/write limits per service**, a refill rate that scales with a
per-service health score in `[0,1]`, and — the hardest part — a **per-service
aggregate limit on top of the per-op limits**: every operation must take tokens
from **both** its per-op bucket **and** the service's aggregate bucket
**atomically**, and if either is short, **neither is consumed** (no partial
consumption / rollback). Services may be added or reconfigured at runtime
(`Register`), health is re-evaluated by a background poller (or on demand via
`Refresh`) and **cached** between evaluations, the clock is injectable for
deterministic behavior, and an unconfigured service must fail open.

A naive approach fails on several independent axes:

- **Atomic two-bucket consumption.** The per-op and aggregate buckets must be
  checked-and-debited as one atomic unit under contention. A check-then-act or
  per-bucket-locked implementation over-consumes (the grading test sees thousands
  of successes where only the aggregate's worth should pass) and/or leaves the
  two buckets inconsistent after a partial take.
- **Health-scaled refill + caching.** Refill must be `Rate × health`, and health
  must be read into a **cache** (updated by the poller or `Refresh()`), not read
  live on every call — otherwise `Refresh()` is meaningless and the documented
  `PollInterval=0` mode breaks.
- **Concurrency correctness.** All bucket and map mutations run under the Go race
  detector; an unsynchronized token update or service map fails immediately.
- **Blocking-with-cancellation, burst cap, and fail-open** edge cases.

## Completion Rates

Out of K trials (reward 1.0 = all 17 grading behaviors pass). Calibration target
is Opus/Avocado; both pass ≥1 and fail ≥1.

| Model | Pass rate | Notes |
|-------|-----------|-------|
| Oracle | **3/3** (1.000) | Reference solution; non-flaky, race-clean |
| Sonnet 4.6 | 5/5 (1.000) | Informational only (not part of validation) |
| **Opus 4.6** | **3/5** (0.600) | Calibration target — genuine failures |
| **Avocado** | **2/5** (0.400) | Calibration target — genuine failures |

## Model Analysis

**Opus 4.6 — 3/5 passed, 2/5 failed.** Both failures are reasoning gaps in the
**health caching/scaling** model (the aggregate logic was implemented correctly —
`test_aggregate_rollback` passed in the failing trials):
- 1 trial failed `test_health_scales_refill` + `test_refresh_updates_cached_health`
  — refill was not scaled by the health factor and/or health was read live instead
  of from the cache (assertion: *"health change must not take effect until
  Refresh() is called"*).
- 1 trial failed `test_health_scales_refill` + `test_health_zero_blocks_and_ctx`
  + `test_refresh_updates_cached_health` — same health-model misunderstanding plus
  the health-0 blocking path.

**Avocado — 2/5 passed, 3/5 failed.** All 3 failures are **concurrency-correctness**
gaps on the atomic multi-bucket take:
- All 3 failed `test_concurrent_allow_exact` **and** `test_aggregate_concurrent`
  with massive over-consumption — *"expected exactly 500 successful takes, got
  2000"* and *"aggregate must cap total successes at 300, got 2000"*. The
  implementation lets effectively every concurrent caller through, i.e. the
  token check-and-debit is not atomic under contention.

**Sonnet 4.6 — 5/5 passed (informational).** Solved every trial; not a validation
signal, included for reference.

**Dominant failure modes (across all 5 genuine failures):**
1. **Concurrency correctness — 3 failures** (Avocado): non-atomic check-and-debit
   of the per-op + aggregate buckets under load → unbounded over-consumption.
2. **Health caching/scaling — 2 failures** (Opus): refill not scaled by health,
   and/or health read live instead of from the cache that `Refresh()`/the poller
   maintain.

These are **reasoning gaps, not task-setup issues**: the oracle passes 3/3 and the
suite is race-clean; failing trials compiled and ran but produced wrong runtime
behavior (wrong counts under concurrency, wrong refill/caching semantics). The two
models fail on **different** axes, which is strong evidence the task discriminates
on understanding rather than on a single brittle check.

## Anti-Cheating Analysis

- **Hardcoded outputs:** Impossible — the grading test is compiled *into* the
  candidate's own Go package and calls their real `New`/`Allow`/`Wait`/etc.;
  results come from executing their code. Concurrency tests assert exact success
  counts derived from the candidate's own buckets, so a constant can't satisfy them.
- **Overfitting to visible tests:** The grader is not the candidate's tests — any
  `*_test.go` the candidate writes is renamed aside before grading, and a hidden
  internal grading test is injected at verify time. It also runs under `-race`,
  which rejects shortcut implementations.
- **Modifying test files:** Test files live under `tests/`, copied in only at
  verify time (hidden during the trajectory in TBR); the harness uses its own copy.
- **Bypassing the intended solution path:** There is no oracle/answer key on disk
  in the verify environment. The injected test pins the API names/semantics from
  `instruction.md`, and correctness under concurrency + the atomic-rollback
  requirement + the race detector force a genuinely synchronized, health-aware
  token-bucket implementation.
