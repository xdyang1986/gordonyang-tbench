# codimango/pub-sub

## Description

**Debug-in-place (70 tests): repair a shipped Go hierarchical broker allocator carrying four subtle defects.**

The Dockerfile `COPY`s `environment/broken/main.go` to `/app/main.go`. The agent must find and fix the defects
in place. The prompt gives **only** the stdin/stdout format, four invariants, and one failing input/output
example per defect — it does **not** state the allocation algorithm. The algorithm lives in the shipped ~700-line
source and has to be reverse-engineered.

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

| # | Line | Defect | Class | Exposed by |
|---|---|---|---|---|
| D1 | 169 | `creditTmp[i] = c/2` instead of `c/2 + 1` (in-round) | policy | E5 |
| D2 | 478 | `sRemCostIter` omits `- subBatchCount[s]*subCost[s]` | invariant — subscriber **cost cap** | E1 |
| D3 | 493 | subscriber `rateRem` omits `- subBatchCount[s]` | invariant — subscriber **rate** | E3 |
| D4 | 535 | group `rateRem` omits `- groupBatchCount[g]` | invariant — group **rate + burst** | E2 |

D2–D4 are one conceptual mistake: the 10-iteration rebalancing loop recomputes headroom from *persisted* state
and forgets the allocation already made *within the current batch*. Each is an **invariant violation** — the
shipped program exceeds a limit the prompt declares it must respect — so a fix is verifiable without knowing the
allocation policy. All 15 agents at `0bf1e7c` fixed all three.

D1 is the one **policy divergence**, and it is deliberately the only one left. The two persistent credit sites at
lines 663 and 685 are shipped **correct**, reading `c/2 + 1`, while line 169 reads `c/2`. The file therefore
documents its own defect: three credit-update sites, two agreeing, one not. That converts an unguessable
recurrence into a visible internal inconsistency, with no hint in the prompt.

Combined: **27/70 failing**, reference 70/70, `go vet` clean on both. Verified end-to-end: shipped → 27 failures,
`solve.sh` → 70/70.

### Why the credit defects went from three to one

At `0bf1e7c` all three credit sites were bugged (D1 plus persistent D5/D6) and the run came back 0/15. CTRF
against the shipped baseline shows why:

| | shipped baseline | agents |
|---|---|---|
| invariant tests (conservation, fuzz, rate, min, cap, gid) | fail | **fixed** |
| `test_credit_off_by_one` | fail | still fail |
| `test_dynamic_weight_1_1_10`, `test_served_decay_vs_unallocated`, `test_eligible_but_unallocated_plus1` | **pass** | **newly broken** |

Agents were *regressing* the dynamic-weight recurrence — code that was never defective — while hunting the credit
bugs, because weight and credit are updated in the same block and nothing distinguished which was wrong. Adding
more defects to that block made the flailing worse, not the task harder. Leaving the persistent sites correct
gives the agent a reference point inside the file and removes the incentive to perturb neighbouring logic.

### Every defect must be exposed by an example — enforced by `tools/audit_defects.py`

Run it before every push. It extracts the reference from `solve.sh`, diffs it against the shipped source to
enumerate defects, builds a binary per defect, parses the examples out of `instruction.md`, and fails if any
defect produces identical output on all of them. It also checks each example really is a failing case and that
each documented "Correct output" matches the reference.

Against the committed state at `00a7555` it reports:

```
L169:creditTmp[i] = creditTmp[i]/2 + 1     .     .     .     .   <-- INVISIBLE
L663:groupCredit[g] = groupCredit[g]/2 + 1 .     .     .     .   <-- INVISIBLE
FAIL: see above — do not push
```

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

### How the credit defects became unfair (historical record)

Defect 4 is a policy divergence, and at `cc85add` it was provably unfair rather than merely hard. All 15 agent
trials fixed defects 1–3 and failed only on the credit recurrence. A claude-code trajectory shows the agent
replaced the rule with `mulDiv(credit, 9, 10)` — copying the *weight* decay from the adjacent lines.

That guess **reproduces the reference output on all four visible examples**, including the one meant to pin the
credit rule. Agents were producing solutions consistent with 100% of the evidence they could see, and the hidden
tests rejected them.

The replacement fourth example separates the correct rule from every plausible alternative:

```
3
4
4
10
1
0 0 1 200 0 0
2
0 0 0 1 34 0 0 1
0 0 0 3 33 0 0 1
```

| credit rule | output |
|---|---|
| `c/2 + 1` (reference) | `1,3 / 1,3 / 3,7` |
| `c/2` (shipped defect) | `1,3 / 0,4 / 10,0` |
| `c*9/10` (what every agent chose) | `1,3 / 0,4 / 5,5` |
| `(c+1)/2` | `1,3 / 1,3 / 5,5` |
| `c/2 + 2` | `1,3 / 2,2 / 5,5` |
| reset to `1` | `1,3 / 2,2 / 5,5` |

No rates, bursts or cost factors, so it isolates the credit recurrence cleanly. It replaces the old fourth
example, whose prose still described the retired dynamic-weight mechanism (*"pinning exact +1 rule"*) and pointed
solvers at the wrong subsystem. With it, guessing is falsified by visible evidence and the task becomes
hypothesis-and-check rather than hypothesis-and-hope.

Superseded: that example fixed D6 only. The audit later showed D1 and D5 were still invisible — see below.

`go vet` is clean on the shipped source; there is no dead code, unused variable, or comment that marks the
defects as planted.

## Completion Rates

- Reference (`solution/solve.sh`): **70/70**.
- Shipped buggy state: **27 failed / 43 passed**.
- Verified **inside the real built image** (`docker build environment/`, then mount `tests/` and `solution/`):
  shipped → `reward=0`; `solve.sh` then `tests/test.sh` → `reward=1`, 70/70. (Image verification was run against
  the six-defect source at 28 failures; the four-defect source is verified locally at 27 and changes no harness
  behaviour.)

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

### Online run at `cc85add` (first valid measurement) — AFTR GOOD, 0/15 on difficulty

| Gate | Result |
|---|---|
| Oracle | 3/3 |
| AI assessment | Accept (0 Crit · 0 High · 0 Med · 2 Low) |
| AFTR | **GOOD**, difficulty **GENUINELY_HARD**, all 13 rubrics pass, Secondary NONE, "No required fixes" |
| Balance | **failed** — avocado 0/5, opus 0/5, gpt 0/5 |

The invariant-violation redesign cleared the quality axis outright: `BAD_AMBIGUOUS` is gone and R01–R03 pass.
Trials were genuine (3 `SandboxBuildFailedError` aborts aside; durations 347–2277s, real costs).

CTRF across claude-code, metacode and codex shows one signature: defects 1–3 all fixed, failures confined to
`test_credit_off_by_one`, `test_served_decay_vs_unallocated`, `test_dynamic_weight_1_1_10` and a few
`test_allocation` cases. Cause and fix above.

Caveat on the AFTR: it rated R01 PASS, but the witness above shows a solution consistent with every visible
example that the hidden tests reject. The `GOOD` verdict is fragile until the discriminating example lands.

### Online run at `00a7555` (discriminating credit example) — 0/15, cause found

Clean sample: 15 genuine trials, **zero infra aborts**, durations 335–1475s. Oracle 3/3, AI assessment Accept,
provenance and contamination clean.

The new example helped — metacode reached 33/35 named tests (only `test_allocation` + `test_credit_off_by_one`)
and its trajectory shows it wrote `credit/2 + 1` correctly at both persistent sites. It still failed, because it
never touched the third site, `creditTmp`.

`tools/audit_defects.py` then showed why no agent could: of six defects, **D1 and D5 produced identical output on
all four visible examples**. The task required six fixes while showing evidence for four. That, not difficulty,
is what produced 0/15 three rounds running:

| round | defect nobody could see |
|---|---|
| `f4e1b2d` | weight `+1` omission — no artifact in the code to read |
| `cc85add` | persistent credit — the natural guess `c*9/10` satisfied every visible example |
| `00a7555` | in-round `creditTmp` and group credit — invisible in every example |

Each round fixed the named defect and left another unexposed one behind it. The audit script exists so this
cannot recur.

### Fix: two more examples, one per invisible defect

**E5 — exposes D1 (in-round temp credit).** Single batch, so D5/D6 cannot contribute:

```
1
12
1
0 0 2 42 0 0
3
0 0 0 3 14 0 0 1
0 0 0 4 3 0 0 1
0 0 0 1 13 0 0 1
```
correct `7,3,2` — shipped prints `8,3,1`.

**E6 — exposes D5 (persistent group credit).** Needs two groups, which no earlier example had; that is exactly
why D5 was invisible:

```
2
8
4
2
0 0 1 28 0 0
0 0 3 59 0 0
2
0 0 0 2 14 0 0 1
1 0 0 1 10 0 0 1
```
correct `2,6` / `1,3` — shipped prints `2,6` / `0,4`. The `c*9/10` guess also gives `2,6 / 0,4`, so this example
falsifies it too.

With both added the audit passes: every defect exposed, 1:1 defect-to-example mapping.

### Online run at `0bf1e7c` (leak fixed, six defects, all exposed) — 0/15

Clean sample: 15 genuine trials, zero infra aborts, 377–3040s. Oracle 3/3, AI assessment back to Accept
(0 Crit / 0 High) after the leakage fix, provenance and contamination clean.

Every quality gate passes. The only failure is difficulty, and the CTRF comparison above identifies the mechanism
as flailing-induced regression rather than raw difficulty. Response: cut the credit defects from three to one and
leave the persistent sites correct as an in-file reference point. Examples E4 and E6 targeted the retired defects
and must be dropped — `tools/audit_defects.py` flags them automatically:

```
  E4: shipped output matches the reference — not a failing case
  E6: shipped output matches the reference — not a failing case
```

Note one trial ran 3040s against a 3000s agent timeout, so the ceiling is being reached; worth watching if
metacode durations keep climbing.

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
