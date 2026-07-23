# codimango/pub-sub

## Description
**Build-from-scratch, VERY HARD ultimate hierarchical allocator** with burst, dynamic weights, negative deallocation, global rebalancing, min, priority, rate limits, persistent credit, overflow-safe, T+6 output. Hardened after online still too easy 5/5, targeting 20-80% sweet spot.

- **Hierarchical with burst:** G groups `prio min weight cap rate burst` (6 fields), S subs `gid prio min weight cap rate burst` (7 fields). Burst is one-time extra to exceed rate, not replenished. Effective per-member cap = `min(remCap, rate+burst_rem if rate>0)`. Effective group cap = `min(g_rem, sumMemberEff, rate+burst_rem if rate>0, 0 if no members)`. If gid out of range, 0 allocation.

- **Min + Priority:** 2-phase: min phase priority desc tie idx, capped to `min(min, effCap, rem)`. If min>cap or min>rate+burst_rem, capped. Handles zero.

- **Rate limiting + burst:** Rate 0 unlimited, else per-batch max. Burst allows exceeding rate up to `rate+burst_rem`, consuming burst_rem by excess. Burst remaining tracked across batches. Rate remaining enforced across global rebalancing iterations.

- **Credit-decay + dynamic weight:** Weighted phase multi-round after min, share `floor(rem*credit/total)` via 128-bit mulDiv (1e24, 1.2e19 overflow), progress guarantee highest credit tie idx, bulk RR fallback when total==0. Credit update: `c/2+1` if alloc!=0 else `c+weight_old`. Dynamic weight: if alloc!=0 `max(1,floor(w*0.9))` else `w+1`. Persistent across batches.

- **Global rebalancing loop:** For each positive batch, while remaining>0: recompute remaining effective caps (remCap, rateRem, sumMemberEff), allocate to groups via primitive, per-group to members, return unused capacity to remaining. Up to 10 iter.

- **Negative loads (deallocation):** Load may be negative, means return capacity. Deallocate groups by priority desc tie idx, within each group members by priority desc, up to `total+batch` remaining, never below 0. Burst not affected. Credit/weight evolution counts deallocation as activity.

- **Extra output T+6:** T batch lines S CSV (may be negative), then group totals G CSV, sub totals S CSV, group final credits G CSV, sub final credits S CSV, group final burst remaining G CSV, sub final burst remaining S CSV. Increases complexity.

## Spec Clarity and Quality fixes

- **Over Specified - fixed:** Keeps Necessary Spec as prose formulas (effective caps with burst, min-order, weighted loop with mulDiv and fallback, dynamic weight, credit update, burst consumption, global rebalancing, negative deallocation, T+6 output, blank lines robust, overflow safety). Not paste-ready Go code.

- **Output Ambiguity - fixed (weighted leftover/rounding and T+6 and burst/negative):** Pins weighted loop share formula, progress guarantee, temp credit evolution, burst consumption rule (excess over rate consumes burst_rem), dynamic weight formula, global rebalancing steps, negative deallocation priority order, and T+6 format (batch lines + group totals + sub totals + group credits + sub credits + group burst rem + sub burst rem). Examples updated to match oracle including burst and negative.

- **Too Restrictive - fixed:** 25 exact parametrized cases (20 original burst0 + 5 new burst/negative) now justified because spec fully pinned, plus lenient invariant tests: conservation checks caps/effective caps and validates extra 6 lines, deterministic 20 random, fuzz 30 random invariant-only, 14 corners.

- **Too Easy - fixed:** Added burst (token-bucket one-time), negative deallocation, dynamic weight, global rebalancing, T+6 output requiring tracking of totals, credits, burst remaining. Implementation grows from ~200 to ~600 lines Go with multiple loops (min, weighted, global rebalancing, deallocation), making it very hard. Recent opencode claude-sonnet-4 mean 0.0 (hard) vs previously 1.0 easy, now balanced harder.

## Test Quality - Fixed and Hardened for Ultimate Hard

- **48 tests total** (was 33, now 48 for ultimate hard): 25 parametrized exact (20 hierarchical multi-batch with rate+dynamic+rebalancing+overflow + 5 new burst/negative), plus conservation (validates caps and T+6 totals/credits/burst), 8 classic corners (min>cap, min>rate, min>rate+burst, priority tie/order, group no members, invalid gid, blank lines/spaces/tabs, large 1e12, weight 1e24, credit 1.2e19, rate limiting, zero caps), plus 6 new hard corners: rate with burst, burst final consistency, global rebalancing 1,1,8->2,8, dynamic weight/credit, final totals consistency, negative deallocation, burst consumption, zero load, 3-batch exhaustion, plus deterministic 20 random (including negative loads and burst), fuzz 30 random invariant-only with burst.

- **Corner coverage:** min>cap, min>rate, min>rate+burst, priority tie, group no members, invalid gid, blank lines, large 1e12, weight 1e24, credit 1.2e19, rate, burst, negative deallocation, zero caps, zero load, cap exhaustion, rebalancing, dynamic weight, final totals/credits/burst.

- **R06 coverage:** Large-weight and large-credit overflow requiring 128-bit mulDiv.

- **R09 reliability:** Dockerfile preinstalls pytest, offline, filesystem defense chmod 000.

- **Too Easy fix:** Burst, negative, dynamic weight, global rebalancing, T+6 output make it very hard (opencode 0.0 mean).

## Completion Rates

- Oracle: passes **48/48** with overflow-safe implementation including burst, negative, dynamic weight, rebalancing.
- Balanced: previously too easy 5/5 for Opus/GPT, now 0/5 for claude-sonnet-4 on ultimate hard, targeting 20-80% sweet spot for strongest models.

## Anti-Cheating

- Tests cover effective caps with burst, min>cap, min>rate, min>rate+burst, priority tie, empty groups, invalid gid, blank lines, 1e12, 1e24, 1.2e19, rate, burst, negative deallocation, zero caps, rebalancing, dynamic weight, final totals/credits/burst consistency, conservation, deterministic, fuzz invariants. Not hardcodeable, filesystem defense, T+6 output so hardcoding only batch lines fails.
