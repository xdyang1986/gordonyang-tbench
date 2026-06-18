# codimango/autoscaling-in-go

## Description

The agent is given a Go module (`autoscaling`) implementing a CPU-utilization
autoscaler — a control loop that drives an instance fleet toward a target
average CPU. The repository ships **complete except for one file**:
`autoscaler/algorithm.go` is absent, so the package does not compile
(`engine.go` and `cmd/autoscalesim` reference `rawDesiredReplicas`, `stabilize`,
`limitScaleDownRate`, `clampInt`, and `predictiveFloor`, which are undefined).
The agent must create `autoscaler/algorithm.go` so the module builds and the
documented policy holds.

The task tests whether the agent can implement a small but subtle control
algorithm from a behavioral spec. The naive "scale to `ceil(current ×
cpu/target)`" reaction is easy; what is hard is the cluster of subtle, partly
interacting requirements that production autoscalers (e.g. the Kubernetes HPA)
need:

- **Scale up immediately** on a spike; **absorb a transient CPU dip** (a single
  low sample must not shrink the fleet — implemented by taking the *minimum*
  recommendation over the up-window and the *maximum* over the down-window).
- **Rate-limited scale-in**: remove at most `MaxScaleDownFraction` of the fleet
  per step, but always allow ≥1 removal so the fleet still converges.
- A **tolerance dead-band** and a **floating-point epsilon guard** so an exact
  integer multiple (e.g. `2.0000000000000004`) does not over-provision by one.
- **Predictive (seasonal) scale-up** (off by default): given a daily/weekly
  `Forecaster`, pre-provision for the **peak** forecast load over the lookahead
  window — **scale-up only**; the forecast must never scale the fleet *in*
  (scale-down stays reactive on actual CPU), must never exceed `MaxReplicas`,
  and must impose **no floor when disabled**.

A solution that only handles the common cases compiles and passes the easy
assertions but fails the transient-dip, gradual-scale-in, FP-epsilon, and
predictive cases in the hidden suite.

## Completion Rates

Empirical pass rates (out of K=5 trials; oracle K=3). The configuration measured
here is the final submitted task.

| Model | Agent | Trials | Pass rate |
|-------|-------|--------|-----------|
| Oracle | `oracle` | 3 | **3/3 (1.00)** |
| Sonnet 4.6 | `claude-code` | 5 | 4/5 (0.80) |
| Opus 4.6 | `claude-code` | 5 | **3/5 (0.60)** |
| Avocado | `metacode` | 5 | **2/5 (0.40)** |

> Calibration target met: **both** Opus and Avocado pass at least once **and**
> fail at least once out of 5. Sonnet is informational only.

## Model Analysis

**Oracle — 3/3 passed.** Deterministic; confirms the task is solvable and the
harness is non-flaky.

**Opus 4.6 — 3/5 passed, 2/5 failed.** Both failures were the predictive
**off-switch**: with prediction configured but `PredictionStep <= 0` (disabled),
the implementation still returned a peak-derived floor of `20`, which would scale
the fleet out while the feature is off (`predictive_test.go:91: disabled
prediction must impose no floor (<= 1), got 20`). Opus guarded `Forecast == nil`
and `lookahead <= 0` but missed the `step <= 0` disable condition.

**Avocado — 2/5 passed, 3/5 failed.** Same failure mode as Opus — the `step <= 0`
disable condition returning a floor of `20` instead of none. Avocado's solution
varied across trials (some correctly imposed no floor when disabled, some did
not), which is exactly the pass/fail mix calibration needs.

**Sonnet 4.6 (informational) — 4/5 passed, 1/5 failed.** The single failure was
the **scale-down rate limiter**: it removed more than `MaxScaleDownFraction` of
the fleet in a step (`TestLimitScaleDownRate`, `TestGradualScaleDown`) and the
closed-loop trajectory diverged (`TestClosedLoopWorkload`).

**Dominant failure modes across all models:**

| Failure mode | Count | Tests |
|---|---|---|
| Incomplete predictive off-switch (returns a scaling floor when disabled) | 5 (Opus 2, Avocado 3) | `TestPredictiveFloorDisabled` |
| Scale-down rate limiter (per-step removal bound) | 1 (Sonnet 1) | `TestLimitScaleDownRate`, `TestGradualScaleDown`, `TestClosedLoopWorkload` |

**Why these are reasoning gaps, not task-setup issues.** The oracle passes 3/3
and every model passes a majority of trials on the identical harness, so the
environment is sound. The failures are specific incorrect logic in the agent's
own `algorithm.go`: returning a non-trivial predictive floor while the feature is
disabled (which would over-provision a fleet whose prediction is off), and
mis-bounding per-step scale-in. Both are exercised by hidden tests that call the
functions directly and by a closed-loop simulation, so they reflect the model's
control-logic correctness, not prompt ambiguity or harness flakiness. (The
disabled-path test asserts the behaviorally-meaningful property "imposes no
upward floor," so a correct implementation that returns `0` *or* `MinReplicas`
passes — only a solution that would actually scale out while off fails.)

## Anti-Cheating Analysis

- **Hardcoded outputs:** The hidden Go suite calls the agent's functions
  directly across many input combinations (FakeClock-driven scenarios, a pure
  rate-limiter truth table, a `predictiveFloor` truth table, and two closed-loop
  simulations where CPU is a function of the live fleet). There is no fixed
  output string to print; only real control logic satisfies the assertions.
- **Overfitting to visible tests:** No test files ship in the container. The
  hidden unit + behavioral + predictive suites are copied in only at verify time
  and cover more cases (transient-dip hold, gradual/rate-limited scale-in,
  dead-band, FP-epsilon, predictive peak-over-window, scale-up-only,
  off-by-default) than the prompt enumerates, so matching the prompt's examples
  is insufficient.
- **Modifying test files:** Any `*_test.go` the agent leaves in the package is
  deleted before the authoritative suites are copied in, so the agent cannot
  weaken or replace the tests.
- **Bypassing the intended solution path:** Before grading, the verifier restores
  pristine copies of every non-implementation file (`engine.go`, `types.go`,
  `clock.go`, `cmd/autoscalesim/main.go`, `go.mod`) over `/app`. Only the agent's
  `autoscaler/algorithm.go` survives, so editing the engine, the simulation, the
  config, or the harness to fake a pass has no effect — the graded behavior must
  come from a correct `algorithm.go` that satisfies the original function
  contracts.
