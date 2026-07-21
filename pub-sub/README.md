# codimango/pub-sub

## Description
**Build-from-scratch, hierarchical** Go task. Agent must implement a message broker's **two-level fan-out allocator** at `/app/main.go`.

- **Level 1 - Groups:** G groups each have weight and cap. Effective cap = min(group cap, sum member caps). Allocate load to groups using credit-decay weighted allocation.
- **Level 2 - Subscribers:** S subscribers each belong to a group (gid) and have weight/cap. For each group, allocate its group-level share to its members using the same credit-decay algorithm.

Primitive `allocate(load, weights, caps)` is a multi-round credit-decay allocator:
- credit starts = weight, decays to `credit/2+1` when served, else accumulates `+weight`
- each round splits remaining load proportionally `rem*credit/total`, capped, guarantees progress via highest-credit fallback (tie lowest index)
- if total credit drains to 0, fallback to round-robin in input order

This hierarchical shape is significantly harder than single-level: agent must correctly implement the primitive once and reuse it at two levels with effective-cap computation and scatter/gather of per-group allocations. Previous debug-in-place calibration showed single-level fully-specified was too easy for the online gate; adding the hierarchical step (double allocation, effective caps) increases reasoning and implementation complexity while avoiding trajectory contamination (no `broken/` file shipped, empty `/app`).

## Completion Rates
- Oracle: passes (reference `solve.sh` writes hierarchical allocator).
- `claude-code` / `claude-opus-4-6` and `metacode`: to be measured.

Empirical local pytest: 35 parametrized hierarchical cases + 2 invariants (conservation, deterministic) = 37 tests; reference solution passes 37/37. Cases cover group-cap limiting, effective caps, multi-round at both levels, RR fallback, zero load, many groups.

## Anti-Cheating Analysis
- **Hardcoded outputs**: tests build and run binary on many (load, groups, subs) combos and assert exact per-sub allocations plus hierarchical invariants (per-sub cap, per-group effective cap, total = min(load, sum effective caps)). Not hardcodeable.
- **Overfitting to visible tests**: tests hidden at solve time include random hierarchical cases beyond 3 instruction examples.
- **Modifying test files**: Dockerfile does not copy tests; harness injects `/tests/` after agent run.
- **Bypassing the intended path**: grade builds and runs `/app`; only correct hierarchical allocator passes.
- **Pinned toolchain**: `GOTOOLCHAIN=local` on pinned golang image.
