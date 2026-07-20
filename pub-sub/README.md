# codimango/pub-sub

## Description
**Debug-in-place, under-specified** Go task. The image ships a small Go program at `/app/main.go` — a message broker's **fan-out allocator** that distributes a batch of `load` messages across weighted, capacity-limited subscribers. It builds and mostly works, but contains **one subtle planted bug**: for some inputs the allocation is wrong. The agent must find and fix the defect so the program is correct in general, without rewriting.

Crucially, `instruction.md` gives **only the I/O format and a few failing input→output examples — never the algorithm.** The intended behavior (a bespoke multi-round **credit-decay weighted allocation**: each round splits the remaining load proportionally to per-subscriber credit, caps at capacity, guarantees progress with a highest-credit fallback, then decays served subscribers' credit and accumulates unserved ones) lives only in the shipped code. The agent must reverse-engineer the intended algorithm from the code + examples and locate the deviation.

The planted defect is a subtle off-by-one in the per-round credit decay (`credit/2` instead of `credit/2 + 1`) that only changes results in **multi-round** allocations, so it cannot be spotted by eyeballing or reading a spec — it requires understanding the algorithm and tracing.

## Why this shape (calibration history)
Extensive from-scratch calibration (Python and Go, ~16 versions) showed the online Metacode gate implements *any fully-specified* pub-sub/broker behavior correctly — fair + specified always came out too easy, and under-specifying a *validation edge* only broke the fair model. The one profile that resists the gate (and is `accepted` online, cf. dr-buffer) is **debug-in-place + under-specification**: the ground truth lives in code + examples, so there is nothing to transcribe and the instruction rephrase cannot leak the algorithm — the difficulty is *inference and localization*, not *implementation*.

## Completion Rates
- Oracle: passes (reference `solve.sh` overwrites `/app/main.go` with the corrected version).
- `claude-code` / `claude-opus-4-6` and `metacode` / `meta/avocado_dvsc_tester`: measured online.

Empirical: the shipped buggy program fails 13 of 25 local pytest cases (the multi-round divergences); the reference solution passes 25/25.

## Anti-Cheating Analysis
- **Hardcoded outputs**: tests build and run the program on many `(load, subscribers)` inputs and assert exact allocations plus a conservation invariant (`sum == min(load, total_cap)`, each within cap); nothing is statically hardcodeable.
- **Overfitting to visible tests**: tests are hidden at solve time and include multi-round divergent cases beyond the 3 examples in the instruction; a fix that only matches the examples but not the general algorithm fails.
- **Modifying test files**: the Dockerfile does not copy tests into the image; the harness injects `/tests/` after the agent run.
- **Bypassing the intended path**: the grade builds and runs `/app`; only a correct allocator passes. The algorithm is not stated, so it cannot be trivially regenerated from the prompt.
- **Pinned toolchain**: `GOTOOLCHAIN=local` on a pinned `golang` image; no network to build.
