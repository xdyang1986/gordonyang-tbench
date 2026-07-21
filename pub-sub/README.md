# codimango/pub-sub

## Description
**Build-from-scratch** Go task. Agent must implement a message broker's **fan-out allocator** at `/app/main.go` that distributes a batch of `load` messages across weighted, capacity-limited subscribers.

The required behavior is a fully-specified multi-round **credit-decay weighted allocation**:
- credit starts = weight, decays to `credit/2+1` when served, otherwise accumulates `+weight`
- each round splits remaining load proportionally to credit, caps at capacity, guarantees progress with highest-credit fallback (tie: lowest index)
- if total credit drains to 0, fallback to round-robin in input order

This shape avoids the trajectory-contamination problem of debug-in-place tasks (which require shipping a buggy file that must itself be human-written). Here no `broken/` file is shipped; the image starts with empty `/app`.

## Completion Rates
- Oracle: passes (reference `solve.sh` writes correct allocator to `/app/main.go`).
- `claude-code` / `claude-opus-4-6` and `metacode`: to be measured.

Empirical local pytest: 22 parametrized allocation cases + 3 invariants = 25 tests; reference solution passes 25/25.

## Anti-Cheating Analysis
- **Hardcoded outputs**: tests build and run the program on many `(load, subscribers)` inputs and assert exact allocations plus conservation invariant (`sum == min(load, total_cap)`, each within cap); nothing is statically hardcodeable.
- **Overfitting to visible tests**: tests are hidden at solve time and include multi-round cases beyond the 3 examples in instruction.
- **Modifying test files**: Dockerfile does not copy tests; harness injects `/tests/` after agent run.
- **Bypassing the intended path**: grade builds and runs `/app`; only a correct allocator passes.
- **Pinned toolchain**: `GOTOOLCHAIN=local` on pinned `golang` image; no network.
