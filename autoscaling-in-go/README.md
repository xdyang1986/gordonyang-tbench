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
- **Predictive scale-up** (`predict_lookahead`, off by default): pre-scale up
  ahead of demand **only on a clear sustained upward trend** (a transient blip
  must not trigger it), **scale-up only** (never predicts a scale-down). Specified
  at the scenario level, so the *gate* — not a fixed formula — is what's graded
  (directional tests); this is the task's main differentiator across models.
- **Strict, deterministic I/O**: exact CSV format, 2-decimal CPU echo, byte-iden­
  tical output, fail-fast input validation.

Grading is **black-box on stdout** (the verifier builds and runs the agent's own
program on crafted workloads), so there is no internal-API crutch.

## Completion Rates

Empirical pass rates (K=5 trials; oracle K=3) on the redesigned task, run via
`codimango bench run` (Docker harness).

| Model | Agent | Role | Trials | Pass rate |
|-------|-------|------|--------|-----------|
| Oracle | `oracle` | validation-gate | 3 | **3/3 (1.00)** |
| Avocado | `metacode` | validation-gate | 5 | **4/5 (0.80)** |
| Opus 4.6 | `claude-code` | data-collection | 5 | **1/5 (0.20)** |

> Gate met: oracle passes; Avocado is **below** the 5/5 too-easy threshold.
> ⚠️ The Opus number is **contaminated by agent timeouts**: 3 of its 5 trials hit
> `AgentTimeoutError` (600 s agent budget), not logic failures. On the 2 trials
> that completed, Opus is 1 pass / 1 logic-fail. Treat Opus as **needs a clean
> re-run** with a larger `agent.timeout_sec`, not as a settled 1/5.
> (`mini-swe-agent` is platform-side only and not runnable via local `bench run`.)

## Model Analysis

**Oracle — 3/3.** Deterministic; confirms solvability and a non-flaky harness end
to end (Dockerfile build → agent program → black-box verifier).

**Avocado — 4/5.** Implements the reactive control loop and all exact edge cases
correctly on every trial: ratio rule (incl. exact-integer multiples), scale-up /
rate-limit asymmetry, geometric scale-in, clamp + action, the symmetric tolerance
dead-band, and input validation. Its single failure was the **predictive
sustained-trend gate** (`test_predictive_ignores_transient_blips`): on a choppy /
zigzag workload it pre-scaled on a non-sustained up-tick instead of holding. That
is the one behavior near the edge of a strong model's reliability, and it fails
only intermittently — so 4/5 sits just under the too-easy gate and should be read
as **marginal** (a re-run can land 5/5).

**Opus 4.6 — 1/5, but mostly timeouts, not logic.** Of five trials: one clean
21/21 pass; one **genuine logic failure** (`test_predictive_prescales_on_sustained_rise`
— predictive under-scaled on a sustained ramp); and **three `AgentTimeoutError`**
(600 s agent budget) — two left `/app` empty (no program written) and one wrote a
partial program and timed out mid-implementation (its 9 test failures are an
incomplete program, not wrong logic). On the two trials that completed, Opus is
1 pass / 1 logic-fail, so the headline 1/5 understates it and is **not a reliable
difficulty signal** until re-run with a larger agent timeout.

**Failure modes (this run), by legitimacy:**

| Failure mode | Count | Legitimate? |
|---|---|---|
| Agent timeout — 600 s budget (empty or partial `/app`) | 3 (Opus) | ❌ infra/budget artifact |
| Predictive under-scales on a sustained ramp | 1 (Opus) | ✅ logic |
| Predictive pre-scales on a transient blip | 1 (Avocado) | ✅ logic |

**Honest calibration note.** Once input validation was made explicit, Avocado
solved the fully-specified reactive logic and all exact edge cases on every trial
— a fairly-specified CPU autoscaler is close to a strong model's ceiling. The only
**legitimate** difficulty signals observed are the two predictive sustained-trend
gate failures (Avocado 1, Opus 1); everything else was the reactive math (solved)
or agent timeouts. The validation-gate margin (Avocado 4/5) is thin and rests
almost entirely on that predictive gate, and the Opus data-collection number needs
a clean re-run (raise `agent.timeout_sec`). Treat this as a **provisional medium**:
the discriminator is real but narrow, and the cross-model signal is not yet clean.

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
