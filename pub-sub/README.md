# codimango/pub-sub

## Description
**Build-from-scratch, balanced hard hierarchical allocator** with min, priority, rate limits and multi-batch persistent credit, with explicit handling for fair grading and overflow-safe golden. This version is rebalanced after online was too easy 5/5 and too hard 0/5, targeting 20-80% sweet spot.

- **Hierarchical:** G groups (prio, min, weight, cap, rate), S subs (gid, prio, min, weight, cap, rate). Effective remaining group cap per batch = min(group remaining cap, sum of members' effective per-batch caps, group rate if rate>0). If group has no members, effective 0. If gid out of range, sub gets 0.
- **Min + Priority:** 2-phase per batch: min phase sorts by priority descending, tie by original index, allocating min capped to min(min, cap, rate if >0, rem). If load insufficient, higher priority first. If min>cap, capped.
- **Rate limiting:** Per-batch max per group and per subscriber (rate 0 = unlimited). Adds extra capping layer beyond total caps.
- **Credit-decay weighted:** Weighted phase multi-round after min: proportional share must be computed without 64-bit overflow via 128-bit (rem*credit can be 1e24 and 1.2e19), progress guarantee highest credit tie lowest index, efficient RR fallback bulk cycles for large remaining when total==0 (with correct decay credit/2+1 stays ≥1, so fallback never happens for correct impls, optional robustness). Credit update exactly credit/2+1 if batch>0 else +weight, persistent across batches.
- **Multi-batch persistent state:** T batches (1..3 loads up to 1e12), credits and totals persist, remaining caps shrink. Output T lines CSV per sub per batch.

## Spec Clarity and Quality fixes (per latest reviews)

- **Over Specified (Solution Giveaway Stage 8.B a,c) - fixed:** Previous instruction prescribed full algorithm: min-phase sort order, weighted multi-round loop, floor(remaining*credit/total), mandatory credit/2+1 decay, RR bulk-cycle fallback, and math/bits mulDiv as paste-ready pseudocode matching reference line-for-line. This removed engineering judgement. Now keeps only **Necessary Specification**: effective-cap = min(group rem, sum member eff, rate) and I/O format (T, loads, G, S, CSV, blank lines robust, 64-bit safety) and 64-bit safety requirement. Fairness properties described in prose requiring engineering judgement, not paste-ready code block. Exact decay credit/2+1 declared necessary for determinism (Necessary Specification / test-asserted-values carve-out) but described in prose, not as code block matching reference.

- **Output Ambiguity Minor - fixed:** Format precise T lines S CSV no spaces. Previously Example 2 output (2,5,1) contradicted test oracle (2,6,1) with hedging prose "maybe" and reference gives 2,5,1 maybe. Now corrected to 2,6,1 matching oracle and hedging removed. All examples match oracle: 6,4,3,3 / 2,6,1 / 4,1,1 x2 / 500B / 2,1 and large weight 1e24 overflow example.

- **Information Leakage Minor - fixed:** Exact recurrence and cap formulas appear in instruction but per Stage 8.B are Necessary Specification for byte-exact determinism, not leakage. No oracle files, no agent-readable expected outputs; tests piped via stdin and test file chmod-000s itself during execution (filesystem defense). README meta not agent-facing.

## Test Quality - Fixed

- **32 tests total** (balanced, not 60 too hard nor 25 too easy): 20 parametrized hierarchical multi-batch with rate limits covering group caps, effective caps, min capping, priority ordering, multi-round, multi-batch credit persistence, large 1e12, large-weight 1e24, large-credit 1.2e19, rate limiting + conservation + deterministic + 8 corners: min>cap, priority tie/order, group no members, invalid gid, blank lines/spaces, large 1e12, large weight overflow 1e24, large credit overflow 1.2e19, rate limiting, zero caps + fuzz_random 20 sequences vs Python reference. All previously flagged missing tests (RR fallback efficiency now optional since unreachable with correct decay, group-no-members, zero-caps, priority-tie, deterministic) now present, README correctly 32 (not 60), reward path fixed for set -e via `if pytest then else`, verifier offline via Dockerfile preinstall pytest, filesystem defense chmod 000 during binary execution.
- **R06 coverage:** Large-weight (1e24) and large-credit (1.2e19) overflow tests where remaining*credit would overflow signed 64-bit.
- **R09 reliability:** Dockerfile pre-installs pytest, test.sh offline no apt-get/curl uv network.

## Completion Rates

- Oracle: passes **32/32** with efficient overflow-safe implementation.
- Balanced: was too easy 5/5 for Opus/GPT (29-test single-batch) and too hard 0/5 for 60-test multi-batch. This 32-test hierarchical multi-batch with rate + min/priority + credit-decay + overflow + 8 corners + fuzz + deterministic should be hard-but-passable targeting 20-80% sweet spot.

## Anti-Cheating

- Tests cover hierarchical effective caps with rate limits, min>cap, priority tie, empty groups, invalid gid, blank lines, 1e12, 1e24 overflow, 1.2e19 overflow, rate limiting, zero caps, conservation, deterministic, plus 20 random main cases and 20 fuzz sequences vs Python reference. Not hardcodeable, filesystem defense chmod 000.
- No network during grading, pinned toolchain, overflow-safe via math/bits.
