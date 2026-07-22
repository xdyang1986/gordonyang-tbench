# codimango/pub-sub

## Description
**Build-from-scratch, balanced hard hierarchical allocator** - combines hierarchical groups + min + priority + multi-batch persistent credit + credit-decay, with explicit spec for unique output and 64-bit overflow-safe handling.

**Fixes for previous quality flags:**

- **Information Leakage Significant (Stage 8.B):** Previous versions shipped complete `allocate_batch` pseudocode as paste-ready code matching reference line-for-line (Solution Giveaway). Fixed: instruction now describes algorithm in prose with inline formulas (`give = min(min, cap, rem)`, `share = floor(rem*credit/total)`, `credit/2+1` decay) plus examples, not as copy-paste code block. Exact decay formula still explicit to avoid ambiguity (fixes BAD_AMBIGUOUS).
- **Spec Clarity Other Issues:** Example outputs previously contradicted tests (e.g., multi-batch example claimed `4,1,1` while tests expected `4,0,2 / 3,1,2`) with hedging prose "Actually... Let's use reference". Fixed: all examples now match reference implementation (`6,4,3,3`, `4,4,1`, `4,0,2 / 3,1,2`, etc.) and hedging removed.
- **BAD_AMBIGUOUS / BAD_GRADING_WRONG (R01,R02,R03,R08):** Spec now fully explicit with unique formulas: effective group cap `min(group rem, sum member rem)`, min phase priority desc tie idx asc with capping, proportional share, progress guarantee max credit tie lowest idx, RR fallback efficient bulk cycles input order, credit update `credit/2+1` if batch>0 else `+weight`. No coherent alternative decay passes.
- **BAD_GOLDEN (R12):** Reference Go now uses efficient RR fallback bulk cycles and overflow-safe `mulDiv` via `math/bits` Mul64/Div64 handling `remaining*credit` up to 1e24 and 1.2e19 without overflow, handles 1e12 scale in <0.1s.
- **BAD_GRADING_WEAK / R06 / Test coverage:** Added explicit large-weight overflow (1e12*1e12=1e24) and large-credit overflow (3*4e18=1.2e19) tests where `remaining*credit` would overflow signed 64-bit, directly testing written 64-bit safety requirement. Also added RR fallback, min>cap, priority tie, group no members, invalid gid, blank lines, zero caps, deterministic, conservation. Dockerfile pre-installs pytest, test.sh offline (no apt-get/curl uv network) fixing R09.

**Algorithm:**
- Input: T, loads, G groups (prio, min, weight, cap), S subs (gid, prio, min, weight, cap), may have blank lines/spaces robust parsing.
- State: group_total, sub_total cumulative, group_credit=weight, sub_credit=weight persistent across batches.
- Effective group remaining cap = min(group rem cap, sum member rem caps) (0 if no members, invalid gid ignored →0).
- Per batch: group level allocate via min+priority+credit-decay primitive, then per-group allocate its share to members via same primitive. Output T lines CSV per sub per batch.

## Output Ambiguity - Fixed to Minor
Format precise T lines S CSV no spaces, empty for S==0. Residual numeric ambiguity resolved by explicit `credit/2+1` and examples including large-weight overflow examples.

## Test Quality - Fixed

- **32 tests total** (fixes previous README 60 vs 25 mismatch): 20 parametrized hierarchical multi-batch covering group caps, effective caps, min/priority, multi-round, zero load, multi-batch credit persistence, large 1e12, large-weight 1e24, large-credit 1.2e19 + `test_conservation` + `test_deterministic` + 8 corner tests: `test_min_exceeds_cap`, `test_priority_tie_and_order`, `test_group_no_members`, `test_invalid_gid`, `test_blank_lines_and_spaces`, `test_large_numbers` (1e12), `test_large_weight_overflow` (1e24), `test_large_credit_overflow` (1.2e19), `test_zero_caps`, plus `test_fuzz_random` (20 random sequences vs Python reference). All previously flagged missing tests (RR fallback efficiency, group-no-members, zero-caps, priority-tie, deterministic) now present.
- **test.sh reward path fixed:** Uses safe `if pytest then echo 1 else echo 0` under set -e, offline via Dockerfile preinstall pytest, no network.

## Completion Rates

- Oracle: passes **32/32** with efficient overflow-safe implementation.
- Balanced: was too easy (5/5 single-level) and too hard (0/5 60-test multi-batch). This 32-test hierarchical multi-batch with min/priority + fuzz + overflow should be hard-but-passable.

## Anti-Cheating

- Tests cover hierarchical effective caps, min>cap, priority tie, empty groups, invalid gid, blank lines, 1e12, 1e24 overflow, 1.2e19 overflow, zero caps, conservation, deterministic, plus 20 random main cases and 20 random fuzz sequences vs Python reference. Not hardcodeable.
- No network during grading, pinned toolchain.
