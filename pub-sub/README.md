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

## The three defects

All three are multi-batch-only: the shipped program agrees with the reference on batch 1 of every example, so
single-batch smoke tests look correct.

| # | Subsystem | Defect |
|---|---|---|
| 1 | Persistent credit | Decay is a plain halving `c/2`; the reference is `c/2 + 1`. Reads as idiomatic. |
| 2 | Burst | Excess is measured against `rate + burst_rem` instead of `rate`, so the guard can never fire and `burst_rem` is never depleted. |
| 3 | Dynamic weight | Eligible-but-unallocated entities never grow their weight — the `+1` in the `else` branch is absent. |

`go vet` is clean on the shipped source; there is no dead code or unused-variable tell that marks the defects as
planted.

## Completion Rates

- Reference (`solution/solve.sh`): **70/70**.
- Shipped buggy state: **14 failed / 56 passed**.
- Verified end-to-end locally: buggy `/app` → 14 failures → run `solve.sh` → 70/70.

Failures span three subsystems — 8 exact multi-batch allocation cases plus `test_positive_after_deallocation_burst`,
`test_burst_carryover_multi_batch`, `test_dynamic_weight_1_1_10`, `test_served_decay_vs_unallocated`,
`test_eligible_but_unallocated_plus1`, `test_burst_carryover_cap_diff`. Scoring is all-or-nothing, so the agent
must find all three.

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
