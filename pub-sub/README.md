# codimango/pub-sub

## Description

**Debug-in-place (70 tests): repair a shipped Go hierarchical broker allocator with three subtle multi-batch defects.**

The Dockerfile `COPY`s `environment/broken/main.go` to `/app/main.go`. The agent must find and fix the defects
in place. `instruction.md` gives **only** the stdin/stdout format plus three failing input/output examples — it
does **not** state the allocation algorithm. The algorithm lives in the shipped 706-line source and has to be
reverse-engineered.

## Why this shape

From-scratch with a fully-pinned spec saturated the gate. Pooled across the two runs whose `instruction.md`
differed only by two backticks:

| Commit | avocado | opus | gpt |
|---|---|---|---|
| `2950b49` | 3/4 | 4/5 | 3/5 |
| `12319f8` | 5/5 | 4/5 | 5/5 |

**24/29 ≈ 83% pooled.** The `2950b49` pass was variance, not a real band. The AFTR at `12319f8` diagnosed the
cause directly: *"The crux is not discovering an algorithm, because the spec pins the algorithm, but translating
a dense stateful specification into correct code."* It was a transcription task.

Moving the algorithm out of the prompt and into the code makes the difficulty **inference** rather than
transcription, and is rephrase-proof — Avocado rewrites `instruction.md` but not the shipped source. This is the
same formula as `f400820` (v17), the only version of this task that has ever passed the gate.

## The four defects

Three of the four are **invariant violations** — the shipped program exceeds a limit `instruction.md` declares it
must respect. That matters: an invariant violation is objectively wrong regardless of allocation policy, so the
agent can verify a fix without knowing the policy. The fourth is a policy divergence in the credit recurrence.

All four are multi-batch or multi-iteration only; the shipped program agrees with the reference on the first
batch of every example.

| # | Location | Defect | Effect | Own failures |
|---|---|---|---|---|
| 1 | `sRemCostIter` | omits `- subBatchCount[s]*subCost[s]` | exceeds subscriber **cost cap** | 10 |
| 2 | group `rateRem` | omits `- groupBatchCount[g]` | exceeds group **rate + burst** | 11 |
| 3 | subscriber `rateRem` | omits `- subBatchCount[s]` | exceeds subscriber **rate** | 6 |
| 4 | persistent credit | plain halving `c/2` instead of `c/2 + 1` | policy divergence | 4 |

Defects 1–3 are the same conceptual mistake: the 10-iteration rebalancing loop recomputes headroom from
*persisted* state and forgets the allocation already made *within the current batch*. One locus, coherent story,
findable together.

Combined: **28/70 failing**, reference 70/70, `go vet` clean on both.

Concrete cap violation (needs no policy knowledge to recognise as a bug):

```
2
15
12
2
0 0 1 6 0 0
0 0 1 5 0 0
3
0 0 0 3 5 0 0 1
1 0 0 3 9 0 0 2
1 0 0 2 11 0 0 1
```
correct `5,3,2 / 0,0,0` — shipped `6,3,2 / 0,0,0`. Sub0 has cap 5 at cost 1, so 6 messages puts cumulative cost
at 6 against a cap of 5.

### Why the previous defect set was replaced

`5698433` shipped three *policy divergences* (credit off-by-one, burst carryover, weight direction) and drew AFTR
`BAD_AMBIGUOUS` — R01, R02 and R03 all failed, because the hidden tests enforced state semantics no agent could
uniquely derive. Invariant violations replace guessing with checking.

### Known residual risk

Defect 4 is still a policy divergence. The AFTR at `e6e15492` observed that *"several rollouts fixed the
invariants but chose plausible wrong credit rules that pass the visible examples."* If `BAD_AMBIGUOUS` recurs,
dropping defect 4 is the lever — that leaves 23/70 failing, all of it objectively checkable.

`go vet` is clean on the shipped source; there is no dead code, unused variable, or comment that marks the
defects as planted.

## Completion Rates

- Reference (`solution/solve.sh`): **70/70**.
- Shipped buggy state: **28 failed / 42 passed**.
- Verified **inside the real built image** (`docker build environment/`, then mount `tests/` and `solution/`):
  - shipped, no fix → `reward=0`, 28 failed / 42 passed
  - `solve.sh` then `tests/test.sh` → `reward=1`, 70/70

### Online run at `f4e1b2d` (first debug-in-place attempt) — 0/15, corrected

| Agent | Result | Best trial |
|---|---|---|
| avocado | 0/5 | 66/70 |
| opus | 0/5 | 66/70 |
| gpt | 0/5 | 63/70 |

Oracle spawned 0 of 3 trials (platform glitch, no build artifacts), so validation stayed `pending`. Agent trials
were genuine — real durations (225–1811s) and costs, `reward=0.00` not `error`.

CTRF showed an identical failure signature across all three models: `test_dynamic_weight_1_1_10`,
`test_served_decay_vs_unallocated`, `test_eligible_but_unallocated_plus1`. Not a capability gradient — a single
undiscoverable defect blocking 100% of attempts. Two of three models fixed bugs 1 and 2 cleanly and stopped at
66/70. Fixed by making bug 3 visible (above) and by adding a fourth prompt example that pins the `+1` recurrence
across two consecutive starved batches.

### Online run at `e6e15492` (invariant-violation redesign) — VOID

Oracle 0/3, all agents 0/14. Neither number means anything: **the verifier could not run pytest in any trial.**
`golang:1.26.2-bookworm` ships `/usr/bin/python3` but has no `pip` and no `ensurepip`, so
`python3 -m pip install …` failed; the retry loop `for i in 1 2 3; do CMD && break || sleep 5; done` still exits 0,
so the image built clean and every trial died at verify time with `No module named pytest`. AFTR: `BAD_INFRA`.

That run also carried a separate own-goal: the shipped `main.go` contained six `// BUG` comments naming each
defect and its fix, so even with a working verifier the task would have been self-solving (AFTR
`Information Leakage: Significant`, 1 High). Both are fixed; the next run is the first valid measurement of this
design.

The Dockerfile now bootstraps pip via `python3-pip`, fails the build if installation fails, and asserts
`import pytest, ctrf` plus the presence of the `--ctrf` flag `tests/test.sh` depends on. That assertion caught a
real error while it was being written — the plugin's import name is `ctrf`, not `pytest_json_ctrf`.

Scoring is all-or-nothing, so the agent must find every defect.

## Test Quality

Grader unchanged from the `GOOD`-AFTR version at `12319f8` (AFTR: verdict GOOD, Secondary Issues NONE, all 13
rubrics pass). 70 items: 36 exact allocation batch cases plus 34 named corners — `min>cap`, `min>rate`,
`min>rate+burst`, tie/order, no members, invalid gid, blank lines/spaces/tabs, 1e12 loads, weight overflow (1e24),
credit overflow (1.2e19) via 128-bit `mulDiv`, zero caps, global rebalancing, cost factor, negative deallocation,
backward-compat 5/6/7-field raw via `run_case_raw`, deterministic 20 random, fuzz invariants 30 random with
conservation.

Mutation-tested: the grader kills persistent-credit, temp-credit, burst-carryover and dynamic-weight mutants
individually (4–6 failures each). This closed the earlier `BAD_GRADING_WEAK` hole where a mutant deleting an
entire spec subsystem passed all 50 items.

## Anti-Cheating

Tests build and run the agent's binary on fresh stdin — no static-artifact cheat. The Docker build context is
`environment/`, so `README.md`, `tests/` and `solution/` are never copied into the image. Toolchain pinned via
`GOTOOLCHAIN=local`; verifier runs offline against pytest pre-installed at build time.

## Open items

- AFTR (optional) asked for one exact regression isolating whether a negative deallocation updates credit and
  weight. Under the debug-in-place shape the "counts as activity" clause no longer appears in the prompt, so this
  is grading strength rather than spec alignment. Candidate input (correct output shown, shipped program prints
  `4,2 / -3,0 / 0,6 / 6,0`):

  ```
  4
  6
  -3
  6
  6
  1
  0 0 1 100 0 0
  2
  0 0 0 2 50 0 0 1
  0 0 0 1 50 0 0 1
  ```
  → `4,2` / `-3,0` / `3,3` / `3,3`

## History

17+ from-scratch versions (Python and Go, prescriptive and terse, 33–70 tests) all saturated the gate. `f400820`
(v17) was the sole pass, via debug-in-place + under-specification. This version restores that shape on top of the
richer allocator and the hardened 70-test grader.
