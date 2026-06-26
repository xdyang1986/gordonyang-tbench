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
- **Cooldown windows** — independent `up_cooldown` / `down_cooldown` that suppress a
  same-direction action within the window (overriding even immediate scale-up) and compose
  with the stabilization window and rate limiter.
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

## Completion Rates (current — PASSING)

Commit `988bb30`. Platform validation **passes**: balance check *"avocado not trivial
and ≥1 agent solved."* Oracle 3/3, AI assessment **Accept** (0 Crit / 0 High / 2 Med / 0 Low),
contamination **LOW**, provenance SUSPECT (non-blocking).

| Model | Pass rate (k=5) |
|-------|-----------|
| Oracle | 3/3 (deterministic; full suite verified offline via Docker) |
| gpt-5.5 | **4/5** — mix |
| Avocado | **3/5** — non-trivial mix |
| Opus 4.6 | **0/5** — fails all |

The task discriminates across the whole tier: opus fails every trial, gpt-5.5 and avocado
are genuine mixes. **Caveat:** the avocado margin is variance-sensitive — it has come in at
both 3/5 and 5/5 across runs, so a future re-roll can flip the balance to "too easy"; the
cooldown + multi-gate interaction is what pulled it off a reliable 5/5.

## Why models fail (current)

All failures are on **stated** behavior — no unstated-convention traps. The difficulty comes
from composing several interacting gates correctly:

- **Cooldown windows (rule 6).** Separate `up_cooldown` / `down_cooldown` suppress a
  same-direction action within the window (overriding even the immediate-scale-up rule) and
  must compose with the down-window stabilization and the rate limiter. This is the main new
  differentiator — it pulled gpt-5.5 from 5/5 to a mix and avocado off a reliable 5/5.
- **Predictive sustained-trend gate (rule 5).** Pre-scale only on a clear sustained rise,
  scale-up only, off by default; models still occasionally pre-scale on a transient blip or
  under-scale on a ramp.
- **Reactive interactions.** Transient-dip absorption, geometric rate-limited scale-in,
  symmetric dead-band, exact ratio rounding, and bounds — each solvable alone, but the full
  multi-gate trajectory is where opus (0/5) and gpt slip.

The reactive control loop by itself is near a strong model's ceiling; the cross-model spread
(opus 0/5, gpt 4/5, avocado 3/5) comes from the cooldown + gate-precedence composition.

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
