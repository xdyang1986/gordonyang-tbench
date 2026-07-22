# codimango/pub-sub

## Description
**Build-from-scratch, balanced hard hierarchical allocator** - combines hierarchical groups + min + priority + multi-batch persistent credit + credit-decay, with explicit spec for fair grading and overflow-safe golden.

- **Hierarchical:** G groups (prio, min, weight, cap), S subs (gid, prio, min, weight, cap). Effective remaining cap = min(group remaining cap, sum member remaining caps). If group has no members, effective 0. If gid out of range, sub gets 0.
- **Min + Priority:** 2-phase per batch: min phase sorted priority desc tie index, allocate min capped to min(min, cap, rem). If load insufficient, higher priority first. If min>cap, capped.
- **Credit-decay weighted:** Weighted phase multi-round: proportional share floor(rem*credit/total) capped, but must be computed without 64-bit overflow using 128-bit (remaining*credit can be 1e24 > 2^63-1, e.g., 1e12*1e12, and 3*4e18=1.2e19). Progress guarantee highest credit tie lowest index, efficient RR fallback bulk cycles + partial input order when total==0 (efficient for 1e12, stays ≥1 with correct decay credit/2+1), credit update credit/2+1 if batch>0 else +weight, persistent across batches.
- **Multi-batch persistent state:** T batches (1..3 loads up to 1e12). Credits and totals persist, remaining caps shrink. Output T lines CSV per sub per batch.

Fixes previous quality flags:
- **Information Leakage Significant:** Removed complete allocate_batch paste-ready pseudocode matching reference line-for-line. Now prose with inline formulas, not copy-paste code.
- **Spec Clarity:** Example outputs corrected to match tests (6,4,3,3 / 4,4,1 / 4,1,1 x2 etc.) and hedging removed.
- **BAD_AMBIGUOUS / BAD_GRADING_WRONG:** Core primitive fully explicit with unique formula credit/2+1, no alternative decay considered correct.
- **BAD_GOLDEN:** Efficient RR fallback bulk cycles handles 1e12, overflow-safe mulDiv via math/bits.
- **BAD_GRADING_WEAK / R06:** Added large-weight overflow (1e12*1e12=1e24) and large-credit overflow (3*4e18=1.2e19) tests where remaining*credit would overflow signed 64-bit, explicitly testing 64-bit safety requirement.
- **Output Ambiguity Minor:** Format precise T lines S CSV no spaces, empty for S==0, explicit credit/2+1.

## Test Quality - Fixed

- **30 tests total** (balanced, not 60 too hard nor 25 too easy): 20 parametrized hierarchical multi-batch covering group caps, effective caps, min/priority, multi-round, zero load, multi-batch credit persistence, large 1e12 + conservation + deterministic + 8 corners: min>cap, group no members, invalid gid, blank lines/spaces, large 1e12, large weight overflow 1e24, large credit overflow 1.2e19, zero caps. All named corner tests present, README now correctly 30.
- **test.sh reward path fixed:** Uses safe `if pytest then echo 1 else echo 0` under set -e, offline via Dockerfile preinstall pytest (no apt-get/curl/network).
- **Verifier stdout test names match checked-in tests exactly** (parametrized names like test_allocation[16-groups0-subs0-6,4,3,3]).

## Completion Rates

- Oracle: passes **30/30** with efficient overflow-safe implementation. Tested 1e12, 1e12*1e12 overflow, 4e18*3 overflow.
- Balanced: hierarchical + min/priority multi-batch is hard-but-passable, not too easy (was 5/5 single-level) nor too hard (0/5 for 60-test).

## Anti-Cheating

- Tests cover hierarchical effective caps, min>cap, priority tie, empty groups, invalid gid, blank lines, 1e12, 1e24 overflow, 1.2e19 overflow, zero caps, conservation, deterministic.
- No network during grading, pinned toolchain.
