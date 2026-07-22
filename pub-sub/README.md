# codimango/pub-sub

## Description
**Build-from-scratch, balanced complex** - single-level multi-batch allocator with **min guarantees, priority, and credit-decay weighted fair share**. Rebalanced after ultimate hierarchical 60-test version was too hard (0/5).

- **Single-level:** No groups (removed hierarchical to reduce complexity). S subscribers each have priority, min, weight, cap total.
- **Min + Priority:** 2-phase per batch: min phase sorts by priority descending, tie index, allocating min capped to remaining cap and load. If load < sum mins, higher priority first.
- **Credit-decay weighted:** Weighted phase multi-round: proportional share floor(rem*credit/total) capped, progress guarantee highest credit tie lowest index, efficient RR fallback bulk cycles + partial input order when total==0 (never happens with correct decay credit/2+1, but must be efficient for 1e12). Credit update exactly credit/2+1 if batch>0 else +weight, persistent across batches.
- **Multi-batch persistent state:** T batches, loads up to 1e12, credits and totals persist, remaining caps shrink. Output T lines CSV.

This version fixes previous quality flags:
- **Information Leakage (Significant) - fixed:** Previous instruction shipped complete `allocate_batch` pseudocode as paste-ready code matching reference line-for-line (Solution Giveaway Stage 8.B). Now instruction describes algorithm in prose with formulas inline, not as copy-paste code block. Core formulas still explicit to avoid ambiguity.
- **Spec Clarity Other Issues - fixed:** Pseudocode was exhaustively explicit bordering on giveaway, yet Example 3 output (4,1,1) contradicted test expected (4,0,2 / 3,1,2) with hedging prose "Actually... Let's use reference". Now examples corrected to match tests (Example 3 is 4,0,2 and 3,1,2) and hedging removed.

## Output Ambiguity - Minor (fixed)
Output format precise: T lines, S comma-separated ints, no spaces, empty lines for S==0. Residual numeric ambiguity resolved by explicit credit/2+1 formula and matching examples. Reasonable agent can iterate.

## Test Quality - Fixed
Tests correctly build and execute binary with exact-match (strong).
- **29 tests total** (fixes README 60 vs 25 mismatch): 21 parametrized multi-batch covering weighted, min+priority, multi-batch credit persistence, zero, large, many rounds + `test_conservation` + `test_deterministic` + 6 corner tests: `test_min_exceeds_cap`, `test_priority_tie_and_order`, `test_blank_lines_and_spaces`, `test_large_numbers` (1e12), `test_zero_caps`, `test_rr_fallback_efficiency`. All previously flagged missing tests (RR fallback, zero-caps, priority-tie, deterministic) are now present. Group-no-members and invalid-gid are not applicable for single-level (no groups/gid), so not needed.
- **test.sh reward path fixed:** Old `set -e` + `$?` check broken - pytest failure caused exit before reward write. Now uses safe `if pytest ...; then echo 1 else echo 0` pattern which does not trigger `set -e`.

## Quality fixes from earlier BADs retained
- **BAD_AMBIGUOUS / BAD_GRADING_WRONG:** Core primitive fully explicit with unique formula credit/2+1, no alternative like (credit+weight)/2 considered correct.
- **BAD_GOLDEN:** Efficient RR fallback bulk cycles handles 1e12 in <0.1s, 64-bit safe.
- **BAD_GRADING_WEAK:** Large numbers and RR fallback explicitly tested, Dockerfile pre-installs pytest so verifier offline, no apt-get/network.

## Completion Rates
- Oracle: passes **29/29** with efficient implementation.
- Balanced difficulty: single-level + min/priority + multi-batch should be hard-but-passable (expect 2-3/5 for strong models, not 0/5 too hard nor too easy).

## Anti-Cheating
- Exact outputs fair because spec explicit with unique formula.
- Tests cover weighted, min+priority, multi-batch persistence, min>cap, priority tie, blank lines, 1e12, zero caps, RR fallback efficiency, conservation, deterministic.
- No network during grading, pinned toolchain.
