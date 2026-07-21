# codimango/pub-sub

## Description
**Build-from-scratch, ultimate complex hierarchical allocator with explicit spec and implicit robustness** - combines hierarchical groups + min + priority + multi-batch persistent credit + credit-decay. Fixes previous BAD quality flags while keeping task hard.

**Core allocation (fully explicit, unique output, no alternative):**
- **Primitive allocate_batch(load, prio, min, weight, cap, credit) → batch_alloc**: min phase sorted priority desc tie idx asc, `give = min(min, cap, rem)`, then weighted phase multi-round: proportional `rem*credit/total` capped, progress guarantee highest credit tie lowest idx, efficient RR fallback bulk cycles + partial input order when total==0 (efficient for 1e12, stays ≥1 with correct decay), credit_tmp evolves `credit/2+1` if served else `+weight`, final credit update `credit/2+1` if batch>0 else `+weight`. Exact formulas uniquely determine output.
- **Hierarchical multi-batch:** T batches, loads. State: group_total, sub_total, group_credit=weight, sub_credit=weight persistent. Per batch: remaining caps, sum member remaining caps per group, effective remaining group cap = min(group rem cap, sum member rem caps) (0 if no members). Group-level batch via allocate_batch, then per group allocate its share to members via same primitive. Output T lines CSV per sub per batch.

**Why still hard (balanced):**
- Core primitive explicit but must be implemented correctly and reused at two levels with scatter/gather, effective caps, and persistent state across batches (2 levels × 2 phases × T batches).
- **Implicit robustness (8 corner tests):** blank lines and extra spaces robust parsing, min>cap capped, min>load priority order, group with no members → effective 0, invalid gid → 0 allocation no crash, large numbers up to 1e12 efficient O(n log n) 64-bit, zero caps/loads/mins, RR fallback determinism, priority tie deterministic. These were previously flagged as missing; now all present as explicit tests.

**Quality fixes:**
- **BAD_AMBIGUOUS / BAD_GRADING_WRONG (R01,R02,R03,R08):** Instruction contains exact pseudocode for allocate_batch including `credit/2+1`, min capping, priority order, effective caps, so only one output correct. Previous alternative decay `(credit+weight)/2` is now explicitly incorrect.
- **BAD_GOLDEN (R12):** Reference Go uses efficient RR fallback bulk cycles `cycles = min(minRem, rem/len(active))` and processes 1e12 case `500B,500B` in <0.1s, 64-bit safe.
- **BAD_GRADING_WEAK (R06,R09):** Tests include explicit `test_large_numbers` 1e12 and `test_rr_fallback_multi_batch`, `test_min_exceeds_cap`, `test_group_no_members`, `test_invalid_gid`, `test_blank_lines_and_spaces`, `test_zero_caps`, `test_priority_tie_and_order`, `test_deterministic` plus conservation. Dockerfile pre-installs pytest, test.sh offline no apt-get/curl/network.

## Output Ambiguity - Minor (fixed)
Output format is now precise: T lines, S comma-separated ints, no spaces, empty lines for S==0. Residual edge-case numeric ambiguity resolved by explicit `credit/2+1` formula and matching examples. Reasonable agent can iterate against tests.

## Test Quality - Fixed
Tests correctly build and execute binary and use exact-match assertions (strong).
- **30 tests total** (not 60 as previously claimed): 20 parametrized hierarchical multi-batch (fixed examples + random T=1..4, G=1..4, S=1..16) covering group caps, effective caps, min/priority, multi-round both levels, zero load, multi-batch credit persistence + `test_conservation` + `test_deterministic` + 8 corner tests: `test_min_exceeds_cap`, `test_priority_tie_and_order`, `test_group_no_members`, `test_invalid_gid`, `test_blank_lines_and_spaces`, `test_large_numbers` (1e12), `test_rr_fallback_multi_batch`, `test_zero_caps`. All present now.
- **test.sh reward path fixed:** Previously `set -e` + `$?` check broken - pytest failure caused exit before reward write. Now uses `if pytest ...; then echo 1 else echo 0` which is safe under `set -e` (if condition does not trigger exit).

## Completion Rates
- Oracle: passes **30/30** with efficient implementation.
- Previous 72c1ddc had 4/5 metacode false negative due to ambiguous decay; now explicit, should be clean.

## Anti-Cheating
- Exact outputs fair because spec explicit with unique formula.
- Tests cover hierarchical, effective caps, min>cap, priority tie, empty groups, invalid gid, blank lines, 1e12, RR fallback, zero caps, conservation, deterministic. Not hardcodeable.
- No network during grading, pinned toolchain `GOTOOLCHAIN=local`.
