# codimango/unified-routing

## Task Overview

Build, from scratch in Python (standard library only), a command-line provider-routing layer
`router`. The agent starts with an empty `/app/src` and must implement the full CLI:

- `router --config <PATH> --requests <PATH>`
- `--config` is a JSON document `{"strategy", "max_replicas", "tenant_budgets", "providers": [...]}`
  where each provider has `id`, `region`, `latency_ms`, `cost_per_1k`, `error_rate`, `capacity_rps`,
  `status` (`up`/`down`), and `health` (a float in `(0,1]`).
- `--requests` is newline-delimited JSON, one request per line with `id`, `user_region`, optional
  `sla_ms`, optional `priority` (`high`/`normal`/`low`), and optional `tenant`.
- Prints one JSON array per request, in input order — the ordered, region-diverse failover chain of up
  to `max_replicas` distinct provider ids, or `[]`.
- Exit `0` when every request received exactly `max_replicas` providers · `1` when at least one request
  is degraded (fewer than `max_replicas`, including empty) · `2` on invalid input (bad config/requests,
  unknown strategy, `max_replicas < 1`, duplicate/empty id, out-of-range numeric fields, unrecognized
  status/priority) — no output on exit 2.

## Routing model

State is maintained **per request file, in order**: each provider has a remaining capacity initialized
from `capacity_rps`, and each tenant a remaining budget from `tenant_budgets` (infinite if unlisted).
For each request in turn:

1. **Eligibility** — `status == up`, remaining capacity `> 0`, `health > 0`, and the tenant can afford
   the provider's per-request cost (`cost_per_1k / 1000`); then, if `sla_ms` is present,
   `latency_ms <= sla_ms`.
2. **Scoring** — base weights come from `strategy` (`latency` / `cost` / `balanced`), adjusted by a
   `priority` multiplier (`high`: w_lat×2.0, w_cost×0.5; `low`: the inverse; `normal`: unchanged). A
   region-affinity multiplier is applied to latency (strongest for an exact `region == user_region`
   match, moderate for the same continent, none otherwise), and the final score is
   `(effective_latency*w_lat + cost_per_1k*w_cost + error_rate*w_err) / health` (lower is better).
3. **Selection** — sort ascending by score with a deterministic multi-level tie-break, then choose a
   region-diverse chain up to `max_replicas` (first pass: one provider per not-yet-used region; second
   pass: fill remaining slots by score).
4. **Consumption** — decrement the primary (first-chosen) provider's capacity by 1 and deduct its
   per-request cost from the tenant's budget.

The instruction states the strategy weights, priority multipliers, eligibility rules, region-diverse
selection, and exit contract precisely, but deliberately leaves two things qualitative:

1. **Region-affinity magnitudes / application.** The instruction says an exact match gets the
   strongest bonus, same-continent a moderate one, else none — without pinning the exact multipliers or
   whether the bonus is multiplicative. A solution that applies affinity inconsistently computes
   different scores and breaks score-ties the wrong way.
2. **Multi-level tie-break order.** The instruction names the tie-break keys (health, cost, latency, id)
   but not their direction, so an implementation must reason out the stable ordering that reproduces the
   intended winner.

## Test / Solution Details

- **Tests** (`tests/test_outputs.py`): drive the `router` binary over `subprocess` with configs and
  request streams built in Python, asserting exact provider chains and exit codes. Coverage spans
  region affinity (exact / same-continent / none), cost strategy, priority high-vs-low trade-off,
  tenant-budget enforcement, **multi-tenant budget independence**, stateful capacity exhaustion and
  spillover across providers, region-diverse `max_replicas` chains, the multi-level tie-break,
  SLA filtering and degraded exit, `status: down` exclusion, blank/whitespace line handling,
  `payload_kb` being ignored, default-tenant unlimited budget, the exit-0 fully-routed path, and the
  exit-2 validation contract (duplicate/empty id, negative latency/capacity, out-of-range
  error_rate/health, `max_replicas < 1`, unrecognized status/priority).
- **Reference solution** (`solution/solve.sh`): writes a complete stdlib-only Python implementation —
  strategy/priority weighting, prefix-based continent affinity, health-divided scoring, multi-level
  tie-break, stateful capacity/budget consumption, and region-diverse failover selection — installed as
  `/usr/local/bin/router`.
- **Environment** (`environment/Dockerfile`): `ubuntu:24.04` + `python3`; `/app` created empty. No
  source shipped to the agent.

## Completion Rates

Latest validation run (commit `0febedf`) — **passing**:

| Check | Result |
|---|---|
| Structural | 9/9 |
| Oracle | 3/3 |
| Difficulty balance | passed — avocado (avocado_dvsc_tester) 2/5, opus 5/5 |
| AI assessment | Revise (0 Critical / 0 High) — non-blocking |
| Contamination | LOW |

The difficulty gate passes because the weak runner (avocado) is not trivial (2/5) while a strong runner
(opus 5/5) solves it, so the task is hard but fair. Across avocado's failing trials the *only* failing
test is the multi-level tie-break, whose outcome depends on how region affinity is applied — the two
qualitative points above.

## Model Analysis

The routing logic is otherwise fully specified and easily implemented, so the difficulty concentrates
in the two deliberately-qualitative behaviors: the region-affinity application (which determines whether
a score-tie actually occurs) and the multi-level tie-break order (which resolves it). A good-faith
implementation that applies affinity additively or with a different magnitude, or orders the tie-break
differently, computes a different winner on the tie case and fails — while an implementation that
reasons out the intended, reproducible ordering passes. Strong models (opus 5/5) get it right
consistently; avocado (2/5) gets it right only some of the time.

## Anti-Cheating Analysis

- **Hardcoded outputs:** tests construct configs/requests programmatically and assert provider chains
  derived from the inputs, not fixed literals; multiple tests share providers with different
  strategies/priorities/tenants so a constant answer cannot satisfy them.
- **Overfitting to visible tests:** the grader tests are not shipped in the container; the agent sees
  only `instruction.md` and an empty `/app/src`.
- **Modifying test files:** grading runs the harness's own `tests/` against `/app/src`; the agent
  cannot alter the verifier.
- **Bypassing the solution path:** tests invoke the real `router` binary end-to-end and check exit
  codes and full failover chains, so a stub that prints fixed strings fails the scoring, stateful
  capacity/budget, and validation assertions.
