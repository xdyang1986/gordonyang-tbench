# codimango/node-down-detection

A terminal-bench task. The agent implements a Go library that estimates the
**probability that a cluster node is down** — robust to single-region network
partitions — rather than returning a brittle up/down boolean.

## Description

The agent must implement the `failuredetector` Go package inside a pre-created
module (`github.com/example/nodedown`), exposing the exact API given in
`instruction.md`. The library has two layers:

1. **Phi accrual failure detector** (`PhiDetector`): per `(observer, target)`
   link, reports an adaptive suspicion level `phi` from the observed distribution
   of heartbeat inter-arrival times, and maps it to a down-probability
   (`P = 1 − 10^(−phi)`). Because `phi` is computed against the *observed* mean
   and standard deviation of intervals, the detector adapts to real network
   jitter instead of tripping on a fixed timeout.
2. **Region-aware aggregation** (`Cluster`): **averages** observer probabilities
   *within* a region (observers there share fate — same switches/links) and
   **multiplies** them *across* regions (independent vantage points), so a node
   is condemned only when every region agrees. One healthy region drives the
   cluster verdict toward 0.

The task tests probabilistic reasoning and distributed-systems judgement, not
boilerplate. A naive approach fails: a fixed up/down timeout fails the
jitter-tolerance and smooth-increase requirements, and a region-blind
cluster-wide average fails the partition test — averaging a fully-suspect region
with a healthy one yields ≈0.5, condemning a node that is actually alive. The
agent must discover that cross-region evidence has to combine multiplicatively
(or min-like) so a single healthy vantage point dominates, while within-region
observers are correlated and should be averaged.

## Completion Rates

Empirical pass rates (local `codimango bench run`). _Calibration target: Opus or
Avocado must pass ≥1 and fail ≥1 out of 5 — **met by both**._

| Model | Agent | Pass rate | Notes |
|---|---|---|---|
| Oracle | `oracle` | 3/3 (100%) | reference solution; also green under `go test -race` |
| Avocado | `metacode` | ~20% (5/25 pooled; runs of 0/5, 4/10, 1/10) | all failures on the cross-region aggregation |
| Opus 4.6 | `claude-code` | 2/10 (20%) | failures: aggregation + API-signature build slips |
| Sonnet 4.6 | `claude-code` | 1/5 (~15%) | informational only |

Both calibration targets (Opus, Avocado) pass at least once and fail at least
once, with pass rates clustered around ~20% — differentiation driven by genuine
reasoning, not setup noise.

## Model Analysis

**Avocado — ~20% (5/25 across runs; e.g. 4/10 then 1/10).** Every failing trial
failed `TestClusterPartitionDoesNotCondemn`: the model returned a cluster-wide
down-probability of **0.5** (region-blind averaging — averaging one fully-silent
region with one healthy region) or **1.0** (an aggregation that lets any silent
region condemn the node), where ≤0.2 is required. Zero panics, zero build
failures, zero unfair-threshold rejections. Avocado reliably produces a
compiling, correct *phi* layer but does not derive that cross-region evidence
must combine so one healthy region dominates.

**Opus 4.6 — 2/10 (20%).** Two distinct failure modes: (a) the same
`TestClusterPartitionDoesNotCondemn` aggregation error (legitimate reasoning
gap); (b) build failures from deviating from the required exported API
signatures. No panics (the bootstrap-seeding hint in the `Config` doc comment
resolved the earlier empty-window crashes). Opus passes when it both implements
the API exactly and derives the region-aware multiply rule.

**Sonnet 4.6 — 1/5 (informational).** Mixed failures spanning the partition test
and structural slips; consistent with the ~15–20% band of the stronger models.

**Dominant failure mode across all models:** `TestClusterPartitionDoesNotCondemn`
(region-aware aggregation) — the single largest bucket of failures on every
model. This is the task's core novel requirement: a node still reachable from any
one region must stay low-probability. The secondary mode (Opus) is exact API
conformance.

**Why these are reasoning gaps, not task-setup issues:** the reference solution
passes 3/3 (and under `-race`), the failure is the *same* well-defined assertion
across independent trials and models, and the wrong answers (0.5 from averaging,
1.0 from a condemning combinator) are exactly the predicted naive approaches. The
task spec states the region-aware requirement but deliberately does not prescribe
the aggregation rule (average-within / multiply-across), so models must discover
it — and most do not.

## Anti-Cheating Analysis

- **Hardcoded outputs:** Tests synthesize all heartbeat data in-code and call the
  agent's exported API directly via `go test`; there is no fixture file to copy
  and no literal expected-output file to echo. Probabilities are checked under
  several distinct scenarios (healthy, all-silent, single-region partition,
  unknown target, jitter) whose required bounds — healthy ≤0.1, silent ≥0.99,
  partition EU ≥0.9 ∧ US ≤0.1 ∧ node ≤0.2 — cannot all be satisfied by any single
  constant.
- **Overfitting to visible tests:** The grader (`tests/detector_test.go`) is
  delivered at run time (`test.sh` copies it into the package) and is hidden from
  the agent during the trajectory; it is a black-box external `_test` package
  that exercises only the public API, so no internal symbol can be special-cased.
- **Modifying test files:** The test is mounted by the verifier at grading time
  into `/app/failuredetector/detector_test.go`, overwriting anything the agent
  may have placed there; the agent's working tree cannot influence the assertions
  that run.
- **Bypassing the intended solution path:** `go test` compiles and executes the
  agent's actual package — a non-running or empty implementation fails to compile
  or returns wrong probabilities. The partition test forces genuine region-aware
  aggregation, and the `-race` run plus concurrent Heartbeat/Status workload
  forces a real synchronization implementation; a non-thread-safe shortcut is
  caught as a data race.

## Layout

- `instruction.md` — the task given to the agent (the spec + exported API).
- `environment/Dockerfile` — `golang:1.26-bookworm` image with the
  `github.com/example/nodedown` module pre-created at `/app`; the agent fills in
  `/app/failuredetector/`.
- `solution/solve.sh` — oracle: writes the reference `failuredetector` package.
- `tests/detector_test.go` — black-box behavioral tests (external `_test`
  package, exported API only), including a `-race` concurrency check.
- `tests/test.sh` — copies the test into the package, runs `go test -race`,
  writes the reward.

## Grading

The verifier runs `go test ./failuredetector/ -race`. All behavioral properties
are checked through the public API, so any correct implementation passes
regardless of internal design. Reward is `1` iff every test passes.
