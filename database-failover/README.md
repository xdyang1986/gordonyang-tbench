# codimango/database-failover

## Description

The agent must write a **complete Go program from scratch** at `/app` (standard
library only, runnable via `go run .`) that implements a database **failover
decision engine**. The program reads a time-series of cluster snapshots on
**stdin** — one observation per line, `tick id role health pos prio`, grouped
into per-tick snapshots processed in ascending tick order — and writes the
failover decisions to **stdout**, one line per event in tick order:
`PROMOTE <id>` / `ABORT` / `REJOIN <node> <primary>`. It also exits with a
status code (`0` live primary, `1` dead unreplaced primary, `2` bad input/flags).
Nothing is scaffolded: no starter code, no interfaces, no tests in the container —
only `instruction.md`.

The naive "pick the healthy replica with the highest position" reaction is easy;
the task is hard because a correct engine must combine several interacting,
stateful behaviors over the full snapshot stream, with a precise deterministic
I/O contract:

- **A running merged view** — each tick merges the lines present into a
  `map[id]Node`; unmentioned nodes retain their last record (no expiry), and the
  election runs against the full merged state, not just the current tick's lines.
- **Debounced death detection** — failover fires only after `N=3` consecutive
  ticks of the primary observed `down`, with **reset-on-up**, and the counter
  **resets to 0 after an abort** so the still-down primary re-accumulates and
  re-attempts on a later 3-down run.
- **Election order** — highest `pos`, then **higher** `prio`, then lowest `id`.
- **Data-loss guardrail** — abort unless `(cluster-max pos across ALL nodes,
  including the dead primary and lagging/down nodes) − winner pos ≤ max-loss`
  (default `0`, overridable via `-max-loss`).
- **Stateful cutover** — fence + demote the dead primary, promote the winner
  (it becomes the new primary the engine then watches), reattach survivors
  **silently on stderr** (non-graded).
- **REJOIN semantics** — emitted only when a previously-fenced **ex-primary**
  returns `up`, once, targeting the current primary; surviving replicas never
  emit REJOIN. Within a tick the decision prints first, then REJOIN lines sorted
  by node id; across ticks everything is in ascending tick order.
- **Multi-cycle** — the engine continues after each event, so a stream can
  produce several PROMOTE/ABORT/REJOIN lines.
- **Strict validation** — any malformed line or invalid flag exits `2` with no
  decisions emitted.

Grading is **black-box on stdout + exit code** (the verifier builds and runs the
agent's own program on crafted snapshot streams), so there is no internal-API
crutch.

## Completion Rates

Local Docker runs (`codimango bench run`, CLI v0.52.1), k=5. **Calibration is
satisfied** — both calibration targets are genuine mixes (≥1 pass **and** ≥1 fail),
so the balance check ("Avocado not trivial AND ≥1 agent solved") is met.

| Model | Pass rate (k=5) |
|-------|-----------------|
| Oracle | 3/3 (deterministic; full suite verified in Docker) |
| Opus 4.6 | **2/5** — mix |
| Avocado | **4/5** — mix |
| Sonnet 4.6 | not measured (informational only) |

> Avocado measured 3/5 on a suite that still contained one over-strict test
> (`test_bad_role_value_exit2`, asserting exit-2 for an unknown *role* value the
> spec never requires). That test has been removed; recomputing over the
> independent remaining assertions gives 4/5 (the removed-test-only failure flips
> to a pass; the legitimate `test_debounce_two_downs` failure remains). Opus's
> failures never involved that test, so Opus is unaffected.

## Model Analysis

- **Opus 4.6 — 2/5.** 2 clean passes (23/23). 3 failures, all **legitimate
  reasoning gaps** on stated behavior: (a) one fully-broken solution failing 17
  tests including the worked example — did not build a correct merged-state
  engine; (b) one missed `test_debounce_two_downs` (fired before N=3) plus the
  REJOIN ordering/emission rules; (c) one missed the data-loss guardrail
  (`default_maxloss`, `clustermax_includes_a_down_node`, `reattempt_after_abort`).
  No failures involved the removed over-strict test.
- **Avocado — 4/5 (3/5 as measured, pre-fix).** 3 clean passes (23/23). Its one
  legitimate failure missed `test_debounce_two_downs` (fired on 2 consecutive
  downs instead of waiting for N=3). Trajectories show genuine algorithm work
  (clusterMax, debounce, fence, reattach) with **no test-file access and no
  hardcoded test tokens** (spot-checked) — solves are real, not leakage.

**Dominant failure modes across models:** (1) the running merged-map **retention**
rule (unmentioned nodes persist; election against full known state) — Opus's
biggest miss; (2) **debounce timing** (N=3 with reset) — the shared Avocado/Opus
miss; (3) the **data-loss / cluster-max guardrail** and (4) **REJOIN**
emission/ordering. All are reasoning gaps on behavior the spec states explicitly,
not task-setup artifacts — the difficulty is composing the full stateful engine
correctly over the snapshot stream.

**Calibration verdict:** the task discriminates across the tier (Opus 2/5,
Avocado 4/5) with both targets a mix. Note the margin is **variance-sensitive**
(an earlier roll of the same spec gave Opus 0/5, Avocado 5/5); a future re-roll
could shift the balance, so a durable difficulty bump (e.g. converting a
prescribed mechanic into a derived scenario-level guarantee) would harden it.

## Anti-Cheating Analysis

- **Hardcoded outputs:** The verifier drives the agent's compiled program with
  many distinct snapshot streams (debounce on/off boundaries, reset-on-up,
  election tie-breaks, default/overridden data-loss aborts, cluster-max spanning
  down nodes, re-attempt-after-abort, REJOIN, multi-failover, validation) and
  asserts on computed decision sequences and exit codes — there is no fixed
  output to print.
- **Overfitting to visible tests:** No test files ship in the container; the
  suite runs only at verify time and covers more scenarios than `instruction.md`
  enumerates by example.
- **Modifying test files:** Tests live outside `/app` and are supplied by the
  verifier at grade time; the agent cannot see or alter them.
- **Bypassing the intended solution path:** Grading is black-box on stdout +
  exit code from the agent's own compiled program (built from `/app`, any layout)
  on fresh inputs. No code ships in the container and no oracle is present at
  solve time, so the only way to pass is to implement the merged-state engine,
  the debounce + election + data-loss logic, the REJOIN/exit-code contract, and
  input validation correctly.
