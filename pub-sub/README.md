# codimango/pub-sub

## Status

**BLOCKED ON DIFFICULTY at `d08d996` (2026-08-28).** Everything except the balance gate is green: oracle 3/3,
AFTR **GOOD** (all 13 rubrics, Secondary NONE), AI assessment Accept (0/0/0/1), contamination LOW. Balance
**fails**: avocado 5/5, opus 4/4 genuine (one `NetworkConnectionError` abort), gpt 3/5. AFTR rates difficulty
**EASY** and recommends dropping the `hard` metadata tag.

The group-cap grading hole is closed (verified: a pure message-count rewrite of the reference now fails
`test_group_cap_is_cost_based_not_count_based`). Closing it moved opus 5/5 → 4/4 and gpt 5/5 → 3/5, but **not**
avocado. See "The dial is exhausted" below.

## Description

**Debug-in-place (71 tests): repair a shipped Go hierarchical broker allocator carrying five subtle defects
(3 invariant + 2 credit).** Shipped state fails 28/71; reference passes 71/71.

The Dockerfile `COPY`s `environment/broken/main.go` to `/app/main.go`. The agent must find and fix the defects in place. The prompt gives **only** the stdin/stdout format, four invariants, and one failing input/output example per defect — it does **not** state the allocation algorithm. The algorithm lives in the shipped ~700-line source and has to be reverse-engineered.

## Why this shape

From-scratch with a fully-pinned spec saturated the gate. Pooled across the two runs whose `instruction.md` differed only by two backticks:

| Commit | avocado | opus | gpt |
|---|---|---|---|
| `2950b49` | 3/4 | 4/5 | 3/5 |
| `12319f8` | 5/5 | 4/5 | 5/5 |

**24/29 ≈ 83% pooled.** The `2950b49` pass was variance, not a real band. The AFTR at `12319f8` diagnosed the cause directly: *"The crux is not discovering an algorithm, because the spec pins the algorithm, but translating a dense stateful specification into correct code."* It was a transcription task.

Moving the algorithm out of the prompt and into the code makes the difficulty **inference** rather than transcription, and is rephrase-proof — Avocado rewrites `instruction.md` but not the shipped source. This is the same formula as `f400820` (v17), the only version of this task that has ever passed the gate.

## The five defects

| # | Line | Defect | Class | Exposed by |
|---|---|---|---|---|
| D1 | 169 | `creditTmp[i] = c/2` instead of `c/2 + 1` (in-round) | policy | E5 |
| D2 | 478 | `sRemCostIter` omits `- subBatchCount[s]*subCost[s]` | invariant — subscriber **cost cap** | E1 |
| D3 | 493 | subscriber `rateRem` omits `- subBatchCount[s]` | invariant — subscriber **rate** | E3 |
| D4 | 535 | group `rateRem` omits `- groupBatchCount[g]` | invariant — group **rate + burst** | E2 |
| D6 | 685 | `subCredit[s] = c/2` instead of `c/2 + 1` (persistent) | policy | E4 |

`groupCredit` at line 663 ships **correct** (`c/2 + 1`) — the single in-file reference point.

D2–D4 are one conceptual mistake: the 10-iteration rebalancing loop recomputes headroom from *persisted* state and forgets the allocation already made *within the current batch*. Each is an **invariant violation** — the shipped program exceeds a limit the prompt declares it must respect — so a fix is verifiable without knowing the allocation policy.

Combined: **28/71 failing**, reference 71/71, `go vet` clean on both. Verified end-to-end: shipped → 28 failures, `solve.sh` → 71/71. `tools/audit_defects.py` PASS — 5 defects, 5 examples, clean 1:1 mapping.

### The dial is exhausted — avocado solves every fair configuration

Avocado's record, per configuration, counting only genuine trials:

| config | correct credit references visible | avocado |
|---|---|---|
| `0bf1e7c` — all three credit sites bugged | 0 | **0/5** |
| `11be1a1` — in-round + `subCredit` bugged | 1, near (22 lines, same function) | 4/4, then 5/5 |
| `14a51e7` — in-round correct, both persistent bugged | 1, far (different function and name) | 5/5 |
| `d08d996` — same defects as `11be1a1`, grading hole closed | 1, near | 5/5 |

**Avocado is 19/19 across every configuration where a correct credit site is visible anywhere in the file, and
0/5 in the one configuration where none is.** That zero-reference config is not hard, it is unfair: all three
models score 0 and agents regress dynamic-weight code that was never defective.

Two hypotheses were tested and both were wrong. Reference *proximity* does not matter (`14a51e7`: moving the
reference ~500 lines away into a different function under a different name still gave 5/5). The grading hole was
not masking avocado difficulty either — closing it converted opus and gpt passes into failures, because their
submissions had included policy rewrites, but every avocado pass was already a genuine solve.

What remains true: all five defects are **one-line fixes**. Once located, there is no work left. Any further
progress has to come from a defect whose *repair* is substantial, not from moving or counting reference sites.

Superseded note: an earlier revision of this README claimed `11be1a1` was "11/15 in band, validation passing" and
derived a rule that exactly one visible reference lands in band. Both were wrong. That run's single avocado
failure was a `MetaModelCatalogError` infra abort reported as `status=completed reward=0.00`, so avocado was
really 4/4; on revalidation at the same commit it went 5/5. **`status=completed reward=0.00` does not mean the
agent genuinely failed — the agent log has to be read.**

### Why all-three-bugged fails: agents regress code that was never broken

At `0bf1e7c` all three credit sites were bugged (D1 plus persistent D5/D6) and the run came back 0/15. CTRF
against the shipped baseline shows why:

| | shipped baseline | agents |
|---|---|---|
| invariant tests (conservation, fuzz, rate, min, cap, gid) | fail | **fixed** |
| `test_credit_off_by_one` | fail | still fail |
| `test_dynamic_weight_1_1_10`, `test_served_decay_vs_unallocated`, `test_eligible_but_unallocated_plus1` | **pass** | **newly broken** |

Agents were *regressing* the dynamic-weight recurrence — code that was never defective — while hunting the credit
bugs, because weight and credit are updated in the same block and nothing distinguished which was wrong. Adding
more defects to that block made the flailing worse, not the task harder. Leaving at least one credit site correct
gives the agent a reference point inside the file and removes the incentive to perturb neighbouring logic.

### Every defect must be exposed by an example — enforced by `tools/audit_defects.py`

Run it before every push. It extracts the reference from `solve.sh`, diffs it against the shipped source to
enumerate defects, builds a binary per defect, parses the examples out of `instruction.md`, and fails if any
defect produces identical output on all of them. It also checks each example really is a failing case and that
each documented "Correct output" matches the reference.

On the current state it reports `PASS` — 5 defects, 5 examples, clean 1:1 mapping. Historically it is what caught
the `00a7555` failure, where two defects were unfindable:

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

- Reference (`solution/solve.sh`): **70/70**. Oracle 3/3 online.
- Shipped buggy state: **28 failed / 42 passed**.
- `tools/audit_defects.py`: **PASS** — 5 defects, 5 examples, 1:1 mapping, every example a real failing case,
  every documented "Correct output" matching the reference.
- Verified **inside the real built image** (`docker build environment/`, then mount `tests/` and `solution/`):
  shipped → `reward=0`; `solve.sh` then `tests/test.sh` → `reward=1`, 70/70. (Image verification was run against
  an earlier defect set; the current source is verified locally and changes no harness behaviour.)

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

### Online run at `e704e82` (four defects, two reference sites) — TOO EASY, and provenance regressed

| Gate | Result |
|---|---|
| Oracle | 3/3 |
| AI assessment | **Accept (0 Crit / 0 High / 0 Med / 0 Low)** — cleanest yet |
| Balance | **failed, too easy** — avocado 5/5, opus 5/5, gpt 3/5 |
| Provenance | **failed** — unauthorized third-party model authorship |

First time the difficulty dial moved off zero, and it overshot: leaving two correct credit sites turned D1 into a
copy-paste. gpt at 3/5 shows the invariant defects still cost something; avocado and opus at 5/5 show they are not
enough on their own.

Provenance broke for the same reason as at `2950b49`: the prompt file was edited directly rather than regenerated
through Avocado. This time the edit was pure deletion (−47 lines, removing E4 and E6) — **deletions count as
authorship edits too**. Any change to that file, including removing text, has to go through Avocado.

### Online run at `11be1a1` (five defects, one reference site) — PASSING

| Gate | Result |
|---|---|
| Structural | PASS 10/10 |
| Oracle | 3/3 |
| **Balance** | **PASS** — avocado 4/5 · opus 3/5 · gpt 4/5 |
| AI assessment | Accept (0 Critical · 0 High · 0 Medium · 1 Low) |
| Contamination | LOW |
| Provenance | passed — **SUSPECT, review recommended** |
| **AFTR** | **`GOOD` · `GENUINELY_HARD` · all 13 rubrics pass · Secondary NONE** |

AFTR: *"This is a GOOD task: R01:Spec sufficiency, R06:Test coverage, R07:Reward-hacking, and R13:Golden
reliability are all strong, and the rollout distribution gives useful signal."*

All 15 trials genuine — no infra aborts, durations 191–1675s, real costs, a real 11/4 pass-fail mix. The
one-reference prediction from the bracket above landed exactly where it was projected.

**Two cautions for anyone touching this next:**

- **The margin is one trial.** Avocado at 4/5 passes; 5/5 is the too-easy fail. At a true rate near 80% a re-run
  has roughly a 1-in-3 chance of coming back 5/5. Don't re-run validation hoping for a better number — the
  realistic outcomes are "same" or "fails".
- **Do not take the AFTR's optional suggestion.** It proposes adding a visible credit-recurrence case to reduce
  "plausible near-misses", which would raise the pass rate while avocado is already one trial from the ceiling.
  It is explicitly optional and the verdict is GOOD without it.

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

Nothing blocking at `11be1a1`. The items below are deliberately **not** actioned — see the cautions under the
`11be1a1` run.

- The AFTR's optional suggestions (spell out the group-cap count approximation; add a visible credit-recurrence
  case) would both raise the pass rate. Declined while avocado sits at 4/5.
- Provenance reads `SUSPECT — review recommended`. It counts as passed, but this check has failed twice before on
  direct edits to the prompt file (once an addition, once a pure deletion). Worth confirming the last prompt
  change went through Avocado, and saying so in the submission so the reviewer doesn't have to guess.
- Two mutants — group and member minimums re-applied on every rebalancing iteration instead of only the first —
  pass all 70 tests. Untested behaviour; a candidate case if `tests/` is ever reopened.
- Older AFTR note (still unactioned, grading strength rather than spec alignment): an exact regression isolating
  whether a negative deallocation updates credit and weight. Candidate input (correct output shown; the
  four-defect source printed `4,2 / -3,0 / 0,6 / 6,0`):

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

17+ from-scratch versions (Python and Go, prescriptive and terse, 33–70 tests) all saturated the gate — pooled
24/29 ≈ 83%, which the AFTR called transcription rather than problem-solving. `f400820` (v17) was the sole pass,
via debug-in-place + under-specification. The current task restores that shape on top of the richer allocator and
the hardened 70-test grader.

Getting from there to `11be1a1` took a further nine rounds, and the four failure classes are worth remembering:

| class | rounds lost | fix |
|---|---|---|
| broken verifier (pytest never installed; build succeeded anyway) | 1 | in-image oracle check |
| unexposed defect — impossible, not hard | 3 | `tools/audit_defects.py` |
| information leakage (internal annotations reaching the prompt) | 1 | keep defect labels out of the handoff |
| provenance (prompt edited outside Avocado — additions *and* deletions) | 2 | regenerate, never edit |

The two scripted checks — defect visibility and the in-image oracle — cover the first two classes and should run
before every push.
