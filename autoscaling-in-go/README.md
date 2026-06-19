# codimango/autoscaling-in-go

## Description

The agent must write a **complete Go program from scratch** at `/app` (standard
library only) that implements a CPU-utilization autoscaler. The program reads a
workload trace on **stdin** — a first line of space-separated `key=value` config,
then one CPU sample per tick — and writes the per-tick scaling decisions to
**stdout** as CSV (`tick,cpu,replicas,action`). Nothing is scaffolded: there is
no starter code, no interfaces, and no tests in the container — only
`instruction.md`.

The naive "scale to `ceil(current × cpu / target)`" reaction is easy; the task is
hard because a correct controller must combine several subtle, partly interacting
behaviors and a precise, deterministic I/O contract:

- **Immediate scale-up** on a spike, but **transient-dip absorption** (a single
  low sample must not scale in; scale-in only after sustained low through the
  stabilization window).
- **Rate-limited scale-in** (≤ `max_scale_down_frac` of the fleet per tick, ≥1).
- A **symmetric tolerance dead-band** around the target (suppresses small
  scale-*ups* too, not just scale-downs) and bounds `[min,max]`.
- **Predictive (trend-based) scale-up** (`predict_lookahead`, off by default):
  extrapolate the recent observed CPU trend forward and **pre-scale up** for the
  projected demand — **scale-up only** (never predicts a scale-down). This is a
  bespoke heuristic, not stock HPA or stock seasonal forecasting.
- **Strict, deterministic I/O**: exact CSV format, 2-decimal CPU echo, byte-iden­
  tical output, fail-fast input validation.

Grading is **black-box on stdout** (the verifier builds and runs the agent's own
program on crafted workloads), so there is no internal-API crutch.

## Completion Rates

Empirical pass rates (out of K=5 trials; oracle K=3) on the final task.

| Model | Agent | Trials | Pass rate |
|-------|-------|--------|-----------|
| Oracle | `oracle` | 3 | **3/3 (1.00)** |
| Sonnet 4.6 | `claude-code` | 5 | 2/5 (0.40) |
| Opus 4.6 | `claude-code` | 5 | **3/5 (0.60)** |
| Avocado | `metacode` | 5 | **3/5 (0.60)** |

> Calibration target met: **both** Opus and Avocado pass at least once and fail
> at least once out of 5.

## Model Analysis

**Oracle — 3/3 passed.** Deterministic; confirms solvability and a non-flaky harness.

**Opus 4.6 — 3/5 passed, 2/5 failed.** Both failures were `test_tolerance_dead_band`:
given `cpu=0.63` at `target=0.60`, `tolerance=0.10` (ratio 1.05, inside the ±10%
band), the program scaled **up** instead of holding — it applied the dead-band
only on the scale-down side and missed its **symmetric** nature. The predictive,
reactive, rate-limit, and validation behaviors all passed (no config-parse
errors).

**Avocado — 3/5 passed, 2/5 failed.** Identical failure mode (`test_tolerance_dead_band`),
same root cause (asymmetric dead-band). Predictive feature implemented correctly
in passing trials.

**Sonnet 4.6 (informational) — 2/5 passed, 3/5 failed.** One failure was the
rate limiter (`test_rate_limited_scale_in_is_gradual`); two were non-compiling
programs (`go build` errors). No config-parse failures.

**Dominant failure modes across models:**

| Failure mode | Count | Test |
|---|---|---|
| Asymmetric tolerance dead-band (scales up inside the band) | 4 (Opus 2, Avocado 2) | `test_tolerance_dead_band` |
| Rate-limiter math | 1 (Sonnet) | `test_rate_limited_scale_in_is_gradual` |
| Non-compiling program | 2 (Sonnet) | build step |

**Why this is a reasoning gap, not a task-setup issue.** The oracle passes 3/3 and
both targets pass the majority of trials on the identical harness; the only
failures are a specific control-logic error (applying the dead-band on one side
only, which causes the thrash the spec says to avoid). The predictive tests are
**directional** (a rising trend must pre-scale earlier than the reactive baseline;
a falling trend must never pre-scale *in*), so they pass under any reasonable
trend-extrapolation, and scale-in tests are asymptotic — no exact-tick or
formula brittleness.

## Anti-Cheating Analysis

- **Hardcoded outputs:** The verifier drives the agent's compiled program with many
  distinct workloads (spike, transient dip, sustained drop, gradual descent,
  tolerance, bounds, rising/falling trends, validation) and asserts on computed
  trajectories — there is no fixed output to print.
- **Overfitting to visible tests:** No test files ship in the container; the suite
  runs only at verify time and covers more scenarios than `instruction.md`
  enumerates by example.
- **Modifying test files:** Tests live outside `/app` and are supplied by the
  verifier; the agent cannot see or alter them.
- **Bypassing the intended solution path:** Grading is black-box on stdout from the
  agent's own compiled program (built from `/app`, any layout) on fresh inputs. No
  code ships in the container and no oracle is present at solve time, so the only
  way to pass is to implement the control logic, the predictive feature, and the
  I/O contract correctly.
