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
  `map[id]Node`; unmentioned nodes retain their last record (no expiry), and every
  decision runs against the full merged state, not just the current tick's lines.
- **Debounced death detection** — the spec is scenario-level ("ignore transient
  blips, fail over only on a sustained outage"), so the exact threshold is the
  agent's to choose; the counter resets on recovery and re-accumulates after an
  abort. Graded by invariant (a single-tick blip must not fire; a sustained
  outage must).
- **Election order** — highest `pos`, then **higher** `prio`, then lowest `id`.
- **Quorum gate** — a promotion requires a strict majority of *all nodes ever
  seen* to be healthy; otherwise `ABORT`, re-attempting once quorum is restored.
- **Data-loss guardrail** — abort unless `(cluster-max pos, including the dead
  primary and lagging nodes but EXCLUDING any diverged ex-primary) − winner pos ≤
  max-loss` (default `0`, overridable via `-max-loss`).
- **Divergence guard (3-way)** — a fenced ex-primary that recovers is *diverged*
  if it is strictly ahead of the current primary. A diverged node: **counts**
  toward quorum, is **excluded** from the cluster-max, and is **not** an election
  candidate; it may `REJOIN` only once it is no longer ahead of the primary.
- **Stateful cutover / multi-cycle** — fence + demote the dead primary, promote
  the winner (which the engine then watches), reattach survivors silently on
  stderr; the stream can produce several events.
- **REJOIN ordering** — decision line first, then REJOIN lines sorted by node id;
  ascending tick order across ticks.
- **Strict validation** — any malformed line or invalid flag exits `2` with no
  decisions emitted.

Grading is **black-box on stdout + exit code** (the verifier builds and runs the
agent's own program on crafted snapshot streams), so there is no internal-API
crutch.

## Completion Rates

Local Docker runs (`codimango bench run`, CLI v0.52.1), k=5, on the current build
(quorum + 3-way divergence guard). **Avocado is a genuine mix**, so the balance
check ("Avocado not trivial AND ≥1 agent solved") is met locally.

| Model | Pass rate (k=5) |
|-------|-----------------|
| Oracle | 3/3 (deterministic; full suite verified in Docker) |
| Avocado | **2/5** — mix (validation-gate model) |
| Opus 4.6 | 2/5 on an earlier build; not re-measured on the current build |
| gpt-5.5 | not runnable locally (measured only by the cloud gate) |

## Model Analysis

- **Avocado — 2/5 (current build).** 2 clean passes (28/28); all 5 trials ran
  (no reward-less/infra trials). The 3 failures are on **stated/derivable**
  behavior:
  - `test_elect_highest_position` — missed the core election rule (1 trial).
  - `test_diverged_node_quorum_yes_election_no_clustermax_no` — missed the 3-way
    divergence composition: a diverged node must count toward quorum yet be
    excluded from both the candidate set and the cluster-max (1 trial).
  - `test_rejoin_diverged_then_recovers_when_primary_advances` — treated
    divergence as permanent instead of re-evaluating against the current
    primary's position, so it never re-emitted the delayed `REJOIN` (2 trials).
  Trajectories show genuine algorithm work (quorum, diverge, fence, rejoin) with
  **no test-file access and no hardcoded test tokens** — solves are real.

**Dominant failure modes:** the **divergence composition** (the 3-way split and
its dynamic re-evaluation) is the primary differentiator, followed by exact
**election** ordering. These are reasoning gaps on behavior the spec states, not
task-setup artifacts — the difficulty is composing the full stateful engine
(retention + debounce + quorum + data-loss + divergence + REJOIN) correctly.

**Calibration note:** this margin is **variance-sensitive** — earlier builds of
this task rolled Avocado at 5/5 (too easy) and 0/5 (when the divergence rules
were not yet stated). The current mix comes from the divergence composition being
both *stated* (fair) and *subtle* (a real reasoning challenge). The cloud gate
also evaluates gpt-5.5, which cannot be measured locally.

## Anti-Cheating Analysis

- **Hardcoded outputs:** The verifier drives the agent's compiled program with
  many distinct snapshot streams (blip vs sustained outage, election tie-breaks,
  default/overridden data-loss aborts, cluster-max spanning down nodes, quorum
  loss/restore, re-attempt-after-abort, REJOIN with divergence, multi-failover,
  validation) and asserts on computed decision sequences and exit codes — there
  is no fixed output to print.
- **Overfitting to visible tests:** No test files ship in the container; the
  suite runs only at verify time and covers more scenarios than `instruction.md`
  enumerates by example.
- **Modifying test files:** Tests live outside `/app` and are supplied by the
  verifier at grade time; the agent cannot see or alter them.
- **Bypassing the intended solution path:** Grading is black-box on stdout +
  exit code from the agent's own compiled program (built from `/app`, any layout)
  on fresh inputs. No code ships in the container and no oracle is present at
  solve time, so the only way to pass is to implement the merged-state engine,
  debounce, quorum, data-loss, the divergence guard, and the REJOIN/exit-code
  contract correctly.
