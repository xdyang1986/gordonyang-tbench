# codimango/pub-sub

## Description
**Build-from-scratch, HARD hierarchical allocator** with dynamic weights, global rebalancing, min, priority, rate limits, persistent credit, overflow-safe, extra totals/credits output. This version is hardened after online was too easy 5/5 for strong models, targeting 20-80% sweet spot.

- **Hierarchical:** G groups (prio, min, weight, cap, rate), S subs (gid, prio, min, weight, cap, rate). Effective remaining group cap per batch = min(group remaining cap, sum of members' effective per-batch caps, group rate if rate>0). If group has no members, effective 0. If gid out of range, sub gets 0 and does not contribute.

- **Min + Priority:** 2-phase per batch: min phase sorts by priority descending, tie by original index, allocating min capped to min(min, effective cap, rate, rem). If load insufficient, higher priority first. If min>cap or min>rate, capped to feasible.

- **Rate limiting:** Per-batch max per group and per subscriber (rate 0 = unlimited). Effective caps incorporate rate limits. Rate remaining is enforced across global rebalancing iterations within same batch (once rate exhausted, no more allocation that batch).

- **Credit-decay weighted + dynamic weight:** Weighted phase multi-round after min: proportional share `floor(rem*credit/total)` must be computed without 64-bit overflow via 128-bit `mulDiv` (rem*credit can be 1e24 and 1.2e19), progress guarantee highest credit tie lowest index, bulk RR fallback when total==0 (shouldn't happen with correct decay credit/2+1 stays ≥1). Credit update: if batch>0 then `credit=credit/2+1` else `credit+=weight_old`. Dynamic weight evolution (new hard feature): if allocated>0 then `weight=max(1, floor(weight*0.9))` else `weight=weight+1`. Persistent across batches, affects future credit growth.

- **Global rebalancing loop (new hard feature):** For each batch, while remaining load >0: recompute remaining effective caps (group rem, member rem, rate remaining, sum member eff), allocate to groups via primitive, then per-group to members via same primitive, return unused capacity (when members cannot take full group allocation) to remaining pool and reallocate to other groups. Up to 10 iterations, terminates when no effective cap or no progress. Ensures no capacity wasted due to member caps.

- **Multi-batch persistent state + extra output:** T batches (1..3 loads up to 1e12), credits, weights, totals persist, remaining caps shrink. Output is `T+4` lines: T batch allocations (S CSV), then group cumulative totals (G CSV), sub cumulative totals (S CSV), group final credits (G CSV), sub final credits (S CSV). Previously T lines only, now T+4 increases implementation complexity and output validation.

## Spec Clarity and Quality fixes (per latest reviews)

- **Over Specified (Solution Giveaway) - fixed:** Previously pasted full algorithm as Go code block matching reference line-for-line. Now keeps Necessary Specification as prose formulas: effective-cap, min-phase order, weighted loop (active set, total, share floor via mulDiv capped, used==0 fallback, temp credit update), dynamic weight evolution, global rebalancing loop described as prose steps, hierarchical order, credit start=weight, I/O T+4 lines, blank lines robust, 64-bit safety. Not paste-ready code, retains engineering judgement for efficient impl.

- **Output Ambiguity Minor - fixed (including weighted leftover/rounding):** Previously only T lines format pinned, leaving rounding ambiguous. Now pins weighted-phase leftover distribution and also T+4 output format precisely: T batch lines S CSV, plus group totals G CSV, sub totals S CSV, group final credits G CSV, sub final credits S CSV, no spaces. Examples updated to match oracle: Example1 6,4,3,3 + 10,6 + 6,4,3,3 + 3,2 + 3,2,3,1 etc.

- **Information Leakage - fixed:** Exact recurrence and formulas are Necessary Specification for byte-exact determinism, not leakage. Tests chmod 000 via filesystem defense.

- **Test Quality Too Restrictive - fixed:** Previously 20 exact cases seen as too restrictive because weighted phase called engineering-judgement. Now weighted loop fully pinned, so exact-match justified. Retains lenient invariant path: test_conservation, deterministic 20 random, fuzz_invariants 30 random invariant-only, plus 8 corners and 4 new hard corners (min>rate, global rebalancing, dynamic weight, final totals consistency, zero load, 3-batch exhaustion).

## Test Quality - Fixed and Hardened

- **39 tests total** (was 32, now 39 to increase difficulty): 20 parametrized hierarchical multi-batch with rate limits, dynamic weights, global rebalancing, overflow, covering group caps, effective caps, min capping, min>rate capping, priority ordering, multi-round, multi-batch credit persistence and weight decay, large 1e12, large-weight 1e24, large-credit 1.2e19, rate limiting, plus conservation (checks caps/effective caps for all CASES and validates extra 4 lines totals/credits), 8 classic corners (min>cap, min>rate, priority tie/order, group no members, invalid gid, blank lines/spaces, large 1e12, large weight 1e24, large credit 1.2e19, rate limiting, zero caps), plus 4 new hard corners: global rebalancing where group cap limited by sum member caps (1,1,8 -> 2,8), dynamic weight/credit evolution across batches, final totals consistency, zero load batch, cap exhaustion 3 batches, plus deterministic 20 random and fuzz_invariants 30 random invariant-only.

- **Corner coverage:** min>cap, min>rate (new), priority tie, group no members, invalid gid, blank lines/tabs/spaces, large 1e12, large weight 1e24, large credit 1.2e19, rate limiting, zero caps, zero load, cap exhaustion, global rebalancing, dynamic weight, final totals/credits.

- **R06 coverage:** Large-weight (1e24) and large-credit (1.2e19) overflow tests requiring 128-bit mulDiv.

- **R09 reliability:** Dockerfile pre-installs pytest, offline, filesystem defense chmod 000 during binary execution.

- **Output Ambiguity fix:** Weighted loop fully pinned plus T+4 output format (batch allocations + group totals + sub totals + final credits) makes exact-match justified.

- **Too Easy fix:** Added dynamic weight evolution (max(1,floor(w*0.9)) if served else w+1), global rebalancing loop (up to 10 iterations returning unused capacity), and extra 4 output lines requiring tracking of totals and credits. These increase implementation complexity from ~200 to ~400 lines Go, making it significantly harder for LLMs (previous easy version 5/5 now expected 20-80% with 0/5 for weak models).

## Completion Rates

- Oracle: passes **39/39** with efficient overflow-safe implementation including dynamic weight and rebalancing (after fixing rate remaining bug and [10,10] case).
- Balanced: previously too easy 5/5 for Opus/GPT on 29-test single-batch, and 0/5 for 60-test multi-batch. This 39-test version with dynamic weights, global rebalancing, T+4 output, rate+min+priority+credit-decay+overflow+new corners+deterministic+fuzz should be hard-but-passable targeting 20-80% sweet spot. Recent opencode claude-sonnet-4 jobs showed 0.0 mean (hard), while earlier metacode 1.0 (easy), so now balanced harder.

## Anti-Cheating

- Tests cover hierarchical effective caps with rate limits, min>cap, min>rate, priority tie, empty groups, invalid gid, blank lines, 1e12, 1e24 overflow, 1.2e19 overflow, rate limiting, zero caps, zero load, cap exhaustion, global rebalancing (group cap limited by sum member caps), dynamic weight/credit evolution, final totals/credits consistency, conservation, deterministic, plus 20 exact main cases and 30 fuzz invariant sequences. Not hardcodeable, filesystem defense chmod 000, pinned toolchain, overflow-safe via math/bits.
- Output now T+4 lines, so hardcoding only batch lines fails on totals/credits checks.
