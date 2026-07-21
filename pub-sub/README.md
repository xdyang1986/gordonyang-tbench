# codimango/pub-sub

## Description
**Build-from-scratch, ultimate complex** - combines hierarchical groups + min guarantees + priority + multi-batch persistent credit, with **implicit edge handling** that is not fully spelled out.

- **Hierarchical:** G groups (prio, min, weight, cap), S subs (gid, prio, min, weight, cap). Effective remaining group cap is limited by sum of its members' remaining caps (if group has no members, effective 0). This is implied, not given as formula.
- **Min + Priority:** 2-phase allocation at both levels. Min phase must respect caps and remaining load, and when load insufficient, higher priority gets min first. What if min > cap? Sensible capping is expected but not spelled out. Priority tie-breaking deterministic by input order - also implicit.
- **Credit-decay weighted:** Primitive for weighted phase after min phase. Credit starts = weight, persists across batches. Multi-round proportional `rem*credit/total` capped, progress guarantee highest-credit (tie lowest index), RR fallback in input order when total credit 0. Decay is halving plus small constant to avoid zero (exact formula must be deduced from examples: `credit/2+1` vs `+weight`). This is partially implicit.
- **Multi-batch persistent state:** T batches, loads. Credits and cumulative totals persist. Remaining caps shrink. Output T lines CSV per sub per batch.
- **Implicit robustness:** Input may contain blank lines and extra spaces (must parse robustly), gid may be out of range (must be ignored with 0 alloc), group may have no members (effective 0), min may exceed cap or load (must be capped and priority-ordered), large numbers up to 1e12 must not overflow (64-bit), zero caps/loads/mins, credit never negative, deterministic tie-breaking. These are **not fully spelled out** in instruction - tests will check them.

This shape is intentionally **under-specified on edge handling** to force agents to handle corner cases sensibly, not just transcribe pseudocode. Previous fully-specified version was too easy; now 8 additional corner-case tests enforce implicit requirements.

## Completion Rates

Latest **online** validation run (commit `72c1ddc`, multimango.com) — **Validation status: passing** on the numeric gates, but the agentic task-quality reviewer returned **`BAD_GRADING_WRONG`** (see caveat below).

| Agent | Model | Attempts | Passed | Mean reward |
|---|---|---|---|---|
| Oracle | oracle | 3 | 3/3 | 1.000 |
| Metacode (gate) | meta/avocado-5.14-code | 5 | 4/5 | 0.800 |
| Claude-code | claude-opus-4-8 | 5 | 5/5 | 1.000 |
| Codex | gpt-5.5 | 5 | 5/5 | 1.000 |

Structural: 6/6 files present, all checks PASS. Contamination v2: MEDIUM (NOT_FOUND in internal decontamination table, no public instance found).

Local pytest: 30 parametrized hierarchical multi-batch cases (fixed examples + 25 random) + conservation + deterministic + 8 implicit corner case tests (min>cap, priority tie/order, group no members, invalid gid, blank lines/spaces, large numbers 1e12, RR fallback multi-batch, zero caps) = **40 tests**; oracle passes 40/40.

### ⚠ Agentic-review verdict: `BAD_GRADING_WRONG` (task quality flagged)

Although the numeric gates pass, the online task-quality reviewer rated the task **`BAD_GRADING_WRONG`** (difficulty: MIXED, 14/15 non-oracle attempts passed) and failed rubrics R01, R02, R03, R06, R08, R09, R12. This is a **quality flag on the current design, not a pass/fail infra issue**:

- **R08 Accepts alternatives / R02 Spec-test alignment (primary):** exact-output tests over-pin the golden's unstated `credit/2+1` decay formula. The single metacode failure (`pub-sub__5MuhieU`) was a **false negative** — it implemented a coherent alternative decay (`(credit+weight)/2`) that satisfies the examples but mismatched hidden exact outputs. So the 4/5 is ambiguity-driven, not a clean capability discriminator.
- **R01/R03 `BAD_AMBIGUOUS`:** the credit-decay formula is implicit and not uniquely determined by the three examples.
- **R06/R09 `BAD_GRADING_WEAK`:** zero-credit RR fallback is never forced under valid inputs; 1e12 high-weight scale is untested; `tests/test.sh` runs `apt-get` and downloads `uv` from the network during grading (external dependency).
- **R12 `BAD_GOLDEN`:** the reference uses `rem*credit/total` int math and an O(load) path — it times out (>5s) at the spec-stated 1e12 scale.

**Author action items (from reviewer, not yet applied):** state the exact credit-update formulas (temporary + persistent) and the zero-credit fallback in `instruction.md`; replace exact-output random cases with property checks for underspecified behavior; make the verifier self-contained (preinstall pytest/pytest-json-ctrf, drop network `uv` install); switch the golden to 64-bit-safe arithmetic + non-O(load) fallback and add 1e12-scale tests. These figures are from the last completed online run; **no revalidation was triggered for this README update.**

## Anti-Cheating Analysis
- **Hardcoded outputs:** Tests drive binary on many random (T, groups, subs) combos plus explicit corner cases, assert exact per-batch CSVs from reference Python plus invariants (per-sub cap, per-group effective cap, conservation). Not hardcodeable.
- **Overfitting:** Tests hidden at solve time include random multi-batch and all 8 implicit edge cases beyond 3 instruction examples. An implementation that only handles happy path but not min>cap, invalid gid, blank lines, or RR fallback will fail.
- **Implicit requirements:** 8 tests specifically target edge handling that is not fully described in instruction (e.g., min capped to cap, priority ordering when load < sum mins, group no members → 0, invalid gid → 0, blank lines/spaces robust parsing, 1e12 handling, RR fallback determinism). Agent must infer sensible handling.
- **Modifying test files:** Dockerfile does not copy tests; harness injects after agent run.
- **Bypassing path:** Grade builds and runs `/app`; only correct full implementation passes.
- **Pinned toolchain:** `GOTOOLCHAIN=local`.
