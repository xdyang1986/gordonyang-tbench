# codimango/pub-sub

## Description
**ULTIMATE EXTREME HARD hierarchical allocator** with burst, cost, dynamic weights, priority aging, negative deallocation, global rebalancing, min, priority, rate, credit-decay, T+8 output. Hardened three times after online still too easy 5/5, now very hard with 0/5 for claude-sonnet-4.

- **Input:** T loads may be negative ±1e12, G groups `prio min weight cap rate burst` (6 fields, burst one-time extra count to exceed rate), S subs `gid prio min weight cap rate burst cost` (8 fields, cost≥1 per-msg cost, caps are total cost). Accepts old 5-field groups burst0 and 6-field subs burst0 cost1 and 7-field subs cost1 for backward compat.

- **Effective caps with burst+cost:** `sRemCost = cap - totalCost`, `sRemCount = floor(sRemCost/cost)`, `sEffCount = min(sRemCount, rate+burst_rem if rate>0)`, `sumMemberEff` per group, `minCostInGroup`, `gRemCost = cap - totalCost`, `gRemCount = floor(gRemCost/minCost)`, `effG = min(gRemCount, sumMemberEff, rate+burst_rem, 0 if no members)`. Invalid gid →0.

- **Priority aging:** `effectivePriority = basePrio + streak//2` where streak = consecutive batches where eligible (eff>0 or total>0 for dealloc) but alloc==0. Resets to 0 when served. After 2 misses, priority effectively +1, making starved entities higher priority.

- **Min + Priority:** Min-phase sorted by effective priority desc tie idx, capped to `min(min, effCap, rem)`.

- **Weighted loop:** Multi-round after mins, share `floor(rem*credit/total)` via 128-bit mulDiv (1e24,1.2e19), progress guarantee highest credit tie idx, bulk RR fallback when total==0. Temp credits `c/2+1` if delta>0 else `+weight`.

- **Dynamic weight:** `weight = max(1, floor(w*0.9))` if alloc!=0 else `w+1`. Credit `c/2+1` if alloc!=0 else `c+weight_old`. Persistent.

- **Burst consumption:** After positive batch, if rate>0 and batchCount>rate, excess consumes burst_rem: `burst_rem -= min(excess, burst_rem)`. One-time, not replenished.

- **Global rebalancing:** For positive load, while remaining>0 up to 10 iter: recompute remaining cost caps, count caps, rate remaining `rate+burst_rem - batch`, sumMemberEff, effG. Allocate groups via primitive, per-group members via primitive, return unused capacity to remaining. Ensures no waste due to member caps.

- **Negative loads:** Load<0 deallocates N=-load by priority descs: groups priority desc, within each group members priority desc, `dealloc = min(subCount+batch, remaining, group remaining)`, totals never <0, burst unaffected, counts as activity for credit/weight.

- **Output T+8:** T batch counts S CSV (may be negative), group totals cost G CSV, sub totals cost S CSV, group final credits G CSV, sub final credits S CSV, group burst rem G CSV, sub burst rem S CSV, group final weights G CSV, sub final weights S CSV.

## Spec Clarity Fixes

- **Output Ambiguity - fixed for ultimate:** Previously only T lines and T+4 and T+6, leaving rounding, burst, negative, cost, aging ambiguous. Now fully pinned: effective caps with burst+cost, min-order with aging priority, weighted loop share formula with mulDiv and fallback, dynamic weight formula, credit formula, burst consumption rule, global rebalancing steps, negative deallocation priority order, cost handling floor(cap/cost) and totals as cost, aging streak and effective priority formula, T+8 format. Examples all match oracle including burst and negative and cost.

- **Too Restrictive - fixed:** 27 exact cases (20 original burst0 cost1 +5 burst/negative +2 cost) justified because spec fully pinned, plus lenient invariant tests (conservation, deterministic, fuzz). Fuzz includes burst and cost and negative and aging, checks only invariants.

- **Too Easy - fixed repeatedly:** Started as 5/5 easy, hardened to 39 tests with dynamic weight+rebalancing+T+4 still easy, hardened to 48 tests with burst+negative+T+6 still easy for some models, now ultimate 52 tests with burst+cost+aging+negative+T+8, opencode claude-sonnet-4 mean 0.0 (very hard), targeting 20-80% for strongest models. Implementation ~800 lines Go with multiple nested loops.

## Test Quality

- **52 tests total (ultimate):** 27 exact (20 original +5 burst/negative +2 cost factor 2/5 and 2/3), plus 25 corners/invariants: min>cap, min>rate, min>rate+burst, tie/order, group no members, invalid gid, blank lines/spaces/tabs, large 1e12, weight 1e24 overflow, credit 1.2e19 overflow, rate, burst, zero caps, zero load, cap exhaustion 3 batches, global rebalancing 1,1,8->2,8, dynamic weight/credit, final totals cost consistency, negative deallocation -3,-1, burst final consistency 2,0, cost factor validation 3,2 cost 6,10 and 4,4 cost 8,12, priority aging streak→weights, deterministic 20 random including negative/burst/cost, fuzz 30 random invariant-only with burst/cost/negative.

- **Corner coverage checklist:** min>cap, min>rate, min>rate+burst, priority tie/order, group no members, invalid gid, blank lines/spaces, large numbers, large weight overflow 1e24, large credit overflow 1.2e19, rate limiting, burst, cost factor, zero caps, zero load, cap exhaustion, global rebalancing, dynamic weight, aging, negative deallocation, final totals/credits/burst/weights consistency, conservation, determinism, fuzz invariants.

- **R06/R09:** Overflow-safe mulDiv, offline pytest, filesystem defense chmod 000.

## Completion Rates

- Oracle: **52/52** mean 1.0 with overflow-safe, burst, cost, aging, negative, rebalancing, T+8.
- Model difficulty: previously 5/5 easy for Opus/GPT, 1.0 for metacode/claude-code, 0.6-0.2 for some, now 0.0 for claude-sonnet-4 opencode on ultimate, indicating very hard, targeting 20-80% for best models.

## Anti-Cheating

- T+8 output (batch counts + group totals cost + sub totals cost + final credits + burst rem + final weights) requires tracking cost, burst, weight, aging, not just batches. Hardcoding batch lines fails totals/credits/burst/weights. Fuzz invariants with cost and burst and negative ensures not hardcodeable. Filesystem defense, offline, no network.
