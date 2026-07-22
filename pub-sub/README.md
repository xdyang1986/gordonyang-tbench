# codimango/pub-sub

## Description
**Build-from-scratch, ultimate harder hierarchical allocator** - combines **all previous options** plus **per-batch rate limits** to fix "still too easy". This is hierarchical + min + priority + multi-batch + credit-decay + rate limiting, with explicit edge handling and overflow-safe golden.

- **Hierarchical:** G groups (prio, min, weight, cap, rate), S subs (gid, prio, min, weight, cap, rate). Effective remaining group cap per batch = min(group remaining cap, sum of members' effective per-batch caps, group rate if rate>0). If group has no members, effective 0. If gid out of range, sub gets 0.
- **Min + Priority:** 2-phase per batch: min phase sorts by priority descending, tie by original index, allocating min capped to min(min, remaining cap, rate if >0, remaining load). If load insufficient, higher priority first.
- **Rate limiting (new requirement to increase difficulty):** Per-batch max per group and per subscriber (rate 0 = unlimited). Per-member effective per-batch cap = min(remaining cap, rate if >0). Sum member effective per group limits group effective cap, plus group rate. This adds extra capping layer beyond total caps.
- **Credit-decay weighted:** Weighted phase multi-round after min: proportional share floor(remaining * credit / total) capped to remaining effective cap, but must be computed without 64-bit overflow using 128-bit (remaining*credit can be 1e12*1e12=1e24 > 9e18, and 3*4e18=1.2e19). Progress guarantee highest credit tie lowest index, efficient RR fallback bulk cycles + partial for large remaining when total==0 (efficient for 1e12, stays ≥1 with correct decay). Credit update exactly credit/2+1 if batch>0 else +weight, persistent across batches.
- **Multi-batch persistent state:** T batches (1..3 loads up to 1e12), credits and totals persist, remaining caps shrink. Output T lines CSV per sub per batch. Large numbers, blank lines/spaces robust parsing, invalid gid, min>cap, zero caps all handled explicitly for fair grading.

Fixes all previous BAD flags while making harder:
- **Information Leakage:** Removed paste-ready pseudocode matching reference line-for-line. Now prose with inline formulas, not copy-paste code.
- **Spec Clarity:** Examples corrected to match tests (6,4,3,3 / 4,4,1 / 4,1,1 x2 / 2 / 500B / 2,1 etc.) and hedging removed.
- **BAD_AMBIGUOUS / BAD_GRADING_WRONG:** Core fully explicit with unique formula credit/2+1, effective cap min(...), min capping, priority order, so only one output correct.
- **BAD_GOLDEN:** Efficient RR bulk cycles and overflow-safe mulDiv via math/bits handles 1e12 and 1e24/1.2e19 overflow cases in <0.1s.
- **BAD_GRADING_WEAK / R06:** Added large-weight overflow (1e12*1e12=1e24) and large-credit overflow (3*4e18=1.2e19) plus large 1e12, rate limiting test, plus all previous corners.

## Output Ambiguity - Fixed to Minor
Format precise: T lines, S CSV no spaces, empty for S==0. Residual numeric ambiguity resolved by explicit credit/2+1 and examples including large-weight overflow.

## Test Quality - Fixed and Harder

- **31 tests total** (balanced, not 60 too hard nor 25 too easy): 20 parametrized hierarchical multi-batch with rate limits covering group caps, effective caps min(group rem, sum member eff, rate), min capping min(min,cap,rate,rem), priority ordering, multi-round, multi-batch credit persistence, large 1e12, large-weight 1e24, large-credit 1.2e19, rate limiting + conservation + 8 corners: min>cap, group no members, invalid gid, blank lines/spaces, large 1e12, large weight overflow 1e24, large credit overflow 1.2e19, rate limiting, zero caps, deterministic. All previously flagged missing tests present, README now correctly 31 (not 60), reward path fixed for set -e, verifier offline via Dockerfile preinstall pytest, efficient golden.
- **Implicit robustness now explicit:** blank lines/spaces, invalid gid→0, group no members eff0, min>cap capped, priority tie deterministic, zero caps, large numbers, rate limiting 0=unlimited per batch, deterministic, credit never negative - all explicitly documented.
- **Harder via rate limiting:** Adds extra per-batch capping layer at both group and subscriber levels, plus sum member effective caps must include rate limits. 20 main cases include rate 0 (unlimited) and non-zero rates, plus dedicated test_rate_limiting.

## Completion Rates (online validation — commit 4104fa0, 2026-07-22)

- Oracle: **3/3** — validated
- Opus 4.8 (agent): **5/5** — failed (solved every trial → too easy for this model)
- GPT-5.5 (codex): **5/5** — failed (too easy for this model)
- Avocado (metacode): **1/5** — validated (the 4 losses were infra errors, not test failures)
- avgReward **0.76**, validation passing.

## Failure Analysis (latest run)

Derived from downloaded trial CTRF artifacts. In the latest validation, **no model produced a genuine test failure** — every completed trial passed, and every loss was `status=error` (Daytona `ThrottlerException: Too Many Requests` / build-harness failures that score 0).

- **Opus 4.8 (agent) — 5/5 clean passes.** Too easy for this model.
- **GPT-5.5 (codex) — 5/5 clean passes.** Too easy for this model.
- **Avocado (metacode) — clean trials all pass.** Across both recent jobs every completed trial passed; all losses were `status=error` infra flakes. No real test failures.
- **Oracle — 3/3.**

**Assessment:** once infrastructure noise is removed, all three models solve the task on every clean trial — it is currently **too easy** (both frontier models 5/5, and Avocado passes every completed trial). The avgReward 0.76 is held up by the 5/5s; Avocado's sub-5/5 headline is provisioning instability, not reasoning difficulty. To reach the 20-80% sweet spot the allocator needs genuine hardening (e.g. deeper credit-decay interaction across more batches, tighter overflow-only paths, or additional adversarial rate/min/priority corner combinations) rather than relying on flaky infra to suppress pass rates.

## Anti-Cheating

- Tests cover hierarchical effective caps with rate limits, min>cap, priority tie, empty groups, invalid gid, blank lines, 1e12, 1e24 overflow, 1.2e19 overflow, rate limiting, zero caps, conservation, deterministic, plus 20 random main cases. Not hardcodeable.
- No network during grading, pinned toolchain, overflow-safe.
