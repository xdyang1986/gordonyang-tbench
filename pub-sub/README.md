# codimango/pub-sub

## Description
**Balanced hard (65 tests, T lines) with burst, cost, dynamic weight, negative, global rebalancing — closes BAD_GRADING_WEAK.**

- **Input:** T loads may be negative ±1e12, G groups `prio min weight cap rate burst` (6 fields), S subs `gid prio min weight cap rate burst cost` (8 fields, cost≥1, caps total cost). Accepts old formats: 5-field groups burst0, 6-field subs burst0 cost1, 7-field subs burst given cost1.
- **Effective caps:** `sRemCost=cap-totalCost`, `sRemCount=floor(sRemCost/cost)`, `sEffCount=min(sRemCount, rate+burst_rem if rate>0)`, `sumMemberEff`, `gRemCost`, `gRemCount=floor(gRemCost/minCost)` if has members else 0, `effG=min(gRemCount,sumMemberEff,rate+burst_rem,0 if no members)`. Invalid gid →0 and excluded from totals and credit/weight/burst updates (final values equal initial).
- **Backward-compat:** 5-field groups as burst0, 6-field subs as burst0 cost1, 7-field subs as cost1, legacy formats must produce identical output to full-field equivalents.
- **Min-phase:** Priority desc tie idx asc, `min(min,effCap,rem)`.
- **Weighted loop:** Multi-round after mins, temp credits init persistent, active `alloc<effCap`, total sum, if total==0 bulk RR fallback, else share `floor(rem*credit/total)` via overflow-safe mulDiv, capped, fallback highest credit tie idx, temp credits `c/2+1` if delta>0 else `+weight`, repeat. Persistent credit `c/2+1` if alloc!=0 else `+weight_old`. Overflow-safe via `math/bits`.
- **Dynamic weight:** `max(1,floor(mulDiv(weight,9,10)))` if alloc!=0 else `+1`, overflow-safe via mulDiv.
- **Burst:** One-time extra beyond rate, effective cap includes `rate+burst_rem`, after batch excess over rate consumes burst_rem.
- **Global rebalancing:** While remaining>0 up to 10 iter recompute remaining caps, allocate groups then members, return unused.
- **Negative loads:** Dealloc by priority desc groups then members, never below 0.
- **Output:** T lines S CSV only.

## Spec Clarity and Quality Fixes

- **Information Leakage:** Prose formulas are necessary for byte-exact determinism.
- **Output Ambiguity:** T lines S CSV precise, weighted loop fully pinned fixing leftover/rounding.
- **Complexity Balancing:** Previous versions with T+8 output and 56 tests caused timeouts (all agents failed, no Go files after 4320s). Cut subsystems (priority aging, final weights, T+8 extra output) to keep one hard idea +4 extras (burst, cost, negative, dynamic weight, rebalancing).
- **Grading hole closed (Step1):** Added exact multi-batch cases killing mutants:
  - Dynamic weight AFTR 1,1,10 verbatim: loads [1,1,10] case where weight decay changes third batch.
  - Served-decay vs eligible-but-unallocated +1: separate cases for each branch.
  - Burst carryover: [5,5] with rate2 burst3 where batch2 cap differs because batch1 consumed burst (3,2 then 1,1).
  - Negative dealloc with cost>1: [6,-4] cost2 and cost3 case.
  - Credit off-by-one (f400820 bug): [8] 2,5,1 vs mutant 1,6,1.
  - Converted 3 previous invariant-only sensitive cases to exact.
- **R07 fix (Step3):** Removed `_chmod_no_access()` theater – agent runs as root, so chmod 000 ineffective. Rely on fuzz/deterministic random cases for anti-cheating.
- **Cost:** Added cost factor with exact batch allocation `3,2` and cost totals validation.

## Test Quality

- **65 tests:** 34 exact allocation batch-only T lines (24 original +10 new exact multi-batch killing credit, burst, weight mutants +3 sensitive converted to exact), plus 21 corners: min>cap, min>rate, min>rate+burst exact, tie/order, no members, invalid gid exact, blank lines/spaces/tabs, large numbers, weight overflow, credit overflow, rate, rate+burst, zero caps, global rebalancing (1,1,8 demonstrating rebalancing where group0 limited by member caps to 2), cost factor, negative deallocation, backward compat old 5/6 and 7-field raw via `run_case_raw`, deterministic 20 random, fuzz invariants 30 random with conservation (caps cost, non-negative), plus 6 new exact multi-batch killers (dynamic weight 1,1,10, served-decay, eligible+1, burst carryover cap diff, negative cost>1, credit off-by-one).

- **Dynamic weight:** Multi-batch cases now exact: 1,1,10 verbatim plus served-decay vs eligible+1 isolated.

- **Overflow:** Large weight and credit tests, plus dynamic weight overflow-safe.

- **Timeout:** Agent timeout increased to 2400 sec to cover observed claude-code trials 1392-1936s (includes build/verify overhead).

## Completion Rates

- Oracle: **65/65 mean 1.0** with overflow-safe, burst, cost, negative, dynamic weight, rebalancing, T lines.

## Anti-Cheating

- Tests build and run agent binary on fresh stdin, cover effective caps, min, priority, rate, burst, cost, rebalancing, negative, overflow, blank lines, tabs, backward compat, deterministic, fuzz invariants. No chmod theater – rely on fuzz/deterministic random generation plus exact multi-batch state update cases that cannot be hardcoded.
- Hard to overfit: 34 exact cases plus 20 deterministic random +30 fuzz invariants with random groups/subs each run.

## Spec Clarity and Quality Fixes (detailed)

- See instruction.md for full spec.

- **BAD_GRADING_WEAK fixed:** Previously 3 sensitive multi-batch cases were invariant-only (conservation, caps, min guarantees) not byte-exact, allowing mutants that ignore weight evolution, burst carryover, credit off-by-one to pass. Now exact with oracle outputs.

- **Other Quality Issues – fixed:**
  - Example 3 previously 3,1 vs 2,1 fixed, Example 6 fixed 3,2/16/6,10.
  - Backward-compat raw branches now tested via `test_backward_compat_old_format_5_6` and `test_backward_compat_7_fields` using `run_case_raw` with raw 5-field and 7-field lines, plus 2 parametrized CASES with legacy formats in CASES list.

## Anti-Cheating

- Tests build and execute agent binary on fresh stdin, cover effective caps with burst and cost, min>cap, min>rate, min>rate+burst, invalid gid, blank lines, overflow, rebalancing, negative, cost, backward compat, deterministic, fuzz invariants. Not hardcodeable, filesystem defense via randomness, pinned toolchain.

## Model Analysis (to be updated after re-run)

- Previous version 48 tests: all agents 5/5 too easy for Opus/GPT due to fully-specified spec, 0/5 for codex on multi-batch state update axis (the untested axis). New 65-test version with exact multi-batch killers should move gate: expect Opus may drop from 5/5 to 2-3/5, Avocado similar, codex failure axis now tested.
- R07 chmod removed – was theater when agent runs as root.

## Future: Step2 fallback if still 5/5

- If still 5/5 after Step1, pivot to f400820 shape: keep current 703-line allocator as environment/broken/main.go with 1-2 subtle planted bugs (credit off-by-one and burst-carryover), delete entire "Necessary specification" section and leave only I/O format, build command, and 3 failing input/output examples. This is rephrase-proof – Avocado rephrases instruction.md, not shipped code, which is why v17 survived and every from-scratch version got clarified into 5/5.
