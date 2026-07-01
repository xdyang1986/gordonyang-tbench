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

Cloud validation (codimango), commit `c6641c0` — **Validation Passed**, k=5.

| Check / Model | Result |
|---|---|
| Structural checks | PASS (8/8) |
| Oracle | 3/3 |
| Avocado (validation-gate) | **2/5** — mix |
| gpt-5.5 | **0/5** |
| Opus 4.6 | no trials (balance keys on Avocado) |
| Balance check | **Passed** — "avocado not trivial and ≥1 agent solved" |
| AI assessment | Accept (0 Critical · 0 High · 0 Medium · 1 Low) |
| Contamination | MEDIUM |
| Provenance | SUSPECT (non-blocking) |

## Model Analysis

- **Avocado — 2/5** (validation-gate model; a local k=5 run matched the cloud at
  2/5). 2 clean passes (28/28); all trials ran. Failures are on stated/derivable
  behavior:
  - **election ordering** (`highest pos → priority → lowest id`);
  - the **3-way divergence composition** — a diverged ex-primary counts toward
    quorum yet is excluded from both the election candidate set and the
    cluster-max;
  - **dynamic re-evaluation of divergence** — a diverged node rejoins only once
    the primary advances past it; models that treat divergence as permanent never
    re-emit the delayed `REJOIN`.
- **gpt-5.5 — 0/5** — the full composition (retention + debounce + quorum +
  data-loss + divergence guard) breaks it entirely.

**Primary differentiator:** the divergence composition (the 3-way split and its
dynamic re-evaluation), layered on the stateful engine. All failures are
reasoning gaps on behavior the spec states, not task-setup artifacts.

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
