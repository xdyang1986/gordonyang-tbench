# codimango/pub-sub

## Description
**ULTIMATE EXTREME HARD** with burst, cost, dynamic weights, priority aging, negative deallocation, global rebalancing, min, priority, rate, credit-decay, T+8 output. Hardened repeatedly after "still too easy".

- **Input:** T loads may be negative ±1e12, G groups `prio min weight cap rate burst` (6 fields), S subs `gid prio min weight cap rate burst cost` (8 fields, cost≥1, caps are total cost). Accepts old 5-field groups burst0, 6-field subs burst0 cost1, 7-field subs cost1 for backward compat (now tested).
- **Effective caps:** `sRemCost = cap - totalCost`, `sRemCount = floor(sRemCost/cost)`, `sEffCount = min(sRemCount, rate+burst_rem if rate>0)`, `sumMemberEff`, `minCostInGroup`, `gRemCost`, `gRemCount = floor(gRemCost/minCost)`, `effG = min(gRemCount, sumMemberEff, rate+burst_rem, 0 if no members)`. Invalid gid→0.
- **Priority aging:** `effectivePriority = base + streak//2`, streak consecutive eligible batches with alloc==0, resets on serve. After 2 misses prio +1.
- **Min+Priority:** Min-phase sorted by effective priority desc tie idx, capped.
- **Weighted loop:** Multi-round share `floor(rem*credit/total)` via 128-bit mulDiv, progress guarantee highest credit tie idx, bulk RR fallback when total==0, temp credit `c/2+1` if delta>0 else `+weight`.
- **Dynamic weight:** `max(1, floor(w*0.9))` if alloc!=0 else `w+1`, credit `c/2+1` if alloc!=0 else `c+weight_old`.
- **Burst:** One-time extra to exceed rate, effective cap includes `rate+burst_rem`, after batch excess over rate consumes burst_rem.
- **Global rebalancing:** While remaining>0 up to 10 iter recompute remaining caps with rate remaining, allocate groups then members, return unused.
- **Negative:** Deallocation by priority desc groups then members, never <0.
- **Output T+8:** T batch counts, group totals cost, sub totals cost, group credits, sub credits, group burst rem, sub burst rem, group final weights, sub final weights.

## Spec Clarity Fixes

- Output Ambiguity: Pins weighted leftover/rounding (share formula, fallback, temp credit), burst consumption, cost handling floor, T+8 format, priority aging formula, global rebalancing, negative order. Examples match oracle.

- Too Restrictive: 27 exact cases justified because spec fully pinned, plus lenient invariants.

- Too Easy: Added burst, cost, aging, negative, dynamic weight, rebalancing, T+8 output → ~800 lines Go, very hard (0/5 for strong models).

## Test Quality - Fixed Other Quality Issues

**Previously:** 27 strong exact cases plus conservation/determinism/fuzz, but several corners near-vacuous (`test_priority_aging`, `test_dynamic_weight_and_credit` only checked line count or `w>=1`), backward-compat parsing untested, fuzz only invariants.

**Now (56 tests total):**

- **27 exact allocation parametrized:** 20 original burst0 cost1 (exact T+8 including totals/credits/burst/weights) +5 burst/negative (burst 3,2; 6,-4; rate+burst; etc) +2 cost factor (cost 2/5 → 3,2 counts, 16 group total, 6,10 sub totals; cost 2/3 → 4,4 counts, 20 total). All byte-exact, no hedging.

- **Strengthened previously vacuous corners:**
  - `test_priority_aging` now exact: T=3 loads [1,1,1] mins 1 each prio5 → expected `1,0 / 1,0 / 0,1` after 2 misses aging boost makes sub1 effective prio 6 >5, plus checks totals `3 / 2,1`, credits, weights, burst lines exact.
  - `test_dynamic_weight_and_credit` now exact: T=2 loads [5,5] weight10 → first batch `3,2`, second `3,2`, group totals `10`, sub totals `6,4`, final weights `8` and `8,8`, credits exact, and also compares full output to Python reference `run_allocator_py`.
  - `test_min_exceeds_cap`, `test_min_gt_rate`, `test_min_gt_rate_with_burst`, `test_group_no_members`, `test_invalid_gid`, `test_blank_lines_and_spaces`, `test_large_numbers`, `test_large_weight_overflow`, `test_large_credit_overflow`, `test_rate_limiting`, `test_rate_with_burst`, `test_zero_caps`, `test_global_rebalancing`, `test_final_totals_and_credits_consistency`, `test_negative_deallocation`, `test_burst_final_consistency`, `test_cost_factor`, `test_cost_factor_exact`, `test_zero_load_batch`, `test_cap_exhaustion_three_batches` now all check full T+8 exact (including group totals, sub totals, credits, burst rem, weights) not just first line or len.

- **Backward-compat parsing tests (new):**
  - `test_backward_compat_old_format_5_6`: raw input with 5-field groups and 6-field subs (old format) vs new format with burst0 cost1 should produce identical full output, checks len 9 and exact match.
  - `test_backward_compat_7_fields`: raw with 7-field subs (burst given cost default 1) vs 8-field explicit cost 1, identical output.

- **Fuzz:**
  - `test_fuzz_invariants` (30 random) remains invariant-only lenient (caps, cost, totals, T+8 length, credits≥1, burst≥0, weights≥1) providing lenient path.
  - `test_fuzz_exact_vs_reference` (new, 20 random): exact byte-match Go binary vs Python reference `run_allocator_py` that implements same allocate_batch, dynamic weight, aging, burst, cost, global rebalancing, negative deallocation, T+8 output. No leniency, ensures Go and Python reference match exactly for random cases.

- **Determinism:** 20 random with negative/burst/cost, run twice identical.

- **Total:** 27 exact + 29 corners/invariants/exact fuzz = 56, all strong, no vacuous.

- **R06/R09:** Overflow-safe mulDiv, offline, chmod 000 filesystem defense.

## Completion Rates

- Oracle: **56/56** mean 1.0 with ultimate implementation.
- Models: Opencode claude-sonnet-4 **0.0/1**, previously too easy 5/5, now very hard, targeting 20-80% for best models.

## Anti-Cheating

- T+8 output requiring cost, burst, weight, credit tracking. Hardcoding batches fails totals/credits/burst/weights. Fuzz exact vs reference prevents alternative fair implementations that only respect invariants but not exact decay/burst/cost/aging. Backward-compat tests ensure robust parsing not just new format. Filesystem defense, offline, no static oracle.
