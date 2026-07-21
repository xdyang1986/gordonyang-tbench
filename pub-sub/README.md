# codimango/pub-sub

## Description
**Build-from-scratch, ultimate complex hierarchical allocator with explicit spec** - combines hierarchical groups + min guarantees + priority + multi-batch persistent credit + credit-decay. This version fixes the previous `BAD_GRADING_WRONG` quality flag.

**Why previous version was flagged BAD:**
- `BAD_AMBIGUOUS` (R01/R03): credit decay formula `credit/2+1` was implicit, not uniquely determined by examples.
- `BAD_GRADING_WRONG` (R02/R08): exact-output grading rejected a coherent alternative decay `(credit+weight)/2` that satisfied examples, causing metacode false negative (4/5 not clean discriminator, ambiguity-driven).
- `BAD_GRADING_WEAK` (R06/R09): zero-credit RR fallback never forced, 1e12 scale untested, verifier did `apt-get` + network `uv` install.
- `BAD_GOLDEN` (R12): reference used O(rem) RR fallback that timed out >5s at spec-stated 1e12 scale.

**Fixes in this version (72c1ddc → current):**
- **Fully explicit spec:** Instruction now contains exact pseudocode for `allocate_batch` including `give = min(min, cap, rem)`, priority desc tie idx asc for min phase, proportional `rem*credit/total`, progress guarantee highest credit tie lowest idx, efficient RR fallback with bulk cycles + partial in input order, credit update `credit/2+1` if batch>0 else `+weight`, effective group cap `min(group rem cap, sum member rem caps)` (0 if no members), handling of blank lines/spaces (trim + skip), invalid gid → 0 allocation no crash, min>cap capped, large numbers up to 1e12, deterministic.
- **Unique output:** Spec with exact formulas uniquely determines output; no alternative coherent decay is considered correct. Exact-output grading is now fair.
- **Efficient golden:** Reference Go in `solve.sh` now uses efficient RR fallback (bulk cycles `cycles = min(minRem, rem/len(active))`) instead of O(rem) one-by-one, handles 1e12 in <2s (tested: 500B+500B case). Uses 64-bit safe arithmetic.
- **Stronger grading:** Tests include explicit `test_large_numbers` with 1e12, `test_rr_fallback_multi_batch`, `test_min_exceeds_cap`, `test_group_no_members`, `test_invalid_gid`, `test_blank_lines_and_spaces`, `test_zero_caps`, plus conservation and deterministic. Total 40 tests.
- **Offline verifier:** `tests/test.sh` no longer does `apt-get` or network `curl`/`uv` install; uses pre-installed `pytest` or `python3 -m pytest` with no network, fixing R09.

## Algorithm (explicit)

**Input:** T, loads[0..T-1], G groups (prio, min, weight, cap), S subs (gid, prio, min, weight, cap). May contain blank lines/spaces.

**State:** `group_total`, `sub_total` cumulative, `group_credit = group_weight`, `sub_credit = sub_weight` persistent.

**Primitive allocate_batch(load, prio, mins, weights, caps, credits) → batch_alloc** (exact pseudocode in instruction.md, including min phase priority order, weighted credit-decay multi-round with efficient RR fallback).

**Per batch:** 
1. Compute remaining caps and sum member remaining caps per group, effective remaining group cap = min(group rem, sum member rem).
2. Group-level batch = allocate_batch(load, group prio/min/weight/effCap/credit)
3. Per group: collect its subs in input order, allocate group_batch[g] to them via allocate_batch with sub remaining caps.
4. Update totals and credits, output per-batch CSV.

## Completion Rates
- Oracle: passes 40/40 with efficient implementation.
- Previous online: 4/5 metacode was false negative due to ambiguous decay; now spec explicit, should be clean.

## Anti-Cheating & Quality
- Exact outputs are now fair because spec is fully explicit with unique formula.
- Tests cover: random hierarchical multi-batch, effective caps, min>cap capping, priority ordering when load < sum mins, group no members →0, invalid gid→0, blank lines/spaces robust parsing, 1e12 large scale (500B+500B), RR fallback deterministic.
- No network during grading, pinned toolchain `GOTOOLCHAIN=local`.
