# codimango/pub-sub

## Description
**Build-from-scratch, highly complex hierarchical allocator**. Combines all previous hardening options:

- **Hierarchical groups:** G groups each have priority, min, weight, cap. Effective remaining cap = min(group remaining cap, sum member remaining caps). Load first allocated to groups, then per-group to members.
- **Min guarantees + Priority:** At both group and subscriber levels, allocation is 2-phase: min phase sorts by priority descending, tie input order, allocating `min(min, cap, rem)`. Then weighted phase.
- **Credit-decay weighted fair share:** Primitive used for weighted phase at both levels after min phase. Credit starts = weight, persists across batches. Multi-round: proportional `rem*credit/total` capped, progress guarantee highest-credit (tie lowest index), RR fallback when total credit 0, credit update `credit/2+1` if served else `+weight`.
- **Multi-batch persistent state:** T batches with loads. Credits and cumulative totals persist across batches. Group and subscriber remaining caps shrink. Output is T lines, each CSV per subscriber for that batch.

This is the combination of hierarchical + min+weighted + priority + multi-batch persistent credit - significantly harder than single-level fully-specified. The agent must implement `allocateBatch` (min+priority+credit-decay) correctly and reuse it for group-level and per-group subscriber-level across T batches with effective-cap computation and state persistence. No `broken/` file shipped, empty `/app`.

## Completion Rates
- Oracle: passes (reference `solve.sh` implements full spec in Go).
- Online gate: to be measured.

Local pytest: 30 cases (fixed examples + 25 random) with T=1..3, G=1..4, S=1..11, covering group caps, effective caps, min/priority interactions, multi-round at both levels, RR fallback, zero load, multi-batch credit persistence. + conservation + deterministic = 32 tests; oracle passes 32/32.

## Anti-Cheating Analysis
- **Hardcoded outputs:** Tests drive binary on many random (T, groups, subs) combos, assert exact per-batch CSVs computed by reference Python implementation plus hierarchical invariants (per-sub cap, per-group effective cap, conservation). Not hardcodeable.
- **Overfitting:** Tests hidden, include random multi-batch cases beyond 3 instruction examples.
- **Modifying test files:** Dockerfile does not copy tests; harness injects after agent run.
- **Bypassing path:** Grade builds and runs `/app`; only correct full implementation passes.
- **Pinned toolchain:** `GOTOOLCHAIN=local`.
