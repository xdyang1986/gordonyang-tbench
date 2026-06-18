# codimango/autoscaling-in-go

## Description

The agent must write a **complete Go program from scratch** at `/app` (standard
library only) that implements a CPU-utilization autoscaler. The program reads a
workload trace on **stdin** — a config line plus one CPU sample per tick — and
writes the per-tick scaling decisions to **stdout** as CSV (`tick,cpu,replicas,
action`). Nothing is scaffolded: there is no starter code, no interfaces, and no
tests visible in the container — only `instruction.md`.

The naive reading ("scale to `ceil(current × cpu / target)`") is easy; the task
is hard because a correct controller must combine several subtle, partly
interacting behaviors with a precise, deterministic I/O contract:

- **Immediate scale-up** on a spike, but **transient-dip absorption** — a single
  low sample must not scale the fleet in; scale-in only after low demand persists
  through the scale-down stabilization window.
- **Rate-limited scale-in**: remove at most `max_scale_down_frac` of the fleet
  per tick (always ≥1 so the fleet still converges).
- A **symmetric tolerance dead-band** around the target (suppresses small
  *scale-ups* as well as scale-downs) and bounds `[min,max]`.
- **Strict, deterministic I/O**: exact CSV format, 2-decimal CPU echo, byte-iden­
  tical output across runs, and fail-fast validation (malformed/out-of-range
  input → non-zero exit).

Because grading is **black-box on stdout** (the verifier builds and runs the
agent's own program on crafted workloads), the agent gets no internal-API crutch:
it must get both the control logic and the output contract right.

## Completion Rates

Empirical pass rates (out of K=5 trials; oracle K=3) on the final task.

| Model | Agent | Trials | Pass rate |
|-------|-------|--------|-----------|
| Oracle | `oracle` | 3 | **3/3 (1.00)** |
| Sonnet 4.6 | `claude-code` | 5 | 4/5 (0.80) |
| Opus 4.6 | `claude-code` | 5 | **2/5 (0.40)** |
| Avocado | `metacode` | 5 | **5/5 (1.00)** |

> Calibration target met: **Opus** passes at least once **and** fails at least
> once out of 5. Sonnet is informational only.

## Model Analysis

**Oracle — 3/3 passed.** Deterministic; confirms the task is solvable and the
harness is non-flaky.

**Opus 4.6 — 2/5 passed, 3/5 failed.** All three failures were
`test_tolerance_dead_band`: given `cpu=0.63` at `target=0.60` with
`tolerance=0.10` (ratio 1.05, comfortably inside the ±10% band), the program
scaled **up** to 6 (`ceil(5×0.63/0.6)`) instead of holding at 5. Opus applied the
dead-band only on the scale-down side and missed its **symmetric** nature — a
genuine control-logic gap, not a harness issue (the other 11 behaviors passed).

**Avocado — 5/5 passed.** Implemented the full contract correctly every trial.

**Sonnet 4.6 (informational) — 4/5 passed, 1/5 failed.** The single failure was a
program that did not compile (`go build` error); the other four implemented the
contract correctly.

**Dominant failure modes across models:**

| Failure mode | Count | Test(s) |
|---|---|---|
| Asymmetric tolerance dead-band (scales up inside the band) | 3 (Opus) | `test_tolerance_dead_band` |
| Non-compiling solution | 1 (Sonnet) | build step |

**Why these reflect reasoning gaps, not task-setup issues.** The oracle passes
3/3 and most model trials pass on the identical harness, so the environment is
sound. The dominant failure is specific incorrect control logic in the agent's
own program — applying the tolerance dead-band on only one side, which causes
thrash the spec explicitly says to avoid. The scale-in tests assert
*asymptotic* behavior (dip held; sustained low eventually drains) and the rate
limiter via a per-step invariant, so they pass under any reasonable boundary
convention — failures there would reflect a genuinely non-converging controller,
not a convention mismatch.

## Anti-Cheating Analysis

- **Hardcoded outputs:** The verifier drives the agent's compiled program with
  many distinct workloads (spike, transient dip, sustained drop, gradual descent,
  tolerance, bounds, validation) and asserts on computed replica trajectories —
  there is no single fixed output to print.
- **Overfitting to visible tests:** No test files ship in the container; the
  suite is applied only at verify time and covers more scenarios than
  `instruction.md` enumerates by example.
- **Modifying test files:** Tests live outside `/app` and are supplied by the
  verifier; the agent cannot see or alter them.
- **Bypassing the intended solution path:** Grading is black-box on stdout from
  the agent's own compiled program (built from `/app`, any layout), driven on
  fresh inputs. No code ships in the container and no oracle is present at solve
  time, so the only way to pass is to implement the control logic and the I/O
  contract correctly.
