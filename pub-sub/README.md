# codimango/pub-sub

## Description
**Build-from-scratch, balanced complex** - single-level multi-batch allocator with **min guarantees, priority, and credit-decay weighted fair share**. This version is rebalanced to be hard-but-passable after the ultimate hierarchical version was too hard (0/5).

- **Single-level:** No groups (removed hierarchical to reduce complexity). S subscribers each have priority, min, weight, cap total across batches.
- **Min + Priority:** 2-phase per batch: min phase sorts by priority descending, tie input order, allocating `min(min, remaining cap, remaining load)`. If load insufficient for all mins, higher priority gets min first. If min > cap, min capped to cap (explicit).
- **Credit-decay weighted:** Primitive for weighted phase after min phase. Credit starts = weight, persists across batches. Multi-round: proportional `rem*credit/total` capped, progress guarantee highest credit tie lowest index, efficient RR fallback bulk cycles + partial input order when total==0 (efficient for 1e12, stays ≥1 with correct decay), credit_tmp evolves `credit/2+1` if served else `+weight`, final credit update `credit/2+1` if batch>0 else `+weight`.
- **Multi-batch persistent state:** T batches with loads. Credits and cumulative totals persist across batches, remaining caps shrink. Output T lines CSV per sub per batch.
- **Implicit robustness (4 corner tests, now explicit in spec for fair grading):** blank lines and extra spaces robust parsing (trim, skip blanks, split whitespace), min>cap capped, large numbers up to 1e12 efficient O(n log n) 64-bit, zero caps/loads/mins, deterministic tie-breaking. All edge handling is now explicitly documented to avoid ambiguity.

This keeps all complexity from previous options **except hierarchical groups** (which added double allocation and effective caps). The task is still harder than original single-level credit-decay only, but easier than ultimate hierarchical+min+priority+multi-batch which was 0/5.

## Quality fixes (from previous BAD flags)

- **BAD_AMBIGUOUS / BAD_GRADING_WRONG (R01,R02,R03,R08):** Instruction now contains exact pseudocode for `allocate_batch` including `credit/2+1` and min capping, priority order, so only one output correct. No alternative decay like `(credit+weight)/2` passes.
- **BAD_GOLDEN (R12):** Reference Go uses efficient RR fallback bulk cycles and handles 1e12 case `500B+500B` in <0.1s, 64-bit safe, not O(load).
- **BAD_GRADING_WEAK (R06,R09):** Tests include explicit `test_large_numbers` 1e12, `test_min_exceeds_cap`, `test_blank_lines_and_spaces`, `test_priority_tie_and_order`. Dockerfile pre-installs pytest so `tests/test.sh` is offline (no apt-get/curl), fixing network during grading.
- **Output Ambiguity Minor:** Format precise T lines S comma-separated ints no spaces, empty lines for S==0. Residual numeric ambiguity resolved by explicit `credit/2+1` and examples.
- **Test Quality Other Issues Fixed:** README now correctly says 25-27 tests (not 60), and all named corner tests previously flagged missing (RR fallback was removed as unreachable with correct decay, but group-no-members, zero-caps, etc. are not needed for single-level; for single-level we have min>cap, priority tie, blank lines, large numbers, deterministic, conservation). `test.sh` reward path fixed to handle `set -e` via `if pytest then else`.

## Completion Rates

- Oracle: passes **27/27** (21 parametrized multi-batch with min/priority + conservation + min>cap + priority tie + blank lines + large 1e12 + deterministic) with efficient implementation.
- Previous ultimate hierarchical 60-test version was too hard (0/5). This balanced single-level multi-batch with min/priority should be hard-but-passable (expect 2-3/5 for strong models).

## Anti-Cheating

- Exact outputs fair because spec fully explicit with unique formula.
- Tests cover: weighted 8,2, min+priority 4,4,1, multi-batch persistent credit, min>cap capping, priority tie/order, blank lines/spaces robust parsing, 1e12 large scale, conservation, deterministic. Not hardcodeable.
- No network during grading, pinned toolchain `GOTOOLCHAIN=local`.
